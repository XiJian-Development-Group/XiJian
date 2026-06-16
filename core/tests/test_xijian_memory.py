"""Tests for ``/v1/xijian/memory/*``."""

from __future__ import annotations


def test_memory_list_includes_seed(client, auth_headers):
    response = client.get("/v1/xijian/memory/entries", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "list"
    assert len(body["data"]) >= 3


def test_memory_create_get_patch_delete(client, auth_headers):
    payload = {
        "character_id": "char_yuki",
        "content": "用户喜欢猫",
        "tags": ["preference", "pet"],
        "attributes": {"importance": "high", "decay": "slow"},
    }
    create = client.post("/v1/xijian/memory/entries", headers=auth_headers, json=payload)
    assert create.status_code == 201
    entry_id = create.get_json()["id"]
    assert entry_id.startswith("mem_")

    get = client.get(f"/v1/xijian/memory/entries/{entry_id}", headers=auth_headers)
    assert get.status_code == 200

    patch = client.patch(
        f"/v1/xijian/memory/entries/{entry_id}",
        headers=auth_headers,
        json={"content": "用户非常喜欢猫"},
    )
    assert patch.status_code == 200
    assert patch.get_json()["content"] == "用户非常喜欢猫"

    delete = client.delete(f"/v1/xijian/memory/entries/{entry_id}", headers=auth_headers)
    assert delete.status_code == 204


def test_memory_search_returns_hits(client, auth_headers):
    response = client.post(
        "/v1/xijian/memory/search",
        headers=auth_headers,
        json={"query": "冰淇淋", "top_k": 3},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "list"
    assert len(body["data"]) >= 1


def test_memory_forget_by_decay(client, auth_headers):
    response = client.post(
        "/v1/xijian/memory/forget",
        headers=auth_headers,
        json={"decay": "fast"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["forgotten"] >= 0


def test_memory_consolidate_returns_job_id(client, auth_headers):
    response = client.post(
        "/v1/xijian/memory/consolidate",
        headers=auth_headers,
        json={"character_id": "char_yuki"},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert "job_id" in body
    assert body["status"] == "queued"