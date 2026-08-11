"""Tests for ``/v1/files`` family.
(``/v1/files`` 系列的测试。)
"""

from __future__ import annotations


def _upload(client, auth_headers, body, *, purpose="user_data", filename="hello.txt"):
    """Helper to upload a file with given purpose and filename.
    (使用给定的目的和文件名上传文件的辅助函数。)
    """
    return client.post(
        "/v1/files",
        headers=auth_headers,
        data={
            "file": (client.application.test_client_class.open_file if False else _open),
        },
    ) if False else client.post(
        "/v1/files",
        headers={**auth_headers, "Content-Type": "application/octet-stream"},
        query_string={"purpose": purpose, "filename": filename},
        data=body,
    )


def test_file_upload_list_get_content_delete(client, auth_headers):
    """Full lifecycle of file upload → list → get → content → delete.
    (文件上传 → 列表 → 获取 → 内容 → 删除的完整生命周期。)
    """
    payload = b"hello, world\n"

    upload = client.post(
        "/v1/files",
        headers={**auth_headers, "Content-Type": "application/octet-stream"},
        query_string={"purpose": "user_data", "filename": "hello.txt"},
        data=payload,
    )
    assert upload.status_code == 201
    file_id = upload.get_json()["id"]

    listing = client.get("/v1/files", headers=auth_headers)
    assert listing.status_code == 200
    assert any(it["id"] == file_id for it in listing.get_json()["data"])

    one = client.get(f"/v1/files/{file_id}", headers=auth_headers)
    assert one.status_code == 200
    assert one.get_json()["id"] == file_id

    content = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)
    assert content.status_code == 200
    assert content.data == payload
    assert "Content-Disposition" in content.headers

    delete = client.delete(f"/v1/files/{file_id}", headers=auth_headers)
    assert delete.status_code == 204

    missing = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)
    assert missing.status_code == 404


def test_file_upload_invalid_purpose_returns_400(client, auth_headers):
    """Upload with an invalid purpose returns 400.
    (使用无效目的上传返回 400。)
    """
    response = client.post(
        "/v1/files",
        headers={**auth_headers, "Content-Type": "application/octet-stream"},
        query_string={"purpose": "nope", "filename": "x.bin"},
        data=b"abc",
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_purpose"


# ---------------------------------------------------------------------------
# S6 — request body size limit (413)
# S6 — 请求体大小限制 (413)
# ---------------------------------------------------------------------------


def test_upload_exceeding_max_content_length_returns_413(app, auth_headers):
    """Oversized uploads get a clean JSON 413, not a 500 (S6)."""
    # Build a throwaway app with a tiny cap; the module-level token is
    # already set by the session fixture, so setup_token short-circuits
    # and the new app shares the same Bearer token.
    from xijian_api.app import create_app

    tiny = create_app(testing=True)
    try:
        tiny.config["MAX_CONTENT_LENGTH"] = 16 * 1024
        client = tiny.test_client()

        resp = client.post(
            "/v1/files",
            headers=auth_headers,
            data=b"x" * (32 * 1024),
            content_type="application/octet-stream",
        )
        assert resp.status_code == 413
        err = resp.get_json()["error"]
        assert err["code"] == "request_entity_too_large"
        assert err["type"] == "invalid_request_error"
    finally:
        tiny.config.pop("MAX_CONTENT_LENGTH", None)


def test_upload_within_limit_still_works(client, auth_headers):
    """A normal-size upload is unaffected by MAX_CONTENT_LENGTH (S6)."""
    resp = client.post(
        "/v1/files",
        headers=auth_headers,
        data=b"hello world",
        content_type="application/octet-stream",
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["bytes"] == 11
    assert body["object"] == "file"


def test_multipart_upload_streams_via_temp_file(client, auth_headers):
    """Multipart upload still lands correctly via the streaming path (S6)."""
    import io

    resp = client.post(
        "/v1/files",
        headers=auth_headers,
        data={"file": (io.BytesIO(b"streamed-bytes"), "clip.bin"), "purpose": "user_data"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    file_id = resp.get_json()["id"]
    assert resp.get_json()["filename"] == "clip.bin"

    content = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)
    assert content.status_code == 200
    assert content.data == b"streamed-bytes"
