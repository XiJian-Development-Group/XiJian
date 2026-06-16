"""Tests for ``/v1/models`` family."""

from __future__ import annotations

import time


def test_models_list_includes_seeded(client, auth_headers):
    response = client.get("/v1/models", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert "qwen2.5-7b-mlx-4bit" in ids
    assert "qwen2.5-14b-mlx-4bit" in ids
    assert "qwen2.5-7b-gguf-q4km" in ids


def test_model_get_returns_one(client, auth_headers):
    response = client.get("/v1/models/qwen2.5-7b-mlx-4bit", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == "qwen2.5-7b-mlx-4bit"
    assert body["xijian"]["backend"] == "mlx"


def test_model_get_unknown_returns_404(client, auth_headers):
    response = client.get("/v1/models/does-not-exist", headers=auth_headers)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "model_not_found"


def test_model_load_returns_202(client, auth_headers):
    response = client.post(
        "/v1/models/qwen2.5-14b-mlx-4bit/load",
        headers=auth_headers,
        json={"gpu_layers": -1, "context_length": 8192},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["object"] == "model.load"
    assert body["status"] == "loading"
    assert body["progress_url"].startswith("/v1/models/operations/")
    op_id = body["id"]
    # Poll the progress URL.
    deadline = time.time() + 2
    final = body
    while time.time() < deadline:
        poll = client.get(body["progress_url"], headers=auth_headers)
        assert poll.status_code == 200
        final = poll.get_json()
        if final.get("status") in {"loaded", "unloaded"}:
            break
        time.sleep(0.05)
    assert final.get("status") in {"loaded", "unloading"}


def test_model_unload_returns_200(client, auth_headers):
    response = client.post(
        "/v1/models/qwen2.5-7b-mlx-4bit/unload",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "model.unload"


def test_operation_unknown_returns_404(client, auth_headers):
    response = client.get("/v1/models/operations/load_op_does_not_exist", headers=auth_headers)
    assert response.status_code == 404