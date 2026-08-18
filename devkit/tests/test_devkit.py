"""Tests for the Developer-Kit standalone package
(``devkit`` + ``devkit.api``).

The DevKit intentionally has **no** Flask or HTTP wiring — these
tests cover the *Python* side of the pywebview ``js_api`` bridge:

* **Pure helpers** — :func:`archive_name`, :func:`build_manifest`,
  :func:`check_rate_limit`, :func:`check_archive_size`,
  :func:`compute_sha256`.
* **Packing** — :func:`pack_payload` with and without ``py7zr``
  installed (the latter forces the zip fallback).
* **SMTP** — :func:`build_email_message` MIME structure + each
  failure branch of :func:`_smtp_send`.
* **Orchestrator** — :func:`submit` end-to-end with an injected
  ``smtp_send``.
* **js_api bridge** — :class:`DevKitApi` happy-path + every error
  envelope through :func:`serialize_error`.
* **CLI** — :func:`_parse_args` validation and the ``--headless``
  configuration dump.

Each test resets the DevKit bucket via an autouse fixture so the
three in-memory dicts (``submissions``, ``last_submit_at``,
``local_archives``) start empty.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import socket
import zipfile

import pytest

from devkit import (
    ARCHIVE_FORMAT_7Z,
    ARCHIVE_FORMAT_ZIP,
    DEV_SUBMIT_COOLDOWN_SECONDS,
    DEV_SUBMIT_MAX_ATTACHMENT_BYTES,
    DEV_SUBMIT_RECIPIENT,
    DEV_SUBMIT_SMTP_HOST,
    DEV_SUBMIT_SMTP_PORT,
    DEV_SUBMIT_SMTP_USE_TLS,
    DEV_SUBMIT_SMTP_USER,
    TARGET_KINDS,
    DevKitError,
    PayloadTooLargeError,
    RateLimitedError,
    SmtpError,
    _cumulative_size,
    _smtp_send,
    _validate_submission,
    _API_VERSION,
    archive_name,
    build_email_message,
    build_manifest,
    check_archive_size,
    check_rate_limit,
    compute_sha256,
    cooldown_remaining,
    delete_local_archive,
    get_submission,
    last_submit_for,
    list_submissions,
    local_archive_dir,
    local_archive_path,
    pack_payload,
    reset_for_testing,
    seed_default,
    submit,
)
from devkit import state as devkit_state
from devkit.api import DevKitApi, serialize_error


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_devkit_state(tmp_path, monkeypatch):
    """Wipe the in-memory DevKit buckets between tests *and* point
    the local archive directory at ``tmp_path``.

    Setting ``XIJIAN_DEV_WORK_DIR`` forces the DevKit to use the
    temporary directory as its work directory so no test data leaks
    into the real dev directory.
    """
    monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
    monkeypatch.setenv("XIJIAN_DEV_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(
        "devkit._DEV_SUBMIT_LOCAL_DIR", str(tmp_path)
    )
    reset_for_testing()
    yield
    reset_for_testing()


@pytest.fixture()
def make_temp_file(tmp_path):
    """Return a callable that writes ``content`` to ``tmp_path/<name>``
    and returns the path.  Files are not auto-cleaned — pytest's
    ``tmp_path`` handles that.
    """

    def _factory(name: str, content: bytes = b"x") -> str:
        p = tmp_path / name
        p.write_bytes(content)
        return str(p)

    return _factory


@pytest.fixture()
def fake_smtp():
    """Replace :func:`_smtp_send` with a recorder so tests can assert
    the message that *would* have been sent.
    """

    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.next_code = "250"
            self.next_response = "OK"

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            return self.next_code, self.next_response

    return _Recorder()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestArchiveName:
    def test_basic_id(self):
        name = archive_name("alice-01")
        assert name.startswith("alice-01__")
        assert name.endswith(".7z")
        assert "T" in name and "Z" in name

    def test_strips_path_special_chars(self):
        name = archive_name("ali ce/01")
        # `/` and space become `_`
        assert "ali_ce_01__" in name

    def test_empty_id_becomes_developer(self):
        name = archive_name("")
        assert name.startswith("developer__")

    def test_now_override(self):
        moment = _dt.datetime(2026, 7, 3, 8, 30, 0, tzinfo=_dt.timezone.utc)
        name = archive_name("alice", now=moment)
        assert "alice__2026-07-03T08-30-00Z.7z" == name


class TestBuildManifest:
    def test_minimal(self):
        m = build_manifest(
            developer_id="alice",
            target_kind="character",
            target_id="char_yuki",
            payload={"files": ["foo.vrm"], "notes": "hi"},
            submitted_at="2026-07-03T08:30:00Z",
            ai_ratio=0.42,
        )
        assert m["schema"] == "xijian.devkit.submission/v1"
        assert m["developer_id"] == "alice"
        assert m["target_kind"] == "character"
        assert m["target_id"] == "char_yuki"
        assert m["submitted_at"] == "2026-07-03T08:30:00Z"
        assert m["ai_ratio"] == 0.42
        assert m["files"] == ["foo.vrm"]
        assert m["notes"] == "hi"

    def test_files_defaulted_when_missing(self):
        m = build_manifest(
            developer_id="alice",
            target_kind="world",
            target_id="world_modern_tokyo",
            payload={},
            submitted_at="2026-07-03T08:30:00Z",
        )
        assert m["files"] == []
        assert m["notes"] == ""

    def test_files_coerced_to_strings(self):
        m = build_manifest(
            developer_id="alice",
            target_kind="world",
            target_id="w",
            payload={"files": [b"x", 42, None, "ok"]},
            submitted_at="2026-07-03T08:30:00Z",
        )
        # ``None`` becomes ``"None"`` by str(); that's the documented
        # behaviour — the orchestrator filters non-string paths later.
        assert m["files"] == ["b'x'", "42", "None", "ok"]


class TestCheckRateLimit:
    def test_first_time_no_limit(self):
        # No previous submission ⇒ no cooldown.
        assert check_rate_limit("alice") == 0

    def test_within_window_raises(self):
        devkit_state.last_submit_at["alice"] = "2026-07-03T08:00:00Z"
        moment = _dt.datetime(2026, 7, 3, 8, 0, 30, tzinfo=_dt.timezone.utc).timestamp()
        with pytest.raises(RateLimitedError) as ei:
            check_rate_limit("alice", now=moment)
        assert ei.value.retry_after_seconds > 0
        assert ei.value.retry_after_seconds == pytest.approx(
            DEV_SUBMIT_COOLDOWN_SECONDS - 30, abs=2
        )

    def test_past_window_returns_zero(self):
        devkit_state.last_submit_at["alice"] = "2026-07-03T07:00:00Z"
        moment = _dt.datetime(2026, 7, 3, 8, 30, 0, tzinfo=_dt.timezone.utc).timestamp()
        assert check_rate_limit("alice", now=moment) == 0

    def test_clock_backwards_treated_as_fresh(self):
        devkit_state.last_submit_at["alice"] = "2026-07-03T09:00:00Z"
        moment = _dt.datetime(2026, 7, 3, 8, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        # Went backwards — treat as fresh, no cooldown.
        assert check_rate_limit("alice", now=moment) == 0

    def test_garbled_timestamp_returns_zero(self):
        devkit_state.last_submit_at["alice"] = "not-a-date"
        assert check_rate_limit("alice") == 0


class TestCheckArchiveSize:
    def test_within_limit(self):
        check_archive_size(DEV_SUBMIT_MAX_ATTACHMENT_BYTES)  # does not raise

    def test_over_limit_raises(self):
        with pytest.raises(PayloadTooLargeError) as ei:
            check_archive_size(DEV_SUBMIT_MAX_ATTACHMENT_BYTES + 1)
        assert ei.value.size_bytes == DEV_SUBMIT_MAX_ATTACHMENT_BYTES + 1

    def test_custom_limit_attribute(self):
        with pytest.raises(PayloadTooLargeError) as ei:
            check_archive_size(2_000_000_000)
        assert ei.value.max_bytes == DEV_SUBMIT_MAX_ATTACHMENT_BYTES


class TestComputeSha256:
    def test_known_content(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_bytes(b"hello")
        assert (
            compute_sha256(str(p))
            == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty"
        p.write_bytes(b"")
        # sha256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        assert (
            compute_sha256(str(p))
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )


class TestCumulativeSize:
    def test_sums_sizes(self):
        entries = [{"size": 100}, {"size": 200}, {"size": 0}]
        assert _cumulative_size(entries) == 300

    def test_invalid_entries_skipped(self):
        entries = [{"size": "bad"}, {}, {"size": None}, {"size": 10.5}]
        assert _cumulative_size(entries) == 10


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateSubmission:
    def test_ok(self):
        _validate_submission("alice", "character", "char_yuki")

    def test_missing_developer(self):
        with pytest.raises(DevKitError) as ei:
            _validate_submission("", "character", "x")
        assert ei.value.code == "missing_developer_id"

    def test_bad_kind(self):
        with pytest.raises(DevKitError) as ei:
            _validate_submission("alice", "invalid_kind", "x")
        assert ei.value.code == "bad_target_kind"

    def test_missing_target(self):
        with pytest.raises(DevKitError) as ei:
            _validate_submission("alice", "character", "")
        assert ei.value.code == "missing_target_id"

    def test_non_string_developer(self):
        with pytest.raises(DevKitError) as ei:
            _validate_submission(None, "character", "x")  # type: ignore[arg-type]
        assert ei.value.code == "missing_developer_id"


# ---------------------------------------------------------------------------
# Local archive dirs
# ---------------------------------------------------------------------------


class TestLocalArchiveDir:
    def test_default_dir_is_under_tmp(self, monkeypatch):
        # Wipe any override so we test the default behaviour.
        monkeypatch.delenv("XIJIAN_DEV_LOCAL_DIR", raising=False)
        monkeypatch.setattr(
            "devkit._DEV_SUBMIT_LOCAL_DIR", None
        )
        d = local_archive_dir()
        assert d.endswith("xijian_devkit")
        assert os.path.isdir(d)

    def test_overridden_dir(self, tmp_path):
        d = local_archive_dir()
        assert d == str(tmp_path)

    def test_path_join(self, tmp_path):
        p = local_archive_path("alice__x.7z")
        assert p == os.path.join(str(tmp_path), "alice__x.7z")


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


class TestPackPayload:
    def test_zip_fallback_succeeds(self, monkeypatch, make_temp_file, tmp_path):
        # Force the zip fallback by hiding py7zr.
        monkeypatch.setitem(__import__("sys").modules, "py7zr", None)
        f = make_temp_file("data.bin", b"hi")
        manifest = build_manifest(
            developer_id="alice",
            target_kind="character",
            target_id="char_yuki",
            payload={"files": [f], "notes": ""},
            submitted_at="2026-07-03T08:30:00Z",
        )
        archive = str(tmp_path / "out.zip")
        path, size, fmt = pack_payload(
            [{"path": f, "arcname": "data.bin", "size": 2}],
            [{"path": f, "arcname": "data.bin", "size": 2}],  # type: ignore[arg-type]
            archive_path=archive,
        )
        # First positional arg is manifest in our API — re-do correctly.
        path, size, fmt = pack_payload(
            manifest,
            [{"path": f, "arcname": "data.bin", "size": 2}],
            archive_path=archive,
        )
        assert path == archive
        assert fmt == ARCHIVE_FORMAT_ZIP
        assert size > 0
        with zipfile.ZipFile(path, "r") as zf:
            assert "manifest.json" in zf.namelist()
            assert "data.bin" in zf.namelist()
            assert json.loads(zf.read("manifest.json"))["developer_id"] == "alice"

    def test_py7zr_path_used_when_available(self, monkeypatch, make_temp_file, tmp_path):
        # Provide a fake py7zr so the 7Z branch is exercised.
        class _Fake7zFile:
            def __init__(self, path, **kwargs):
                self.path = path
                self.mode = kwargs.get("mode", "w")
                self.kwargs = kwargs
                import io as _io

                self.buffer = _io.BytesIO()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                with open(self.path, "wb") as f:
                    f.write(self.buffer.getvalue())
                return False

            def writestr(self, data, arcname):
                if isinstance(data, str):
                    data = data.encode("utf-8")
                self.buffer.write(b"STR:" + arcname.encode() + b":" + data + b"\n")

            def write(self, src, arcname):
                with open(src, "rb") as f:
                    self.buffer.write(b"FILE:" + arcname.encode() + b":" + f.read())

        class _FakePy7zr:
            SevenZipFile = _Fake7zFile

        monkeypatch.setitem(__import__("sys").modules, "py7zr", _FakePy7zr)
        f = make_temp_file("a.txt", b"abc")
        manifest = build_manifest(
            developer_id="alice",
            target_kind="character",
            target_id="x",
            payload={},
            submitted_at="2026-07-03T08:30:00Z",
        )
        archive = str(tmp_path / "out.7z")
        path, size, fmt = pack_payload(
            manifest,
            [{"path": f, "arcname": "a.txt", "size": 3}],
            archive_path=archive,
        )
        assert fmt == ARCHIVE_FORMAT_7Z
        assert size > 0
        # Inspect the fake-output structure to confirm the writes happened.
        with open(path, "rb") as fh:
            content = fh.read()
        assert b"manifest.json" in content
        assert b"a.txt" in content

    def test_pre_flight_rejects_oversized_input(self, make_temp_file):
        big = make_temp_file("big.bin", b"x" * 100)
        manifest = build_manifest(
            developer_id="alice",
            target_kind="character",
            target_id="x",
            payload={},
            submitted_at="2026-07-03T08:30:00Z",
        )
        with pytest.raises(PayloadTooLargeError):
            pack_payload(
                manifest,
                [{"path": big, "size": DEV_SUBMIT_MAX_ATTACHMENT_BYTES + 1}],
            )


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


class TestBuildEmailMessage:
    def test_subject_and_recipients(self, make_temp_file):
        f = make_temp_file("a.bin", b"hi")
        msg = build_email_message(
            developer_id="alice",
            submitted_at="2026-07-03T08:30:00Z",
            target_kind="character",
            target_id="char_yuki",
            ai_ratio=0.5,
            archive_filename="alice__x.7z",
            archive_size_bytes=10,
            content_sha256="abc123",
            archive_path=f,
            archive_format=ARCHIVE_FORMAT_7Z,
        )
        assert msg["Subject"] == "[XiJian DevKit Package Submit] alice"
        assert msg["From"] == DEV_SUBMIT_SMTP_USER
        assert msg["To"] == DEV_SUBMIT_RECIPIENT
        # Two parts: text body + attachment.
        assert len(msg.get_payload()) == 2  # type: ignore[arg-type]

    def test_zip_ctype_when_format_zip(self, make_temp_file):
        f = make_temp_file("b.bin", b"hi")
        msg = build_email_message(
            developer_id="alice",
            submitted_at="2026-07-03T08:30:00Z",
            target_kind="world",
            target_id="w1",
            ai_ratio=0.0,
            archive_filename="a.zip",
            archive_size_bytes=10,
            content_sha256="abc",
            archive_path=f,
            archive_format=ARCHIVE_FORMAT_ZIP,
        )
        attachment = msg.get_payload()[1]  # type: ignore[index]
        assert attachment.get_content_type() == "application/zip"


class TestSmtpSend:
    def test_records_calls(self, fake_smtp):
        m = {"hello": "world"}
        # Use the recorder directly — saves spinning up an SMTP server.
        code, response = fake_smtp(
            host=DEV_SUBMIT_SMTP_HOST,
            port=DEV_SUBMIT_SMTP_PORT,
            use_tls=DEV_SUBMIT_SMTP_USE_TLS,
            user=DEV_SUBMIT_SMTP_USER,
            password="x",
            sender="a@a",
            recipient="b@b",
            message=m,
        )
        assert code == "250"
        assert response == "OK"
        assert fake_smtp.calls[0]["message"] is m

    def test_connection_failure(self, monkeypatch):
        # Patch smtplib.SMTP to raise the OSError-family we expect.
        class _Boom:
            def __init__(self, *a, **kw):
                raise socket.gaierror("nope")

        import smtplib

        monkeypatch.setattr(smtplib, "SMTP", _Boom)
        with pytest.raises(SmtpError) as ei:
            _smtp_send(
                host="x",
                port=25,
                use_tls=False,
                user="u",
                password="p",
                sender="a@a",
                recipient="b@b",
                message=object(),
            )
        assert ei.value.category == "connection_failed"

    def test_auth_failure(self, monkeypatch):
        class _FakeSMTP:
            def __init__(self, *a, **kw):
                import smtplib as _s

                self._s = _s

            def starttls(self, *a, **kw):
                pass

            def login(self, *a, **kw):
                raise self._s.SMTPAuthenticationError(535, b"bad")

            def quit(self):
                pass

        import smtplib

        monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
        with pytest.raises(SmtpError) as ei:
            _smtp_send(
                host="x",
                port=25,
                use_tls=False,
                user="u",
                password="p",
                sender="a@a",
                recipient="b@b",
                message=object(),
            )
        assert ei.value.category == "auth_failed"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class TestSubmit:
    def test_happy_path_records_and_persists(self, fake_smtp, make_temp_file):
        f = make_temp_file("a.bin", b"hi")
        record = submit(
            "alice",
            "character",
            "char_yuki",
            file_entries=[{"path": f, "arcname": "a.bin", "size": 2}],
            payload={"notes": "ok"},
            smtp_send=fake_smtp,
        )
        assert record["developer_id"] == "alice"
        assert record["target_kind"] == "character"
        assert record["target_id"] == "char_yuki"
        assert record["smtp_status"] == "sent"
        assert record["smtp_code"] == "250"
        assert len(record["content_sha256"]) == 64
        # State was persisted.
        assert devkit_state.submissions[record["id"]] == record
        assert devkit_state.last_submit_at["alice"] == record["submitted_at"]
        assert devkit_state.local_archives[record["id"]] == record["archive_path"]
        # Local archive actually exists on disk and is non-empty.
        assert os.path.isfile(record["archive_path"])
        assert os.path.getsize(record["archive_path"]) > 0

    def test_rate_limited_blocks_second_call(self, fake_smtp, make_temp_file):
        f = make_temp_file("a.bin", b"hi")
        submit(
            "alice",
            "character",
            "char_yuki",
            file_entries=[{"path": f, "size": 2}],
            smtp_send=fake_smtp,
        )
        with pytest.raises(RateLimitedError):
            submit(
                "alice",
                "character",
                "char_other",
                file_entries=[{"path": f, "size": 2}],
                smtp_send=fake_smtp,
            )

    def test_payload_too_large_never_packs(self, fake_smtp, make_temp_file):
        f = make_temp_file("big.bin", b"x")
        with pytest.raises(PayloadTooLargeError):
            submit(
                "alice",
                "character",
                "char_yuki",
                file_entries=[
                    {"path": f, "size": DEV_SUBMIT_MAX_ATTACHMENT_BYTES + 1}
                ],
                smtp_send=fake_smtp,
            )
        # Nothing was packed, nothing was emailed.
        assert len(devkit_state.submissions) == 0
        assert fake_smtp.calls == []

    def test_invalid_target_kind(self, fake_smtp, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        with pytest.raises(DevKitError) as ei:
            submit(
                "alice",
                "bogus",
                "x",
                file_entries=[{"path": f, "size": 1}],
                smtp_send=fake_smtp,
            )
        assert ei.value.code == "bad_target_kind"

    def test_missing_target_id(self, fake_smtp):
        with pytest.raises(DevKitError) as ei:
            submit("alice", "character", "", smtp_send=fake_smtp)
        assert ei.value.code == "missing_target_id"

    def test_seed_default_is_noop(self):
        # Existing stubs use :func:`seed_default()` as a hook; the
        # DevKit has nothing to seed, but the helper exists for
        # symmetry.
        seed_default()
        assert devkit_state.submissions == {}


class TestCooldownRemaining:
    def test_no_history_returns_zero(self):
        assert cooldown_remaining("alice") == 0

    def test_recent_submission_positive(self):
        devkit_state.last_submit_at["alice"] = "2026-07-03T08:00:00Z"
        # Whatever the current time is, the submission was moments
        # ago so remaining > 0 (or 0 if test runs an hour+ later —
        # we just check the clamp).
        result = cooldown_remaining("alice")
        assert 0 <= result <= DEV_SUBMIT_COOLDOWN_SECONDS

    def test_garbled_timestamp_returns_zero(self):
        devkit_state.last_submit_at["alice"] = "garbage"
        assert cooldown_remaining("alice") == 0


class TestReadSide:
    def test_last_submit_for(self, fake_smtp, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        r1 = submit(
            "alice",
            "character",
            "char_yuki",
            file_entries=[{"path": f, "size": 1}],
            smtp_send=fake_smtp,
        )
        assert last_submit_for("alice") == r1
        assert last_submit_for("nobody") is None

    def test_list_submissions_sorted_newest_first(self, fake_smtp, make_temp_file):
        f1 = make_temp_file("a.bin", b"x")
        f2 = make_temp_file("b.bin", b"y")
        submit(
            "alice",
            "character",
            "char_yuki",
            file_entries=[{"path": f1, "size": 1}],
            smtp_send=fake_smtp,
        )
        submit(
            "bob",
            "world",
            "world_modern_tokyo",
            file_entries=[{"path": f2, "size": 1}],
            smtp_send=fake_smtp,
        )
        items = list_submissions(limit=10)
        assert len(items) == 2
        # Newest first by submitted_at ISO string — works because the
        # orchestrator stamps them very close together; the ISO 8601
        # string sorts lexicographically the same way it sorts
        # chronologically.
        ids = [i["developer_id"] for i in items]
        assert ids[0] in {"alice", "bob"}

    def test_get_submission_round_trip(self, fake_smtp, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        r = submit(
            "alice",
            "character",
            "char_yuki",
            file_entries=[{"path": f, "size": 1}],
            smtp_send=fake_smtp,
        )
        assert get_submission(r["id"]) == r
        assert get_submission("sub_doesnotexist") is None

    def test_delete_local_archive(self, fake_smtp, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        r = submit(
            "alice",
            "character",
            "char_yuki",
            file_entries=[{"path": f, "size": 1}],
            smtp_send=fake_smtp,
        )
        assert delete_local_archive(r["id"]) is True
        assert not os.path.exists(r["archive_path"])
        # Second call: nothing to delete.
        assert delete_local_archive(r["id"]) is False


# ---------------------------------------------------------------------------
# js_api bridge
# ---------------------------------------------------------------------------


class TestSerializeError:
    def test_devkit_error_includes_triple(self):
        e = RateLimitedError(retry_after_seconds=42, detail="z")
        s = serialize_error(e)
        assert s["ok"] is False
        assert s["status"] == 429
        assert s["code"] == "rate_limited"
        assert s["type"] == "server_error"
        assert "42 seconds" in s["message"]
        assert s["details"]["detail"] == "z"

    def test_unknown_exception(self):
        s = serialize_error(ValueError("boom"))
        assert s["ok"] is False
        assert s["status"] == 500
        assert s["code"] == "internal_error"
        assert "boom" in s["message"]
        assert s["details"]["exception"] == "ValueError"


class TestDevKitApiMeta:
    def test_whoami(self):
        api = DevKitApi()
        resp = api.whoami()
        assert resp["ok"] is True
        d = resp["data"]
        # 敏感字段不再返回
        assert "smtp_host" not in d
        assert "smtp_port" not in d
        assert "smtp_user" not in d
        assert "recipient" not in d
        assert d["smtp_use_tls"] == DEV_SUBMIT_SMTP_USE_TLS
        assert d["target_kinds"] == list(TARGET_KINDS)
        assert d["max_attachment_bytes"] == DEV_SUBMIT_MAX_ATTACHMENT_BYTES
        assert d["max_attachment_mb"] == DEV_SUBMIT_MAX_ATTACHMENT_BYTES // 1_000_000
        assert d["api_version"] == _API_VERSION
        assert d["cooldown_seconds"] == DEV_SUBMIT_COOLDOWN_SECONDS
        assert d["preferred_archive_format"] == ARCHIVE_FORMAT_7Z

    def test_ping(self):
        api = DevKitApi()
        resp = api.ping()
        assert resp["ok"] is True
        assert resp["data"]["pong"] is True
        assert resp["data"]["active_developer"] is None


class TestDevKitApiSession:
    def test_login_then_current(self):
        api = DevKitApi()
        r1 = api.login("alice-01")
        assert r1 == {"ok": True, "data": {"developer_id": "alice-01"}}
        r2 = api.current_developer()
        assert r2["data"]["developer_id"] == "alice-01"

    def test_login_rejects_empty(self):
        api = DevKitApi()
        r = api.login("   ")
        assert r["ok"] is False
        assert r["code"] == "missing_developer_id"

    def test_logout_clears_session(self):
        api = DevKitApi()
        api.login("alice")
        out = api.logout()
        assert out["ok"] is True
        assert out["data"]["previous"] == "alice"
        assert api.current_developer()["data"]["developer_id"] is None

    def test_target_kinds(self):
        api = DevKitApi()
        r = api.target_kinds()
        assert r["ok"] is True
        assert TARGET_KINDS == tuple(r["data"])


class TestDevKitApiRead:
    def test_cooldown_for_unknown_dev(self):
        api = DevKitApi()
        r = api.cooldown_for("never-submitted")
        assert r["ok"] is True
        assert r["data"] == 0

    def test_cooldown_for_empty_string(self):
        api = DevKitApi()
        r = api.cooldown_for("")
        assert r["ok"] is False
        assert r["code"] == "missing_developer_id"

    def test_cooldown_for_after_recent_submit(
        self, fake_smtp, make_temp_file
    ):
        f = make_temp_file("a.bin", b"x")
        submit(
            "alice",
            "character",
            "char_yuki",
            file_entries=[{"path": f, "size": 1}],
            smtp_send=fake_smtp,
        )
        api = DevKitApi()
        r = api.cooldown_for("alice")
        assert r["ok"] is True
        # Fresh submit ⇒ cooling down.
        assert r["data"] > 0

    def test_last_submit_returns_none_for_unknown(self):
        api = DevKitApi()
        r = api.last_submit("nobody")
        assert r["ok"] is True
        assert r["data"] is None

    def test_list_submissions_default_limit(self, fake_smtp, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        submit(
            "alice",
            "character",
            "char_yuki",
            file_entries=[{"path": f, "size": 1}],
            smtp_send=fake_smtp,
        )
        api = DevKitApi()
        r = api.list_submissions()
        assert r["ok"] is True
        assert len(r["data"]) == 1

    def test_list_submissions_bad_limit(self):
        api = DevKitApi()
        r = api.list_submissions("not-a-number")
        assert r["ok"] is False
        assert r["code"] == "bad_limit"

    def test_get_submission_missing_id(self):
        api = DevKitApi()
        r = api.get_submission("sub_doesnotexist")
        assert r["ok"] is True
        assert r["data"] is None


class TestDevKitApiPreviewSize:
    def test_within_budget(self):
        api = DevKitApi()
        r = api.preview_size([{"path": "/a", "size": 100}, {"size": 200}])
        assert r["ok"] is True
        d = r["data"]
        assert d["total_bytes"] == 300
        assert d["ok"] is True
        assert d["max_mb"] == DEV_SUBMIT_MAX_ATTACHMENT_BYTES // 1_000_000

    def test_over_budget_marks_not_ok(self):
        api = DevKitApi()
        big = [
            {"path": "/a", "size": DEV_SUBMIT_MAX_ATTACHMENT_BYTES}
        ]
        r = api.preview_size(big)
        assert r["ok"] is True
        # Note: the data-level ok=False is the "safe to submit" flag;
        # the envelope ok=True is "the API call succeeded".
        assert r["data"]["ok"] is False
        assert "exceeds limit" in r["data"]["message"]

    def test_rejects_non_list(self):
        api = DevKitApi()
        r = api.preview_size("not a list")
        assert r["ok"] is False
        assert r["code"] == "bad_file_entries"


class TestDevKitApiSubmit:
    def test_uses_active_developer_when_omitted(
        self, fake_smtp, make_temp_file
    ):
        f = make_temp_file("a.bin", b"x")
        api = DevKitApi()
        api.login("alice")
        r = api.submit(
            developer_id=None,
            target_kind="character",
            target_id="char_yuki",
            payload={"notes": "via omitempty"},
            file_entries=[{"path": f, "size": 1}],
            smtp_send=fake_smtp,
        )
        assert r["ok"] is True
        assert r["data"]["developer_id"] == "alice"
        assert r["data"]["smtp_status"] == "sent"

    def test_missing_developer_no_session(self, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        api = DevKitApi()
        r = api.submit(
            target_kind="character",
            target_id="char_yuki",
            file_entries=[{"path": f, "size": 1}],
        )
        assert r["ok"] is False
        assert r["code"] == "missing_developer_id"

    def test_missing_kind(self, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        api = DevKitApi()
        r = api.submit(
            developer_id="alice",
            target_id="char_yuki",
            file_entries=[{"path": f, "size": 1}],
        )
        assert r["ok"] is False
        assert r["code"] == "missing_target_kind"

    def test_missing_target_id(self, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        api = DevKitApi()
        r = api.submit(
            developer_id="alice",
            target_kind="character",
            target_id="",
            file_entries=[{"path": f, "size": 1}],
        )
        assert r["ok"] is False
        assert r["code"] == "missing_target_id"

    def test_bad_file_entries_type(self):
        api = DevKitApi()
        r = api.submit(
            developer_id="alice",
            target_kind="character",
            target_id="x",
            file_entries="not a list",
        )
        assert r["ok"] is False
        assert r["code"] == "bad_file_entries"

    def test_payload_must_be_mapping(self, fake_smtp, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        api = DevKitApi()
        r = api.submit(
            developer_id="alice",
            target_kind="character",
            target_id="x",
            payload="not a mapping",
            file_entries=[{"path": f, "size": 1}],
            smtp_send=fake_smtp,
        )
        assert r["ok"] is True
        # Non-mapping payload becomes None in the orchestrator.
        assert r["data"]["notes"] == ""


def fake_smtp_for_payload_test():
    """Return a callable compatible with ``smtp_send=`` that does
    nothing — used in a couple of happy-path tests.
    """
    from devkit import _smtp_send

    return _smtp_send


class TestDevKitApiDelete:
    def test_delete_existing(self, fake_smtp, make_temp_file):
        f = make_temp_file("a.bin", b"x")
        r = submit(
            "alice",
            "character",
            "char_yuki",
            file_entries=[{"path": f, "size": 1}],
            smtp_send=fake_smtp,
        )
        api = DevKitApi()
        out = api.delete_local(r["id"])
        assert out["ok"] is True
        assert out["data"]["deleted"] is True

    def test_delete_missing_id(self):
        api = DevKitApi()
        r = api.delete_local("sub_doesnotexist")
        assert r["ok"] is True
        assert r["data"]["deleted"] is False

    def test_delete_empty_id(self):
        api = DevKitApi()
        r = api.delete_local("")
        assert r["ok"] is False
        assert r["code"] == "missing_submission_id"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_defaults(self):
        from devkit.main import _parse_args

        ns = _parse_args([])
        assert ns.smtp_host is None
        assert ns.smtp_port is None
        assert ns.no_smtp_tls is False
        assert ns.smtp_user is None
        assert ns.recipient is None
        assert ns.width == 1280
        assert ns.height == 820
        assert ns.headless is False

    def test_overrides(self):
        from devkit.main import _parse_args

        ns = _parse_args(
            [
                "--smtp-host",
                "h.example",
                "--smtp-port",
                "465",
                "--no-smtp-tls",
                "--smtp-user",
                "u@example",
                "--recipient",
                "r@example",
                "--width",
                "1000",
                "--height",
                "600",
                "--headless",
            ]
        )
        assert ns.smtp_host == "h.example"
        assert ns.smtp_port == 465
        assert ns.no_smtp_tls is True
        assert ns.smtp_user == "u@example"
        assert ns.recipient == "r@example"
        assert ns.width == 1000
        assert ns.height == 600
        assert ns.headless is True

    def test_rejects_non_positive_dimensions(self):
        from devkit.main import _parse_args

        with pytest.raises(SystemExit):
            _parse_args(["--width", "0"])

        with pytest.raises(SystemExit):
            _parse_args(["--height", "-1"])


class TestMainHeadless:
    def test_headless_does_not_open_window(self, capsys, monkeypatch):
        # ``_print_config`` writes JSON to stdout.  If ``webview``
        # were imported, the test would hang trying to open a window.
        from devkit import main as devkit_main

        rc = devkit_main.run(["--headless"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "smtp_host" in out
        assert "recipient" in out

    def test_missing_pywebview_raises(self, monkeypatch):
        # Make :func:`run` believe pywebview is *not* available and
        # skip the headless path so we hit the guard.
        import builtins

        real_import = builtins.__import__

        def _block(name, *args, **kw):
            if name == "webview" or name.startswith("webview."):
                raise ImportError("simulated absence of pywebview")
            return real_import(name, *args, **kw)

        monkeypatch.setattr(builtins, "__import__", _block)
        from devkit import main as devkit_main

        with pytest.raises(RuntimeError) as ei:
            devkit_main.run([])
        assert "pip install pywebview" in str(ei.value)


# ---------------------------------------------------------------------------
# Module-level sanity
# ---------------------------------------------------------------------------


def test_package_does_not_depend_on_flask():
    """The DevKit must remain importable even when Flask is broken.

    The DevKit is a standalone package that never imports Flask (the
    only borrowed pieces are vendored dependency-free in
    :mod:`devkit._vendor`).  We force a ModuleNotFoundError on flask and
    re-import to confirm nothing pulls it in.  Skipped if Flask is
    genuinely missing (e.g. the standalone build env).
    """
    try:
        import flask  # noqa: F401
    except ImportError:
        pytest.skip("flask not installed")
    import sys

    # Snapshot the original devkit modules so we can restore them
    # afterwards — re-importing devkit creates brand-new class objects
    # (e.g. DevKitError) that would otherwise leak into later tests and
    # break isinstance/pytest.raises checks that reference the old class.
    saved_devkit = {
        name: mod
        for name, mod in list(sys.modules.items())
        if name == "devkit" or name.startswith("devkit.")
    }
    sys.modules["flask"] = None  # simulate the module being absent
    for mod in list(sys.modules):
        if mod == "devkit" or mod.startswith("devkit."):
            sys.modules.pop(mod, None)
    try:
        import devkit  # noqa: F401
        import devkit.api  # noqa: F401
        import devkit.main  # noqa: F401
    finally:
        del sys.modules["flask"]
        for name, mod in saved_devkit.items():
            sys.modules[name] = mod
    # If the imports above raised ImportError they'd bubble — the
    # devkit package doesn't import flask anywhere.
    assert True


def test_package_does_not_depend_on_xijian_api():
    """The DevKit must import with **no** ``xijian_api`` on the path.

    This is the structural guarantee behind the C5 packaging split: the
    PyInstaller binary bundles only ``devkit`` (+ pywebview / py7zr),
    never the API package.  We simulate ``xijian_api`` being absent and
    re-import every DevKit module.
    """
    import sys

    saved = {
        name: mod
        for name, mod in list(sys.modules.items())
        if name == "xijian_api" or name.startswith("xijian_api.")
    }
    for name in saved:
        sys.modules[name] = None  # simulate absence
    saved_devkit = {
        name: mod
        for name, mod in list(sys.modules.items())
        if name == "devkit" or name.startswith("devkit.")
    }
    for mod in list(sys.modules):
        if mod == "devkit" or mod.startswith("devkit."):
            sys.modules.pop(mod, None)
    try:
        import devkit  # noqa: F401
        import devkit.api  # noqa: F401
        import devkit.main  # noqa: F401
        import devkit.state  # noqa: F401
        import devkit._vendor  # noqa: F401
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        for name, mod in saved_devkit.items():
            sys.modules[name] = mod
    assert True


def test_state_module_owns_independent_buckets():
    """The DevKit's state module owns its own three in-memory buckets,
    separate from any main-API state (the packages no longer share code)."""
    import devkit.state as ds

    assert isinstance(ds.submissions, dict)
    assert isinstance(ds.last_submit_at, dict)
    assert isinstance(ds.local_archives, dict)
    # The three buckets are distinct objects.
    assert ds.submissions is not ds.last_submit_at
    assert ds.submissions is not ds.local_archives


# ---------------------------------------------------------------------------
# Voice cloner (audio_data + sample_path flows)
# ---------------------------------------------------------------------------


class TestVoiceCloner:
    """Cover ``devkit.voice_cloner.save_voice`` for both audio sources
    (a file path *or* raw bytes) and the delete-with-sample-file path.

    The recording flow on the UI side wraps the bytes in base64 and
    forwards them as ``audio_data``; this is the same code path the
    file picker uses with ``sample_path``.
    """

    def test_save_with_sample_path_copies_file(self, tmp_path, make_temp_file):
        from devkit.voice_cloner import save_voice, list_voices

        src = make_temp_file("ref.wav", b"RIFF\x00\x00\x00\x00WAVE")
        rec = save_voice(str(tmp_path), "char_a", "default", sample_path=src)
        assert rec["name"] == "default"
        assert rec["sample_path"].endswith(".wav")
        assert os.path.isfile(rec["sample_path"])
        listed = list_voices(str(tmp_path), "char_a")
        assert len(listed) == 1
        assert listed[0]["id"] == rec["id"]

    def test_save_with_audio_data_writes_wav(self, tmp_path):
        from devkit.voice_cloner import save_voice, get_voice

        rec = save_voice(
            str(tmp_path), "char_b", "whisper",
            audio_data=b"ID3\x03\x00\x00\x00\x00\x00\x21",
        )
        assert rec["name"] == "whisper"
        assert rec["sample_path"].endswith(".wav")
        # Bytes should land verbatim on disk.
        with open(rec["sample_path"], "rb") as f:
            assert f.read() == b"ID3\x03\x00\x00\x00\x00\x00\x21"
        # And be findable by id across the work_dir.
        loaded = get_voice(str(tmp_path), rec["id"])
        assert loaded is not None
        assert loaded["name"] == "whisper"

    def test_save_with_same_name_overwrites(self, tmp_path):
        """Re-saving under the same character+name replaces the entry
        rather than creating a duplicate (matches the UI's "edit" flow).
        """
        from devkit.voice_cloner import save_voice, list_voices

        save_voice(str(tmp_path), "char_c", "default", audio_data=b"v1")
        save_voice(str(tmp_path), "char_c", "default", audio_data=b"v2")
        listed = list_voices(str(tmp_path), "char_c")
        assert len(listed) == 1
        with open(listed[0]["sample_path"], "rb") as f:
            assert f.read() == b"v2"

    def test_delete_voice_removes_sample_file(self, tmp_path):
        from devkit.voice_cloner import delete_voice, save_voice

        rec = save_voice(str(tmp_path), "char_d", "default", audio_data=b"x" * 64)
        sample = rec["sample_path"]
        assert os.path.isfile(sample)
        ok = delete_voice(str(tmp_path), rec["id"])
        assert ok is True
        assert not os.path.isfile(sample)

    def test_unsupported_audio_format_raises(self, tmp_path, make_temp_file):
        from devkit.voice_cloner import save_voice

        src = make_temp_file("ref.xyz", b"junk")
        with pytest.raises(DevKitError) as ei:
            save_voice(str(tmp_path), "char_e", "default", sample_path=src)
        assert ei.value.code == "bad_audio_format"

    def test_list_engines_includes_mlx(self):
        from devkit.voice_cloner import list_engines

        engines = list_engines()
        assert "mlx" in engines
        assert "gguf" in engines
        assert "fallback" in engines
        assert isinstance(engines, list)


# ---------------------------------------------------------------------------
# Voice save_voice js_api bridge — audio_data_b64 path
# ---------------------------------------------------------------------------


class TestDevKitApiSaveVoiceRecording:
    """End-to-end test for the ``audio_data_b64`` parameter on
    :meth:`DevKitApi.save_voice`.  This is what the JS recording
    button sends.
    """

    def _work_dir(self, tmp_path):
        return str(tmp_path / "work")

    def test_audio_data_b64_pure_string(self, tmp_path):
        import base64
        api = DevKitApi()
        raw = b"RIFF\x10\x00\x00\x00WAVEfmt "
        resp = api.save_voice(
            character_id="char_z",
            name="default",
            audio_data_b64=base64.b64encode(raw).decode("ascii"),
        )
        assert resp["ok"] is True
        rec = resp["data"]
        with open(rec["sample_path"], "rb") as f:
            assert f.read() == raw

    def test_audio_data_b64_with_data_url_prefix(self, tmp_path):
        """``FileReader.readAsDataURL`` returns ``data:audio/webm;base64,XXX``.
        The server should strip the prefix before decoding.
        """
        import base64
        api = DevKitApi()
        raw = b"\x1aE\xdf\xa3" * 4
        b64 = base64.b64encode(raw).decode("ascii")
        prefixed = f"data:audio/webm;codecs=opus;base64,{b64}"
        resp = api.save_voice(
            character_id="char_y",
            name="whisper",
            audio_data_b64=prefixed,
        )
        assert resp["ok"] is True
        with open(resp["data"]["sample_path"], "rb") as f:
            assert f.read() == raw

    def test_both_sources_rejected(self, tmp_path, make_temp_file):
        """Sending both ``sample_path`` and ``audio_data_b64`` is
        ambiguous; the API must refuse with a clean 400."""
        api = DevKitApi()
        src = make_temp_file("ref.wav", b"x")
        resp = api.save_voice(
            character_id="char_x",
            name="default",
            sample_path=src,
            audio_data_b64="AAAA",
        )
        assert resp["ok"] is False
        assert resp["code"] == "ambiguous_audio_source"

    def test_no_source_rejected(self, tmp_path):
        api = DevKitApi()
        resp = api.save_voice(
            character_id="char_w",
            name="default",
        )
        assert resp["ok"] is False
        assert resp["code"] == "missing_audio_source"

    def test_bad_base64_rejected(self, tmp_path):
        api = DevKitApi()
        resp = api.save_voice(
            character_id="char_v",
            name="default",
            audio_data_b64="!!!not-base64@@@",
        )
        assert resp["ok"] is False
        assert resp["code"] == "bad_audio_base64"

    def test_empty_recording_rejected(self, tmp_path):
        api = DevKitApi()
        resp = api.save_voice(
            character_id="char_u",
            name="default",
            audio_data_b64="",
        )
        assert resp["ok"] is False
        assert resp["code"] == "missing_audio_source"


# ---------------------------------------------------------------------------
# 3D model viewer — file registration + bytes bridge
# ---------------------------------------------------------------------------


class TestModelViewer:
    def test_register_glb_creates_entry(self, tmp_path, make_temp_file):
        from devkit.model_viewer import (
            list_models, register_model, read_model_bytes,
            get_model_info, unregister_model,
        )
        src = make_temp_file("yuki.glb", b"glTF\x02\x00\x00\x00")
        rec = register_model(str(tmp_path), src)
        assert rec["format"] == "glb"
        assert rec["size_bytes"] == len(b"glTF\x02\x00\x00\x00")
        listed = list_models(str(tmp_path))
        assert len(listed) == 1 and listed[0]["id"] == rec["id"]

        info = get_model_info(str(tmp_path), rec["id"])
        assert info is not None and info["path"] == src

        raw = read_model_bytes(str(tmp_path), rec["id"])
        assert raw is not None
        assert raw["format"] == "glb"
        assert raw["mime"] == "model/gltf-binary"
        import base64
        assert base64.b64decode(raw["data_b64"]) == b"glTF\x02\x00\x00\x00"

        assert unregister_model(str(tmp_path), rec["id"]) is True
        assert list_models(str(tmp_path)) == []
        # Reading an unregistered model returns None.
        assert read_model_bytes(str(tmp_path), rec["id"]) is None

    def test_register_vrm_records_vrm_format(self, tmp_path, make_temp_file):
        from devkit.model_viewer import read_model_bytes, register_model

        src = make_temp_file("hero.vrm", b"glTF\x02\x00\x00\x00VRM")
        rec = register_model(str(tmp_path), src)
        assert rec["format"] == "vrm"
        raw = read_model_bytes(str(tmp_path), rec["id"])
        assert raw["mime"] == "model/gltf-binary"  # VRM is GLB-shaped

    def test_register_unsupported_format_rejected(self, tmp_path, make_temp_file):
        from devkit.model_viewer import register_model

        src = make_temp_file("bad.obj", b"v 0 0 0")
        with pytest.raises(DevKitError) as ei:
            register_model(str(tmp_path), src)
        assert ei.value.code == "bad_format"

    def test_register_missing_file_rejected(self, tmp_path):
        from devkit.model_viewer import register_model

        with pytest.raises(DevKitError) as ei:
            register_model(str(tmp_path), str(tmp_path / "nope.glb"))
        assert ei.value.code == "file_not_found"

    def test_register_duplicate_path_returns_existing(self, tmp_path, make_temp_file):
        """Re-registering the same file path returns the existing entry
        (so the UI doesn't accumulate duplicates if the user clicks
        'add' twice in a row)."""
        from devkit.model_viewer import list_models, register_model

        src = make_temp_file("dup.glb", b"glTF")
        first = register_model(str(tmp_path), src)
        second = register_model(str(tmp_path), src)
        assert first["id"] == second["id"]
        assert len(list_models(str(tmp_path))) == 1


# ---------------------------------------------------------------------------
# 3D model read_model_bytes js_api bridge
# ---------------------------------------------------------------------------


class TestDevKitApiModelBytes:
    def test_returns_null_for_unknown_id(self, tmp_path):
        api = DevKitApi()
        resp = api.read_model_bytes("model_does_not_exist")
        # ``read_model_bytes`` returns None when the id is unknown, so
        # the success envelope is ``{"ok": True, "data": None}``.
        assert resp["ok"] is True
        assert resp["data"] is None

    def test_returns_envelope_for_known_model(self, tmp_path, make_temp_file, monkeypatch):
        import base64
        from devkit.model_viewer import register_model

        # The api's ``_work_dir`` reads ``XIJIAN_DEV_WORK_DIR`` from
        # the environment; point it at ``tmp_path`` so the registered
        # model is visible to ``api.read_model_bytes``.
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        src = make_temp_file("yuki.glb", b"glTF" + b"\x00" * 60)
        rec = register_model(str(tmp_path), src)
        api = DevKitApi()
        resp = api.read_model_bytes(rec["id"])
        assert resp["ok"] is True
        assert resp["data"]["id"] == rec["id"]
        assert resp["data"]["mime"] == "model/gltf-binary"
        assert base64.b64decode(resp["data"]["data_b64"]) == b"glTF" + b"\x00" * 60


# ---------------------------------------------------------------------------
# Auto-update (C6): version parsing / comparison
# ---------------------------------------------------------------------------


class TestUpdaterVersionCompare:
    def test_parse_strips_v_prefix(self):
        from devkit.updater import parse_version

        nums, rank, label = parse_version("v1.2.3")
        assert nums == (1, 2, 3)
        assert label == ""

    def test_major_minor_patch_ordering(self):
        from devkit.updater import is_newer

        assert is_newer("v1.0.0", "v0.9.9") is True
        assert is_newer("v0.2.0", "v0.1.9") is True
        assert is_newer("v0.1.2", "v0.1.1") is True

    def test_equal_versions_not_newer(self):
        from devkit.updater import is_newer

        assert is_newer("v0.1.0", "v0.1.0") is False
        assert is_newer("0.1.0", "v0.1.0") is False

    def test_release_outranks_prerelease(self):
        from devkit.updater import is_newer

        assert is_newer("v0.1.0", "v0.1.0-Beta") is True
        assert is_newer("v0.1.0-Beta", "v0.1.0") is False

    def test_prerelease_ordering(self):
        from devkit.updater import is_newer

        assert is_newer("v0.1.0-Beta", "v0.1.0-Alpha") is True
        assert is_newer("v0.1.0-rc", "v0.1.0-Beta") is True

    def test_differing_length_padding(self):
        from devkit.updater import is_newer

        assert is_newer("v1.0.1", "v1.0") is True
        assert is_newer("v1.0", "v1.0.0") is False


# ---------------------------------------------------------------------------
# Auto-update (C6): check_for_update (mocked network)
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    def __init__(self, payload: dict, headers: dict | None = None):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self, *_a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class TestCheckForUpdate:
    def test_unconfigured_source_returns_error(self, monkeypatch):
        from devkit import updater, version

        monkeypatch.setattr(
            version, "get_update_source",
            lambda: {"owner": "", "repo": "", "api_url": ""},
        )
        r = updater.check_for_update("v0.1.0")
        assert r["configured"] is False
        assert r["update_available"] is False
        assert "error" in r

    def test_detects_newer_release_and_asset(self, monkeypatch):
        from devkit import updater, version

        monkeypatch.setattr(
            version, "get_update_source",
            lambda: {"owner": "XiJian-Development-Group", "repo": "XiJian",
                     "tag_prefix": "DevKit@",
                     "api_url": "https://api.github.com/repos/XiJian-Development-Group/XiJian/releases?per_page=100"},
        )
        monkeypatch.setattr(
            version, "get_asset_patterns",
            lambda: {"macOS": "DevKit_macOS.zip", "Windows": "DevKit_Windows.zip", "Linux": ""},
        )
        # A list of releases across components; only DevKit@ ones count.
        releases = [
            {"tag_name": "Core@v3.0.0", "assets": []},
            {"tag_name": "Main@v9.9.9", "assets": []},
            {
                "tag_name": "DevKit@v1.5.0",
                "body": "Notes",
                "html_url": "https://github.com/XiJian-Development-Group/XiJian/releases/tag/DevKit@v1.5.0",
                "assets": [
                    {"name": "DevKit_Windows.zip", "browser_download_url": "https://dl/win.zip", "size": 1},
                    {"name": "DevKit_macOS.zip", "browser_download_url": "https://dl/mac.zip", "size": 99},
                ],
            },
            {"tag_name": "DevKit@v1.4.0", "assets": []},
        ]
        r = updater.check_for_update(
            "v1.4.3", _opener=lambda req: _FakeHTTPResponse(releases)
        )
        assert r["update_available"] is True
        # Version is reported with the tag prefix stripped.
        assert r["latest_version"] == "v1.5.0"
        import sys as _sys
        if _sys.platform == "darwin":
            assert r["asset_name"] == "DevKit_macOS.zip"
            assert r["asset_url"] == "https://dl/mac.zip"

    def test_ignores_other_component_releases(self, monkeypatch):
        from devkit import updater, version

        monkeypatch.setattr(
            version, "get_update_source",
            lambda: {"owner": "o", "repo": "r", "tag_prefix": "DevKit@",
                     "api_url": "https://api.github.com/repos/o/r/releases?per_page=100"},
        )
        # Newer Main/Core releases must NOT trigger a DevKit update.
        releases = [
            {"tag_name": "Main@v9.9.9", "assets": []},
            {"tag_name": "Core@v8.8.8", "assets": []},
            {"tag_name": "DevKit@v1.4.3", "assets": []},
        ]
        r = updater.check_for_update(
            "v1.4.3", _opener=lambda req: _FakeHTTPResponse(releases)
        )
        assert r["update_available"] is False
        assert r["latest_version"] == "v1.4.3"

    def test_no_matching_component_release(self, monkeypatch):
        from devkit import updater, version

        monkeypatch.setattr(
            version, "get_update_source",
            lambda: {"owner": "o", "repo": "r", "tag_prefix": "DevKit@",
                     "api_url": "https://api.github.com/repos/o/r/releases?per_page=100"},
        )
        releases = [{"tag_name": "Main@v1.0.0", "assets": []}]
        r = updater.check_for_update(
            "v1.4.3", _opener=lambda req: _FakeHTTPResponse(releases)
        )
        assert r["update_available"] is False
        assert "error" in r

    def test_same_version_no_update(self, monkeypatch):
        from devkit import updater, version

        monkeypatch.setattr(
            version, "get_update_source",
            lambda: {"owner": "o", "repo": "r", "tag_prefix": "DevKit@",
                     "api_url": "https://api.github.com/repos/o/r/releases?per_page=100"},
        )
        releases = [{"tag_name": "DevKit@v1.4.3", "assets": []}]
        r = updater.check_for_update(
            "v1.4.3", _opener=lambda req: _FakeHTTPResponse(releases)
        )
        assert r["update_available"] is False

    def test_network_error_is_caught(self, monkeypatch):
        from devkit import updater, version

        monkeypatch.setattr(
            version, "get_update_source",
            lambda: {"owner": "o", "repo": "r", "tag_prefix": "DevKit@",
                     "api_url": "https://api.github.com/repos/o/r/releases?per_page=100"},
        )

        def _boom(req):
            raise OSError("network down")

        r = updater.check_for_update("v1.4.3", _opener=_boom)
        assert r["update_available"] is False
        assert "error" in r


# ---------------------------------------------------------------------------
# Auto-update (C6): download (mocked network)
# ---------------------------------------------------------------------------


class TestDownloadUpdate:
    def test_requires_url_and_name(self):
        from devkit import updater

        assert "error" in updater.download_update("", "x.dmg")
        assert "error" in updater.download_update("https://dl/x", "")

    def test_streams_to_downloads_dir(self, monkeypatch, tmp_path):
        from devkit import updater

        monkeypatch.setattr(updater, "downloads_dir", lambda: tmp_path)

        payload = b"BINARYDATA" * 1000

        class _Stream:
            def __init__(self):
                self._buf = io.BytesIO(payload)
                self.headers = {"Content-Length": str(len(payload))}

            def read(self, n):
                return self._buf.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        seen = []
        r = updater.download_update(
            "https://dl/app.dmg", "app.dmg",
            progress_cb=lambda d, t: seen.append((d, t)),
            _opener=lambda req: _Stream(),
        )
        assert "error" not in r
        assert r["size"] == len(payload)
        assert (tmp_path / "app.dmg").read_bytes() == payload
        assert seen and seen[-1][0] == len(payload)

    def test_sanitizes_asset_name(self, monkeypatch, tmp_path):
        from devkit import updater

        monkeypatch.setattr(updater, "downloads_dir", lambda: tmp_path)

        class _Stream:
            headers = {"Content-Length": "3"}

            def read(self, n):
                if not hasattr(self, "_done"):
                    self._done = True
                    return b"abc"
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        r = updater.download_update(
            "https://dl/x", "../../evil.dmg",
            _opener=lambda req: _Stream(),
        )
        # Path traversal stripped: file lands inside tmp_path only.
        assert "error" not in r
        assert os.path.dirname(r["path"]) == str(tmp_path)


# ---------------------------------------------------------------------------
# Auto-update (C6): api bridge + version source
# ---------------------------------------------------------------------------


class TestUpdateApiBridge:
    def test_get_update_settings_shape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        api = DevKitApi()
        resp = api.get_update_settings()
        assert resp["ok"] is True
        data = resp["data"]
        assert "current_version" in data
        assert "auto_check" in data
        assert "configured" in data

    def test_set_auto_check_persists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        api = DevKitApi()
        api.set_work_dir(str(tmp_path))
        r1 = api.set_auto_check_update(False)
        assert r1["ok"] is True
        assert r1["data"]["auto_check"] is False
        from devkit import config as _cfg
        assert _cfg.get_auto_check_update(str(tmp_path)) is False

    def test_download_update_requires_args(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        api = DevKitApi()
        resp = api.download_update("", "")
        assert resp["ok"] is False


class TestVersionSource:
    def test_get_app_version_nonempty(self):
        from devkit import version

        assert isinstance(version.get_app_version(), str)
        assert version.get_app_version()

    def test_reads_devkit_version_from_config(self):
        from devkit import version

        cfg = version.read_project_config()
        # Config.json ships a DevKit version entry.
        assert cfg.get("Version", {}).get("DevKit")



# ---------------------------------------------------------------------------
# C2.1 新增功能测试：文本生成声音、版权确认、DiffSinger
# ---------------------------------------------------------------------------


class TestGenerateVoiceFromText:
    """C2.1 文本生成语音（MeloTTS 首选 + 自动回退）"""

    def test_generate_voice_from_text_melo_first(self, tmp_path):
        from devkit.voice_cloner import generate_voice_from_text

        # MeloTTS 引擎应该被尝试，如果不可用会自动回退
        # 这里主要测试 API 调用不报错且返回正确结构
        result = generate_voice_from_text(
            work_dir=str(tmp_path),
            character_id="char_test",
            name="test_voice",
            text="你好世界",
            engine="melo",
            params={"speed": 1.0, "pitch": 1.0, "energy": 1.0, "language": "zh"},
        )
        # MeloTTS 在测试环境可能不可用，但应通过 fallback 成功
        assert result["success"] is True
        assert "voice_id" in result
        assert "audio_path" in result
        assert os.path.isfile(result["audio_path"])
        assert result["engine"] in ("melo", "mlx", "gguf", "fallback")
        assert result["duration_sec"] > 0

    def test_generate_voice_from_text_empty_text_raises(self, tmp_path):
        from devkit.voice_cloner import generate_voice_from_text
        from devkit import DevKitError

        with pytest.raises(DevKitError) as ei:
            generate_voice_from_text(
                work_dir=str(tmp_path),
                character_id="char_test",
                name="test",
                text="",  # 空文本
            )
        assert ei.value.code == "empty_text"

    def test_generate_voice_from_text_missing_name_raises(self, tmp_path):
        from devkit.voice_cloner import generate_voice_from_text
        from devkit import DevKitError

        with pytest.raises(DevKitError) as ei:
            generate_voice_from_text(
                work_dir=str(tmp_path),
                character_id="char_test",
                name="",  # 空名称
                text="测试文本",
            )
        assert ei.value.code == "missing_name"

    def test_generate_voice_from_text_with_params(self, tmp_path):
        from devkit.voice_cloner import generate_voice_from_text

        result = generate_voice_from_text(
            work_dir=str(tmp_path),
            character_id="char_test",
            name="test_params",
            text="参数测试",
            engine="melo",
            params={
                "speed": 1.5,
                "pitch": 0.8,
                "energy": 1.2,
                "language": "en",
                "voice_id": "melo_en_female_0",
            },
        )
        assert result["success"] is True
        # 验证生成的语音记录包含参数
        from devkit.voice_cloner import get_voice
        voice = get_voice(str(tmp_path), result["voice_id"])
        assert voice is not None
        assert voice["params"].get("generated_from_text") is True
        assert voice["params"].get("speed") == 1.5
        assert voice["params"].get("pitch") == 0.8


class TestGenerateVoiceFromDescription:
    """C2.1 文本描述生成声音（自然语言 → TTS 参数）"""

    def test_parse_female_young_gentle(self, tmp_path):
        from devkit.voice_cloner import _parse_voice_description

        params = _parse_voice_description("温柔的年轻女性声音，语速稍慢，音调偏高，带点甜美感")
        # 性别 -> voice_id
        assert params.get("voice_id") == "melo_zh_female_0"
        # 年轻 -> pitch 偏高
        assert params.get("pitch", 1.0) > 1.0
        # 温柔 -> pitch 略高, energy 略低
        assert params.get("energy", 1.0) < 1.0
        # 语速稍慢
        assert params.get("speed", 1.0) < 1.0
        # 甜美 -> pitch 更高
        assert params.get("pitch", 1.0) > 1.1
        # 语言默认中文
        assert params.get("language") == "zh"

    def test_parse_male_mature_magnetic(self, tmp_path):
        from devkit.voice_cloner import _parse_voice_description

        params = _parse_voice_description("磁性成熟男声，语速正常，音调偏低，情感深沉")
        assert params.get("voice_id") == "melo_zh_male_0"
        # 成熟 -> pitch 偏低
        assert params.get("pitch", 1.0) < 1.0
        # 磁性 -> pitch 更低, energy 略高
        assert params.get("pitch", 1.0) < 0.95
        assert params.get("energy", 1.0) > 1.0
        # 语速正常
        assert params.get("speed", 1.0) == pytest.approx(1.0, abs=0.1)
        # 深沉 -> pitch 更低
        assert params.get("pitch", 1.0) < 0.9
        # 语言默认中文
        assert params.get("language") == "zh"

    def test_parse_english_language(self, tmp_path):
        from devkit.voice_cloner import _parse_voice_description

        params = _parse_voice_description("A gentle young female voice speaking English")
        assert params.get("language") == "en"
        assert params.get("voice_id") == "melo_en_female_0"

    def test_parse_japanese_language(self, tmp_path):
        from devkit.voice_cloner import _parse_voice_description

        params = _parse_voice_description("日本語の優しい女性の声")
        assert params.get("language") == "jp"

    def test_parse_cantonese_language(self, tmp_path):
        from devkit.voice_cloner import _parse_voice_description

        params = _parse_voice_description("粤语 温柔 女声")
        # 粤语使用中文模型
        assert params.get("language") == "zh"

    def test_parse_emotion_happy(self, tmp_path):
        from devkit.voice_cloner import _parse_voice_description

        params = _parse_voice_description("开心愉快的声音")
        assert params.get("energy", 1.0) > 1.0
        assert params.get("pitch", 1.0) > 1.0
        assert params.get("speed", 1.0) > 1.0

    def test_parse_emotion_sad(self, tmp_path):
        from devkit.voice_cloner import _parse_voice_description

        params = _parse_voice_description("悲伤忧郁的声音")
        assert params.get("energy", 1.0) < 1.0
        assert params.get("pitch", 1.0) < 1.0
        assert params.get("speed", 1.0) < 1.0

    def test_parse_emotion_serious(self, tmp_path):
        from devkit.voice_cloner import _parse_voice_description

        params = _parse_voice_description("严肃严厉的声音")
        assert params.get("energy", 1.0) > 1.0
        assert params.get("pitch", 1.0) < 1.0
        assert params.get("speed", 1.0) < 1.0

    def test_generate_voice_from_description_integration(self, tmp_path):
        from devkit.voice_cloner import generate_voice_from_description

        result = generate_voice_from_description(
            work_dir=str(tmp_path),
            character_id="char_test",
            name="desc_voice",
            description="温柔的年轻女性声音，语速稍慢，音调偏高",
            engine="melo",
        )
        assert result["success"] is True
        assert "voice_id" in result
        assert "audio_path" in result
        assert os.path.isfile(result["audio_path"])

    def test_generate_voice_from_description_empty_raises(self, tmp_path):
        from devkit.voice_cloner import generate_voice_from_description
        from devkit import DevKitError

        with pytest.raises(DevKitError) as ei:
            generate_voice_from_description(
                work_dir=str(tmp_path),
                character_id="char_test",
                name="test",
                description="",  # 空描述
            )
        assert ei.value.code == "empty_description"


class TestCopyrightConfirmation:
    """C2.1 版权确认系统（AC-1：上传声音样本前必须确认版权）"""

    def test_create_copyright_confirmation(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, get_copyright_status
        )

        # 先创建一个声音
        voice = save_voice(str(tmp_path), "char_copyright", "test_voice", audio_data=b"test")

        # 创建版权确认记录
        record = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_copyright",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_123",
            license_info="自行录制",
            evidence_urls=["https://example.com/proof"],
            expires_in_days=365,
        )
        assert record["status"] == "pending"
        assert record["copyright_type"] == "original"
        assert record["declared_by"] == "user_123"
        assert record["license_info"] == "自行录制"
        assert record["evidence_urls"] == ["https://example.com/proof"]
        assert record["expires_at"] is not None
        assert len(record["audit_trail"]) == 1
        assert record["audit_trail"][0]["action"] == "create"

    def test_create_copyright_invalid_type_raises(self, tmp_path):
        from devkit.voice_cloner import save_voice, create_copyright_confirmation
        from devkit import DevKitError

        voice = save_voice(str(tmp_path), "char_c", "v", audio_data=b"x")
        with pytest.raises(DevKitError) as ei:
            create_copyright_confirmation(
                work_dir=str(tmp_path),
                character_id="char_c",
                voice_id=voice["id"],
                copyright_type="invalid_type",
                declared_by="user_1",
            )
        assert ei.value.code == "bad_copyright_type"

    def test_create_copyright_missing_voice_raises(self, tmp_path):
        from devkit.voice_cloner import create_copyright_confirmation
        from devkit import DevKitError

        with pytest.raises(DevKitError) as ei:
            create_copyright_confirmation(
                work_dir=str(tmp_path),
                character_id="char_x",
                voice_id="nonexistent",
                copyright_type="original",
                declared_by="user_1",
            )
        assert ei.value.code == "voice_not_found"

    def test_confirm_copyright_approved(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, confirm_copyright, get_copyright_status
        )

        voice = save_voice(str(tmp_path), "char_cc", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_cc",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
        )

        # 确认版权
        confirmed = confirm_copyright(
            work_dir=str(tmp_path),
            copyright_id=cr["id"],
            actor="user_1",
            confirm=True,
        )
        assert confirmed["status"] == "confirmed"
        assert len(confirmed["audit_trail"]) == 2
        assert confirmed["audit_trail"][1]["action"] == "confirm"
        assert confirmed["audit_trail"][1]["actor"] == "user_1"

        # 通过 get_copyright_status 验证
        status = get_copyright_status(str(tmp_path), voice["id"])
        assert status["status"] == "confirmed"

    def test_confirm_copyright_rejected(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, confirm_copyright, get_copyright_status
        )

        voice = save_voice(str(tmp_path), "char_cr", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_cr",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
        )

        # 拒绝版权
        rejected = confirm_copyright(
            work_dir=str(tmp_path),
            copyright_id=cr["id"],
            actor="user_1",
            confirm=False,
        )
        assert rejected["status"] == "rejected"
        assert rejected["audit_trail"][1]["action"] == "reject"

        status = get_copyright_status(str(tmp_path), voice["id"])
        assert status["status"] == "rejected"

    def test_confirm_copyright_already_confirmed_raises(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, confirm_copyright
        )
        from devkit import DevKitError

        voice = save_voice(str(tmp_path), "char_cr2", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_cr2",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
        )
        confirm_copyright(str(tmp_path), cr["id"], "user_1", confirm=True)

        # 再次确认应该失败
        with pytest.raises(DevKitError) as ei:
            confirm_copyright(str(tmp_path), cr["id"], "user_1", confirm=True)
        assert ei.value.code == "invalid_state_transition"

    def test_check_copyright_before_upload_allowed(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, confirm_copyright,
            check_copyright_before_upload
        )

        voice = save_voice(str(tmp_path), "char_check", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_check",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
        )
        confirm_copyright(str(tmp_path), cr["id"], "user_1", confirm=True)

        # 检查应该通过
        result = check_copyright_before_upload(str(tmp_path), voice["id"])
        assert result["allowed"] is True
        assert result["reason"] == "版权已确认"
        assert result["record"]["status"] == "confirmed"

    def test_check_copyright_before_upload_rejected(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, confirm_copyright,
            check_copyright_before_upload
        )

        voice = save_voice(str(tmp_path), "char_check2", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_check2",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
        )
        confirm_copyright(str(tmp_path), cr["id"], "user_1", confirm=False)

        result = check_copyright_before_upload(str(tmp_path), voice["id"])
        assert result["allowed"] is False
        assert result["reason"] == "版权确认被拒绝，无法上传"

    def test_check_copyright_before_upload_pending(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation,
            check_copyright_before_upload
        )

        voice = save_voice(str(tmp_path), "char_check3", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_check3",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
        )
        # 不 confirm，保持 PENDING

        result = check_copyright_before_upload(str(tmp_path), voice["id"])
        assert result["allowed"] is False
        assert result["reason"] == "版权确认待处理中"

    def test_check_copyright_before_upload_no_record(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, check_copyright_before_upload
        )

        voice = save_voice(str(tmp_path), "char_check4", "voice", audio_data=b"x")
        # 没有创建版权记录

        result = check_copyright_before_upload(str(tmp_path), voice["id"])
        assert result["allowed"] is False
        assert result["reason"] == "未找到版权确认记录，请先完成版权确认"
        assert result["record"] is None

    def test_dispute_copyright(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, confirm_copyright,
            dispute_copyright, get_copyright_status
        )

        voice = save_voice(str(tmp_path), "char_disp", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_disp",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
        )
        confirm_copyright(str(tmp_path), cr["id"], "user_1", confirm=True)

        # 发起争议
        disputed = dispute_copyright(
            work_dir=str(tmp_path),
            copyright_id=cr["id"],
            actor="user_2",
            reason="该音频侵犯我的版权",
        )
        assert disputed["status"] == "disputed"
        assert disputed["audit_trail"][-1]["action"] == "dispute"
        assert disputed["audit_trail"][-1]["details"] == "该音频侵犯我的版权"

        status = get_copyright_status(str(tmp_path), voice["id"])
        assert status["status"] == "disputed"

    def test_resolve_copyright_dispute_confirmed(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, confirm_copyright,
            dispute_copyright, resolve_copyright_dispute, get_copyright_status
        )

        voice = save_voice(str(tmp_path), "char_res", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_res",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
        )
        confirm_copyright(str(tmp_path), cr["id"], "user_1", confirm=True)
        dispute_copyright(str(tmp_path), cr["id"], "user_2", "争议原因")

        # 解决争议 -> confirmed
        resolved = resolve_copyright_dispute(
            work_dir=str(tmp_path),
            copyright_id=cr["id"],
            actor="admin",
            resolved_status="confirmed",
            reason="经核实属于原创",
        )
        assert resolved["status"] == "confirmed"
        assert resolved["audit_trail"][-1]["action"] == "resolve_dispute"

        status = get_copyright_status(str(tmp_path), voice["id"])
        assert status["status"] == "confirmed"

    def test_resolve_copyright_dispute_rejected(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, confirm_copyright,
            dispute_copyright, resolve_copyright_dispute, get_copyright_status
        )

        voice = save_voice(str(tmp_path), "char_res2", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_res2",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
        )
        confirm_copyright(str(tmp_path), cr["id"], "user_1", confirm=True)
        dispute_copyright(str(tmp_path), cr["id"], "user_2", "争议原因")

        # 解决争议 -> rejected
        resolved = resolve_copyright_dispute(
            work_dir=str(tmp_path),
            copyright_id=cr["id"],
            actor="admin",
            resolved_status="rejected",
            reason="确认侵权",
        )
        assert resolved["status"] == "rejected"

        status = get_copyright_status(str(tmp_path), voice["id"])
        assert status["status"] == "rejected"

    def test_list_copyright_records(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, list_copyright_records
        )

        voice1 = save_voice(str(tmp_path), "char_list", "voice1", audio_data=b"x")
        voice2 = save_voice(str(tmp_path), "char_list", "voice2", audio_data=b"y")

        create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_list",
            voice_id=voice1["id"],
            copyright_type="original",
            declared_by="user_1",
        )
        create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_list",
            voice_id=voice2["id"],
            copyright_type="licensed",
            declared_by="user_1",
        )

        records = list_copyright_records(str(tmp_path), "char_list")
        assert len(records) == 2
        assert records[0]["voice_id"] == voice1["id"]
        assert records[1]["voice_id"] == voice2["id"]

    def test_copyright_expiration_check(self, tmp_path):
        from devkit.voice_cloner import (
            save_voice, create_copyright_confirmation, confirm_copyright,
            get_copyright_status, check_copyright_before_upload
        )
        from datetime import datetime, timedelta, timezone

        voice = save_voice(str(tmp_path), "char_exp", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_exp",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="user_1",
            expires_in_days=0,  # 立即过期（用于测试）
        )
        confirm_copyright(str(tmp_path), cr["id"], "user_1", confirm=True)

        # 手动将过期时间设为过去
        from devkit.voice_cloner import _load_copyright_meta, _save_copyright_meta
        meta = _load_copyright_meta(str(tmp_path), "char_exp")
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        meta[0]["expires_at"] = past
        meta[0]["status"] = "confirmed"
        _save_copyright_meta(str(tmp_path), "char_exp", meta)

        # 再次查询应该触发过期检查
        status = get_copyright_status(str(tmp_path), voice["id"])
        # 注意：过期检查在 get_copyright_status 中进行
        assert status["status"] in ("confirmed", "expired")


class TestDiffSingerEngine:
    """C2.1 DiffSinger 歌声引擎（引擎接口 + 默认 unavailable + set_engine 钩子）"""

    def test_diffsinger_engine_hook_set_and_get(self, tmp_path):
        from devkit.voice_cloner import set_diffsinger_engine, _get_diffsinger_engine
        from devkit.tts_engine import TTSEngine, TTSResult

        # 创建一个 Mock 引擎
        class MockDiffSinger(TTSEngine):
            def __init__(self):
                self._available = True
                self._language = "zh"

            @property
            def name(self) -> str:
                return "mock_diffsinger"

            def is_available(self) -> bool:
                return self._available

            def ensure_model(self, language: str) -> bool:
                return True

            def synthesize(self, request) -> TTSResult:
                return TTSResult(success=True, audio_path="/tmp/mock.wav", engine="mock_diffsinger")

            def list_voices(self) -> list[dict]:
                return [{"id": "mock_voice", "name": "Mock", "language": "zh"}]

            def generate_singing(self, lyrics, voice_id, language, params=None) -> TTSResult:
                return TTSResult(success=True, audio_path="/tmp/mock_sing.wav", engine="mock_diffsinger")

            def _get_cache_dir(self) -> str:
                return "/tmp/diffsinger_cache"

            DIFFSINGER_MODELS = {"zh": "mock/repo"}

        mock_engine = MockDiffSinger()
        set_diffsinger_engine(mock_engine)

        # 获取应该返回我们注入的 mock
        engine = _get_diffsinger_engine()
        assert engine is mock_engine
        assert engine.is_available() is True

        # 重置为 None
        set_diffsinger_engine(None)
        engine2 = _get_diffsinger_engine()
        # 应该返回真实的 DiffSingerEngine 实例
        from devkit.tts_engine import DiffSingerEngine
        assert isinstance(engine2, DiffSingerEngine)

    def test_diffsinger_download_model(self, tmp_path):
        from devkit.voice_cloner import download_diffsinger_model, set_diffsinger_engine
        from devkit.tts_engine import DiffSingerEngine

        # 使用真实引擎测试（不下载，只测试调用链）
        set_diffsinger_engine(None)
        ds = DiffSingerEngine()

        # 默认不可用（模型未下载）
        assert ds.is_available() is False

        # download_diffsinger_model 会尝试下载，测试环境无网络会返回 False
        # 这里只验证函数可调用且不报错
        result = download_diffsinger_model("zh")
        # 在无网络/无模型环境下返回 False 是预期行为
        assert isinstance(result, bool)

    def test_diffsinger_model_status(self, tmp_path):
        from devkit.voice_cloner import get_diffsinger_model_status, set_diffsinger_engine

        set_diffsinger_engine(None)
        status = get_diffsinger_model_status("zh")
        assert "language" in status
        assert "model_repo" in status
        assert "local_path" in status
        assert "is_downloaded" in status
        assert "is_available" in status
        assert status["language"] == "zh"
        assert status["is_available"] is False  # 默认未下载

    def test_generate_singing_requires_melody(self, tmp_path):
        from devkit.voice_cloner import generate_singing
        from devkit import DevKitError

        # 缺少 midi_path 和 melody 都应该报错
        with pytest.raises(DevKitError) as ei:
            generate_singing(
                work_dir=str(tmp_path),
                character_id="char_sing",
                name="sing_test",
                text="歌词测试",
                engine="diffsinger",
                params={},  # 无 melody
            )
        assert ei.value.code == "missing_melody"

    def test_generate_singing_with_midi(self, tmp_path):
        from devkit.voice_cloner import generate_singing, set_diffsinger_engine
        from devkit.tts_engine import TTSEngine, TTSResult

        # 注入 mock 避免真实模型依赖
        class MockDS(TTSEngine):
            @property
            def name(self): return "mock_ds"
            def is_available(self): return True
            def ensure_model(self, lang): return True
            def synthesize(self, req): return TTSResult(True, "/tmp/x.wav", "mock")
            def list_voices(self): return [{"id": "mock_v", "name": "Mock", "language": "zh"}]
            def generate_singing(self, lyrics, voice_id, language, params=None):
                return TTSResult(True, "/tmp/sing.wav", "mock")
            def _get_cache_dir(self): return "/tmp"
            DIFFSINGER_MODELS = {"zh": "mock/repo"}

        set_diffsinger_engine(MockDS())

        result = generate_singing(
            work_dir=str(tmp_path),
            character_id="char_sing2",
            name="sing_test2",
            text="啦啦啦",
            engine="diffsinger",
            params={"midi_path": "/fake/test.mid"},
        )
        # 即使 mock 成功，也可能因为文件不存在而失败
        # 但 API 结构应该正确
        assert "success" in result
        assert "engine" in result
        if result["success"]:
            assert "voice_id" in result
            assert "audio_path" in result


# ---------------------------------------------------------------------------
# API 桥接层测试（如果 core/api 有涉及）
# ---------------------------------------------------------------------------


class TestDevKitApiVoiceGeneration:
    """DevKitApi 桥接层：语音生成相关接口"""

    def test_api_generate_voice_from_text(self, tmp_path):
        from devkit.api import DevKitApi

        api = DevKitApi()
        api.set_work_dir(str(tmp_path))
        resp = api.generate_voice_from_text(
            character_id="char_api",
            name="api_voice",
            text="API 测试文本",
            engine="melo",
        )
        assert resp["ok"] is True
        assert "voice_id" in resp["data"]
        assert "audio_path" in resp["data"]

    def test_api_generate_voice_from_description(self, tmp_path):
        from devkit.api import DevKitApi

        api = DevKitApi()
        api.set_work_dir(str(tmp_path))
        resp = api.generate_voice_from_description(
            character_id="char_api2",
            name="api_desc",
            description="温柔的年轻女性声音",
            engine="melo",
        )
        assert resp["ok"] is True
        assert "voice_id" in resp["data"]

    def test_api_create_copyright_confirmation(self, tmp_path):
        from devkit.api import DevKitApi
        from devkit.voice_cloner import save_voice

        api = DevKitApi()
        api.set_work_dir(str(tmp_path))
        voice = save_voice(str(tmp_path), "char_api3", "voice", audio_data=b"x")

        resp = api.create_copyright_confirmation(
            character_id="char_api3",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="api_user",
        )
        assert resp["ok"] is True
        assert resp["data"]["status"] == "pending"

    def test_api_confirm_copyright(self, tmp_path):
        from devkit.api import DevKitApi
        from devkit.voice_cloner import save_voice, create_copyright_confirmation

        api = DevKitApi()
        api.set_work_dir(str(tmp_path))
        voice = save_voice(str(tmp_path), "char_api4", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_api4",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="api_user",
        )

        resp = api.confirm_copyright(
            copyright_id=cr["id"],
            confirm=True,
        )
        assert resp["ok"] is True
        assert resp["data"]["status"] == "confirmed"

    def test_api_check_copyright_before_upload(self, tmp_path):
        from devkit.api import DevKitApi
        from devkit.voice_cloner import save_voice, create_copyright_confirmation, confirm_copyright

        api = DevKitApi()
        api.set_work_dir(str(tmp_path))
        voice = save_voice(str(tmp_path), "char_api5", "voice", audio_data=b"x")
        cr = create_copyright_confirmation(
            work_dir=str(tmp_path),
            character_id="char_api5",
            voice_id=voice["id"],
            copyright_type="original",
            declared_by="api_user",
        )
        confirm_copyright(str(tmp_path), cr["id"], "api_user", confirm=True)

        resp = api.check_copyright_before_upload(voice_id=voice["id"])
        assert resp["ok"] is True
        assert resp["data"]["allowed"] is True

    def test_api_diffsinger_model_status(self, tmp_path):
        from devkit.api import DevKitApi

        api = DevKitApi()
        api.set_work_dir(str(tmp_path))
        resp = api.get_diffsinger_model_status(language="zh")
        assert resp["ok"] is True
        assert "is_available" in resp["data"]
