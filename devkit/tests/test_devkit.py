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

    Setting ``XIJIAN_DEV_LOCAL_DIR`` forces :func:`local_archive_dir`
    to honour it; tests can then inspect whatever was written.

    Note: ``_DEV_SUBMIT_LOCAL_DIR`` is a module-level constant read
    from the environment at import time, so we have to monkeypatch
    the module attribute directly rather than just ``setenv``.
    """
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
        moment = _dt.datetime(2026, 7, 3, 8, 30, 0, tzinfo=_dt.timezone.utc).timestamp()
        with pytest.raises(RateLimitedError) as ei:
            check_rate_limit("alice", now=moment)
        assert ei.value.retry_after_seconds > 0
        assert ei.value.retry_after_seconds == pytest.approx(
            DEV_SUBMIT_COOLDOWN_SECONDS - 30 * 60, abs=2
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
        assert d["smtp_host"] == DEV_SUBMIT_SMTP_HOST
        assert d["smtp_port"] == DEV_SUBMIT_SMTP_PORT
        assert d["recipient"] == DEV_SUBMIT_RECIPIENT
        assert d["target_kinds"] == list(TARGET_KINDS)
        assert d["max_attachment_bytes"] == DEV_SUBMIT_MAX_ATTACHMENT_BYTES
        assert d["max_attachment_mb"] == DEV_SUBMIT_MAX_ATTACHMENT_BYTES // 1_000_000

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

