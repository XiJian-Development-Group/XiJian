"""Tests for ``/v1/files`` family."""

from __future__ import annotations


def _upload(client, auth_headers, body, *, purpose="user_data", filename="hello.txt"):
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
    response = client.post(
        "/v1/files",
        headers={**auth_headers, "Content-Type": "application/octet-stream"},
        query_string={"purpose": "nope", "filename": "x.bin"},
        data=b"abc",
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_purpose"