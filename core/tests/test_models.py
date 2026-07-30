"""Tests for ``/v1/models`` family.
(``/v1/models`` 系列的测试。)
"""

from __future__ import annotations

import time


def test_models_list_includes_seeded(client, auth_headers):
    """List models includes all seeded models.
    (列出模型包含所有已播种的模型。)
    """
    response = client.get("/v1/models", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert "qwen2.5-7b-mlx-4bit" in ids
    assert "qwen2.5-14b-mlx-4bit" in ids
    assert "qwen2.5-7b-gguf-q4km" in ids


def test_model_get_returns_one(client, auth_headers):
    """Get model by id returns the model details.
    (按 id 获取模型返回模型详情。)
    """
    response = client.get("/v1/models/qwen2.5-7b-mlx-4bit", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == "qwen2.5-7b-mlx-4bit"
    # The dev/test config.toml registers the model with ``backend =
    # "mock"`` so the load route can run on any host without mlx or
    # llama_cpp installed.  The field is still surfaced verbatim
    # under ``xijian.backend`` — production deploys flip it back to
    # ``"mlx"`` / ``"gguf"``.
    # (开发/测试 config.toml 使用 ``backend = "mock"`` 注册模型，
    # 这样加载路由可以在任何没有安装 mlx 或 llama_cpp 的主机上运行。
    # 该字段仍在 ``xijian.backend`` 下逐字显示 — 生产部署将其改回
    # ``"mlx"`` / ``"gguf"``。)
    assert body["xijian"]["backend"] == "mock"


def test_model_get_unknown_returns_404(client, auth_headers):
    """Get non-existent model returns 404.
    (获取不存在的模型返回 404。)
    """
    response = client.get("/v1/models/does-not-exist", headers=auth_headers)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "model_not_found"


def test_model_load_returns_202(client, auth_headers):
    """Loading a model returns 202 with progress tracking.
    (加载模型返回 202 并带有进度追踪。)
    """
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
    # (轮询进度 URL。)
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
    """Unloading a model returns 200.
    (卸载模型返回 200。)
    """
    response = client.post(
        "/v1/models/qwen2.5-7b-mlx-4bit/unload",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "model.unload"


def test_operation_unknown_returns_404(client, auth_headers):
    """Querying unknown operation returns 404.
    (查询未知操作返回 404。)
    """
    response = client.get("/v1/models/operations/load_op_does_not_exist", headers=auth_headers)
    assert response.status_code == 404
