"""Tests for the video routes — numeric parameter parsing & range clamps (E1).

视频路由测试 — 数值参数解析与范围钳制 (E1)。

The four bare ``int()`` call sites (video_understanding multipart/JSON,
submit_generation, remix_video) now go through
:func:`xijian_api.utils.params.parse_int_range`:

* non-numeric values → 400 ``invalid_numeric_value`` (no more 500);
* out-of-range values → 400 ``invalid_numeric_value`` with ``param``;
* NaN/Infinity floats → 400.
"""

from __future__ import annotations

import io

import pytest

from xijian_api.utils.params import parse_int_range
from xijian_api.errors import ApiError


# ---------------------------------------------------------------------------
# Unit level — parse_int_range
# ---------------------------------------------------------------------------


def test_parse_int_range_defaults_when_missing():
    assert parse_int_range(None, "fps", 24, 1, 120) == 24
    assert parse_int_range("", "fps", 24, 1, 120) == 24


def test_parse_int_range_accepts_in_range_values():
    assert parse_int_range(1, "fps", 24, 1, 120) == 1
    assert parse_int_range(120, "fps", 24, 1, 120) == 120
    assert parse_int_range("30", "fps", 24, 1, 120) == 30


@pytest.mark.parametrize("bad", ["abc", "12.5x", float("nan"), float("inf"), True, [1, 2]])
def test_parse_int_range_rejects_non_numeric(bad):
    with pytest.raises(ApiError) as ei:
        parse_int_range(bad, "fps", 24, 1, 120)
    assert ei.value.status == 400
    assert ei.value.code == "invalid_numeric_value"
    assert ei.value.param == "fps"


@pytest.mark.parametrize("bad", [-1, 0, 121, 10**9])
def test_parse_int_range_rejects_out_of_range(bad):
    with pytest.raises(ApiError) as ei:
        parse_int_range(bad, "fps", 24, 1, 120)
    assert ei.value.status == 400
    assert ei.value.code == "invalid_numeric_value"
    assert ei.value.param == "fps"
    assert "between 1 and 120" in ei.value.message


# ---------------------------------------------------------------------------
# HTTP level — POST /v1/videos/understanding (JSON branch)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _noop_video_submit(monkeypatch):
    """The stub video backend is unavailable in the test env (503); make
    ``video_stub.submit`` a no-op so the 202 paths are testable.

    测试环境中存根视频后端不可用 (503)；把 ``video_stub.submit``
    变成空操作，使 202 路径可测。
    """
    from xijian_api.stubs import video as video_stub_module

    def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(video_stub_module, "submit", _noop)


def _understanding_json(client, auth_headers, **overrides):
    payload = {"video": "file:///tmp/clip.mp4"}
    payload.update(overrides)
    return client.post("/v1/videos/understanding", headers=auth_headers, json=payload)


def test_understanding_json_fps_non_numeric_400(client, auth_headers):
    resp = _understanding_json(client, auth_headers, fps="abc")
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["code"] == "invalid_numeric_value"
    assert err["param"] == "fps"


def test_understanding_json_fps_nan_400(client, auth_headers):
    import math
    resp = _understanding_json(client, auth_headers, fps=math.nan)
    assert resp.status_code == 400
    assert resp.get_json()["error"]["param"] == "fps"


def test_understanding_json_fps_out_of_range_400(client, auth_headers):
    resp = _understanding_json(client, auth_headers, fps=-1)
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["param"] == "fps"
    assert "between 1 and 120" in err["message"]


def test_understanding_json_max_frames_out_of_range_400(client, auth_headers):
    resp = _understanding_json(client, auth_headers, max_frames=99999)
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["param"] == "max_frames"
    assert "between 1 and 10000" in err["message"]


def test_understanding_json_valid_values_200(client, auth_headers):
    resp = _understanding_json(client, auth_headers, fps=30, max_frames=5)
    assert resp.status_code == 200
    assert resp.get_json()["object"] == "video.understanding"


# ---------------------------------------------------------------------------
# HTTP level — POST /v1/videos/understanding (multipart branch)
# ---------------------------------------------------------------------------


def _understanding_multipart(client, auth_headers, **fields):
    data = {"video": (io.BytesIO(b"fake-video-bytes"), "clip.mp4")}
    data.update(fields)
    return client.post(
        "/v1/videos/understanding",
        headers=auth_headers,
        data=data,
        content_type="multipart/form-data",
    )


def test_understanding_multipart_fps_out_of_range_400(client, auth_headers):
    resp = _understanding_multipart(client, auth_headers, fps="500")
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["code"] == "invalid_numeric_value"
    assert err["param"] == "fps"


def test_understanding_multipart_max_frames_non_numeric_400(client, auth_headers):
    resp = _understanding_multipart(client, auth_headers, max_frames="lots")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["param"] == "max_frames"


def test_understanding_multipart_valid_200(client, auth_headers):
    resp = _understanding_multipart(client, auth_headers, fps="30", max_frames="3")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# HTTP level — POST /v1/videos/generations
# ---------------------------------------------------------------------------


def test_submit_generation_seconds_out_of_range_400(client, auth_headers):
    resp = client.post(
        "/v1/videos/generations",
        headers=auth_headers,
        json={"prompt": "sunset", "seconds": 99999},
    )
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["code"] == "invalid_numeric_value"
    assert err["param"] == "seconds"
    assert "between 1 and 3600" in err["message"]


def test_submit_generation_fps_out_of_range_400(client, auth_headers):
    resp = client.post(
        "/v1/videos/generations",
        headers=auth_headers,
        json={"prompt": "sunset", "fps": 0},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["param"] == "fps"


def test_submit_generation_seconds_non_numeric_400(client, auth_headers):
    resp = client.post(
        "/v1/videos/generations",
        headers=auth_headers,
        json={"prompt": "sunset", "seconds": "three"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["param"] == "seconds"


def test_submit_generation_valid_202(client, auth_headers):
    resp = client.post(
        "/v1/videos/generations",
        headers=auth_headers,
        json={"prompt": "sunset", "seconds": 10, "fps": 30},
    )
    assert resp.status_code == 202
    body = resp.get_json()
    assert body["seconds"] == 10
    assert body["fps"] == 30


# ---------------------------------------------------------------------------
# HTTP level — POST /v1/videos/<id>/remix
# ---------------------------------------------------------------------------


def _create_parent(client, auth_headers, **overrides) -> str:
    payload = {"prompt": "parent clip", "seconds": 4, "fps": 24}
    payload.update(overrides)
    resp = client.post("/v1/videos/generations", headers=auth_headers, json=payload)
    assert resp.status_code == 202
    return resp.get_json()["id"]


def test_remix_seconds_out_of_range_400(client, auth_headers):
    video_id = _create_parent(client, auth_headers)
    resp = client.post(
        f"/v1/videos/{video_id}/remix",
        headers=auth_headers,
        json={"seconds": 5000},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["param"] == "seconds"


def test_remix_fps_non_numeric_400(client, auth_headers):
    video_id = _create_parent(client, auth_headers)
    resp = client.post(
        f"/v1/videos/{video_id}/remix",
        headers=auth_headers,
        json={"fps": "high"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["param"] == "fps"


def test_remix_valid_202(client, auth_headers):
    video_id = _create_parent(client, auth_headers)
    resp = client.post(
        f"/v1/videos/{video_id}/remix",
        headers=auth_headers,
        json={"seconds": 8, "fps": 60},
    )
    assert resp.status_code == 202
    body = resp.get_json()
    assert body["seconds"] == 8
    assert body["fps"] == 60
