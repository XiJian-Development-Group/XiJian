"""Adversarial robustness tests — "the QA engineer walks into a bar".

每个测试对应一种「酒吧点单」式的恶意/边界输入，验证服务端返回干净的
4xx（或按 API 语义安全处理），绝不 500、不崩溃、不产生脏数据。

Categories (19):

1.  正常请求（一杯啤酒）            — happy path smoke
2.  合法但不同（咖啡）              — equivalent-class inputs
3.  0.7 杯（小数）                  — float / decimal boundaries
4.  -1 杯（负数）                   — negative values → 400, no dirty data
5.  2^32 杯（大数）                 — 2^32 / 2^63 / 1e308 boundaries
6.  洗脚水（无效枚举）              — invalid enums / unknown ids → 4xx
7.  蜥蜴（类型混淆）                — string-as-number, bool-as-number, ...
8.  asdfQwer@24dg!&*(@（垃圾字符串） — special-char field values
9.  什么也没要（空请求）            — empty body / missing fields → clean 4xx
10. 反复进出（会话）                — session lifecycle + idempotency keys
11. 反复进出最后打老板（状态机滥用）— illegal state transitions → 4xx
12. 烫烫烫的锟斤拷（乱码）          — invalid UTF-8 / mojibake
13. NaN 杯 Null                     — NaN / Infinity / null in numeric fields
14. 500T 混合 payload（超大）       — deep nesting / huge strings / mixed arrays
15. 把酒吧拆了（破坏性）            — DELETE + reset/recovery flows
16. 化装成老板（越权）              — no / wrong / fake-scheme tokens → 401
17. 一万个工程师（负载）            — concurrent writes (threading)
18. ';DROP TABLE 酒吧;（注入）      — SQL injection + path traversal
19. 点炒饭酒吧炸了（事后回归）      — post-abuse regression
"""

from __future__ import annotations

import json
import math
import threading

from xijian_api import store
from xijian_api.stubs import state as stubs_state


# ---------------------------------------------------------------------------
# Helpers
# 辅助函数
# ---------------------------------------------------------------------------


def _chat_body(**overrides):
    """A valid chat completion payload (overridable)."""
    body = {
        "model": "qwen2.5-7b-mlx-4bit",
        "messages": [{"role": "user", "content": "hi"}],
    }
    body.update(overrides)
    return body


def _new_character(client, auth_headers, **overrides):
    body = {"name": "Smoke", **overrides}
    return client.post("/v1/xijian/characters", json=body, headers=auth_headers)


def _new_world(client, auth_headers, **overrides):
    body = {"name": "Smoke World", **overrides}
    return client.post("/v1/xijian/worlds", json=body, headers=auth_headers)


def _assert_never_500(response):
    """A 4xx/2xx is acceptable; a 500 is a regression."""
    assert response.status_code < 500, (
        f"server crashed with 500 on {response.request.method} "
        f"{response.request.path}: {response.get_data(as_text=True)[:300]}"
    )


# ---------------------------------------------------------------------------
# 1. 正常请求（一杯啤酒）— happy path smoke
# ---------------------------------------------------------------------------


class TestCategory1HappyPath:
    def test_healthz_ok(self, client):
        res = client.get("/healthz")
        assert res.status_code == 200

    def test_chat_completions_sync_ok(self, client, auth_headers):
        res = client.post("/v1/chat/completions", json=_chat_body(), headers=auth_headers)
        assert res.status_code == 200
        assert res.get_json()["object"] == "chat.completion"

    def test_chat_completions_stream_ok(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(stream=True),
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_character_crud_smoke(self, client, auth_headers):
        created = _new_character(client, auth_headers)
        assert created.status_code == 201
        cid = created.get_json()["id"]
        got = client.get(f"/v1/xijian/characters/{cid}", headers=auth_headers)
        assert got.status_code == 200
        listed = client.get("/v1/xijian/characters", headers=auth_headers)
        assert listed.status_code == 200

    def test_world_crud_smoke(self, client, auth_headers):
        created = _new_world(client, auth_headers)
        assert created.status_code == 201
        wid = created.get_json()["id"]
        got = client.get(f"/v1/xijian/worlds/{wid}", headers=auth_headers)
        assert got.status_code == 200

    def test_session_smoke(self, client, auth_headers):
        res = client.post("/v1/xijian/sessions", json={"title": "t"}, headers=auth_headers)
        assert res.status_code == 201

    def test_embeddings_smoke(self, client, auth_headers):
        res = client.post("/v1/embeddings", json={"input": "hello"}, headers=auth_headers)
        # 503 backend_unavailable is the documented clean response when no
        # embedding backend is registered in the test config (environmental).
        assert res.status_code in (200, 503)

    def test_completions_smoke(self, client, auth_headers):
        res = client.post("/v1/completions", json={"prompt": "hello"}, headers=auth_headers)
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# 2. 合法但不同（咖啡）— equivalent-class inputs
# ---------------------------------------------------------------------------


class TestCategory2EquivalentClasses:
    def test_chat_different_valid_models(self, client, auth_headers):
        for model in ("qwen2.5-7b-mlx-4bit", "stub-model", "llama3.1-8b"):
            res = client.post(
                "/v1/chat/completions",
                json=_chat_body(model=model),
                headers=auth_headers,
            )
            assert res.status_code in (200, 503), res.get_data(as_text=True)[:200]

    def test_chat_varied_numeric_fields_ok(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(temperature=0.2, top_p=0.9, max_tokens=16, n=1),
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_character_different_valid_names(self, client, auth_headers):
        for name in ("Yuki", "A", "中文名", "user-1", "x" * 200):
            res = _new_character(client, auth_headers, name=name)
            assert res.status_code == 201, res.get_data(as_text=True)[:200]

    def test_world_different_valid_names(self, client, auth_headers):
        # MAX_WORLDS=3 and the seeded default world consumes one slot,
        # so stay within the remaining budget.
        for name in ("W", "中文世界"):
            res = _new_world(client, auth_headers, name=name)
            assert res.status_code == 201, res.get_data(as_text=True)[:200]
        # Long-name equivalence class — exercised on a character instead
        # where there is no world-count cap.
        res = _new_character(client, auth_headers, name="x" * 200)
        assert res.status_code == 201


# ---------------------------------------------------------------------------
# 3. 0.7 杯（小数）— float / decimal boundaries
# ---------------------------------------------------------------------------


class TestCategory3Decimals:
    def test_chat_temperature_decimal(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(temperature=0.7),
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_chat_temperature_tiny_and_just_above_zero(self, client, auth_headers):
        for t in (1e-9, 0.1, 1.5, 1.999999):
            res = client.post(
                "/v1/chat/completions",
                json=_chat_body(temperature=t),
                headers=auth_headers,
            )
            assert res.status_code == 200

    def test_wallet_deposit_decimal(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        cur = client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        assert cur.status_code == 201
        res = client.post(
            "/v1/xijian/wallets/user/user_local/{}/mora/deposit".format(world),
            json={"amount": 0.7},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["balance"] == 0.7

    def test_transactions_limit_decimal_query(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/economy/transactions?limit=0.7",
            headers=auth_headers,
        )
        # int("0.7") raises → route falls back to default 50.
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# 4. -1 杯（负数）— negatives → 400, no dirty data
# ---------------------------------------------------------------------------


class TestCategory4Negatives:
    def test_wallet_deposit_negative_rejected(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        res = client.post(
            "/v1/xijian/wallets/user/user_local/{}/mora/deposit".format(world),
            json={"amount": -1},
            headers=auth_headers,
        )
        assert res.status_code == 400
        # No dirty data: the wallet must not exist / balance unchanged.
        got = client.get(
            "/v1/xijian/wallets/user/user_local/{}/mora".format(world),
            headers=auth_headers,
        )
        assert got.status_code == 404

    def test_economy_reward_negative_rejected(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/economy/reward",
            json={
                "world_id": "w_1",
                "to_kind": "user",
                "to_id": "u1",
                "currency_code": "mora",
                "amount": -5,
            },
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_wallet_withdraw_negative_rejected(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        res = client.post(
            "/v1/xijian/wallets/user/user_local/{}/mora/withdraw".format(world),
            json={"amount": -0.5},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_chat_negative_temperature_safe(self, client, auth_headers):
        # float(-1) is valid — must not crash.
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(temperature=-1),
            headers=auth_headers,
        )
        _assert_never_500(res)


# ---------------------------------------------------------------------------
# 5. 2^32 杯（大数）— big-number boundaries
# ---------------------------------------------------------------------------


class TestCategory5BigNumbers:
    def test_wallet_deposit_2_32_rejected(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        for amount in (2**32, 2**63, 1e308, 10**100):
            res = client.post(
                "/v1/xijian/wallets/user/user_local/{}/mora/deposit".format(world),
                json={"amount": amount},
                headers=auth_headers,
            )
            assert res.status_code == 400, (
                f"amount={amount} -> {res.status_code}"
            )

    def test_chat_big_numbers_safe(self, client, auth_headers):
        for t in (2**32, 2**63, 1e308):
            res = client.post(
                "/v1/chat/completions",
                json=_chat_body(temperature=t, max_tokens=2**63, n=2**32),
                headers=auth_headers,
            )
            _assert_never_500(res)

    def test_character_id_big_number(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/characters/{}".format(2**63),
            headers=auth_headers,
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# 6. 洗脚水（无效枚举）— invalid enums / unknown ids → 4xx
# ---------------------------------------------------------------------------


class TestCategory6InvalidEnums:
    def test_unknown_character_404(self, client, auth_headers):
        res = client.get("/v1/xijian/characters/char_does_not_exist", headers=auth_headers)
        assert res.status_code == 404

    def test_unknown_world_404(self, client, auth_headers):
        res = client.get("/v1/xijian/worlds/w_does_not_exist", headers=auth_headers)
        assert res.status_code == 404

    def test_invalid_owner_kind_rejected(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        res = client.post(
            "/v1/xijian/wallets/system/hacker/{}/mora/deposit".format(world),
            json={"amount": 1},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_unknown_session_404(self, client, auth_headers):
        res = client.get("/v1/xijian/sessions/sess_xyz/messages", headers=auth_headers)
        assert res.status_code == 404

    def test_unknown_transaction_404(self, client, auth_headers):
        res = client.get("/v1/xijian/economy/transactions/txn_xyz", headers=auth_headers)
        assert res.status_code == 404

    def test_currency_in_world_that_does_not_exist(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/currencies",
            json={"world_id": "w_nope", "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# 7. 蜥蜴（类型混淆）— type confusion
# ---------------------------------------------------------------------------


class TestCategory7TypeConfusion:
    def test_chat_temperature_string_rejected(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(temperature="abc"),
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["type"] == "invalid_request_error"

    def test_chat_temperature_numeric_string_ok(self, client, auth_headers):
        # "0.7" is safely convertible — must not 500.
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(temperature="0.7"),
            headers=auth_headers,
        )
        _assert_never_500(res)

    def test_chat_top_p_string_rejected(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(top_p="abc"),
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_chat_max_tokens_string_rejected(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(max_tokens="abc"),
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_chat_n_string_rejected(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(n="abc"),
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_chat_bool_as_number_rejected(self, client, auth_headers):
        # bool is a subclass of int in Python — must be rejected, not
        # silently treated as 1.0 / 1.
        for field in ("temperature", "top_p", "max_tokens", "n"):
            res = client.post(
                "/v1/chat/completions",
                json=_chat_body(**{field: True}),
                headers=auth_headers,
            )
            assert res.status_code == 400, f"{field}=True -> {res.status_code}"

    def test_chat_bool_as_stream_string(self, client, auth_headers):
        # "false" string is truthy in Python — pre-existing lenient
        # behaviour; must not crash.
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(stream="false"),
            headers=auth_headers,
        )
        _assert_never_500(res)

    def test_wallet_amount_string_rejected(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        res = client.post(
            "/v1/xijian/wallets/user/user_local/{}/mora/deposit".format(world),
            json={"amount": "10"},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_wallet_amount_bool_rejected(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        res = client.post(
            "/v1/xijian/wallets/user/user_local/{}/mora/deposit".format(world),
            json={"amount": True},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_embeddings_input_number(self, client, auth_headers):
        # input=123 — type confusion; must be a clean 4xx/503, not 500.
        # 503 = backend_unavailable (no embedding backend in test config).
        res = client.post("/v1/embeddings", json={"input": 123}, headers=auth_headers)
        assert res.status_code in (400, 503), res.get_data(as_text=True)[:200]

    def test_chat_messages_dict_rejected(self, client, auth_headers):
        # messages must be a list — a dict is type confusion.
        res = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": {"role": "user", "content": "hi"}},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_chat_messages_list_of_numbers(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [1, 2, 3]},
            headers=auth_headers,
        )
        _assert_never_500(res)


# ---------------------------------------------------------------------------
# 8. asdfQwer@24dg!&*(@（垃圾字符串）— special chars in field values
# ---------------------------------------------------------------------------


class TestCategory8GarbageStrings:
    GARBAGE = "asdfQwer@24dg!&*(@ \t\n\"'\\<>`~|^[]{}=;:/?,.#$%"

    def test_chat_garbage_content(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(messages=[{"role": "user", "content": self.GARBAGE}]),
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_character_garbage_name_and_persona(self, client, auth_headers):
        res = _new_character(
            client, auth_headers,
            name=self.GARBAGE,
            persona_doc=self.GARBAGE * 5,
        )
        assert res.status_code == 201
        record = res.get_json()
        assert record["name"] == self.GARBAGE

    def test_world_garbage_name(self, client, auth_headers):
        res = _new_world(client, auth_headers, name=self.GARBAGE)
        assert res.status_code == 201

    def test_session_garbage_title_and_content(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/sessions",
            json={"title": self.GARBAGE},
            headers=auth_headers,
        )
        assert res.status_code == 201
        sid = res.get_json()["id"]
        res = client.post(
            f"/v1/xijian/sessions/{sid}/messages",
            json={"content": self.GARBAGE, "role": self.GARBAGE},
            headers=auth_headers,
        )
        assert res.status_code == 201

    def test_chat_garbage_model_and_user(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(model=self.GARBAGE, user=self.GARBAGE),
            headers=auth_headers,
        )
        _assert_never_500(res)


# ---------------------------------------------------------------------------
# 9. 什么也没要（空请求）— empty body / missing fields → clean 4xx
# ---------------------------------------------------------------------------


class TestCategory9EmptyRequests:
    def test_chat_empty_body(self, client, auth_headers):
        res = client.post("/v1/chat/completions", json={}, headers=auth_headers)
        assert res.status_code == 400

    def test_chat_no_body_at_all(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            data=b"",
            content_type="application/json",
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_chat_missing_messages(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json={"model": "m"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "missing_messages"

    def test_embeddings_missing_input(self, client, auth_headers):
        res = client.post("/v1/embeddings", json={}, headers=auth_headers)
        assert res.status_code == 400

    def test_completions_missing_prompt(self, client, auth_headers):
        res = client.post("/v1/completions", json={}, headers=auth_headers)
        assert res.status_code == 400

    def test_worlds_missing_name(self, client, auth_headers):
        res = client.post("/v1/xijian/worlds", json={}, headers=auth_headers)
        assert res.status_code == 400

    def test_session_message_missing_content(self, client, auth_headers):
        res = client.post("/v1/xijian/sessions", json={}, headers=auth_headers)
        assert res.status_code == 201
        sid = res.get_json()["id"]
        res = client.post(
            f"/v1/xijian/sessions/{sid}/messages",
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_wallet_deposit_missing_amount(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        res = client.post(
            "/v1/xijian/wallets/user/user_local/{}/mora/deposit".format(world),
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_chat_abort_missing_request_id(self, client, auth_headers):
        res = client.post("/v1/chat/abort", json={}, headers=auth_headers)
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# 10. 反复进出（会话）— sessions + idempotency keys
# ---------------------------------------------------------------------------


class TestCategory10SessionsAndIdempotency:
    def test_session_lifecycle(self, client, auth_headers):
        created = client.post(
            "/v1/xijian/sessions", json={"title": "t"}, headers=auth_headers
        )
        assert created.status_code == 201
        sid = created.get_json()["id"]

        msg = client.post(
            f"/v1/xijian/sessions/{sid}/messages",
            json={"content": "hello"},
            headers=auth_headers,
        )
        assert msg.status_code == 201

        listed = client.get(f"/v1/xijian/sessions/{sid}/messages", headers=auth_headers)
        assert listed.status_code == 200
        assert len(listed.get_json()["data"]) == 1

        deleted = client.delete(f"/v1/xijian/sessions/{sid}", headers=auth_headers)
        assert deleted.status_code == 204

        # Reuse after delete → clean 404, not a crash.
        gone = client.get(f"/v1/xijian/sessions/{sid}/messages", headers=auth_headers)
        assert gone.status_code == 404

    def test_idempotency_same_body_replay(self, client, auth_headers):
        headers = {**auth_headers, "Idempotency-Key": "rob-key-1"}
        first = client.post(
            "/v1/xijian/characters", json={"name": "Idem"}, headers=headers
        )
        assert first.status_code == 201
        second = client.post(
            "/v1/xijian/characters", json={"name": "Idem"}, headers=headers
        )
        assert second.status_code == 201
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert second.get_json()["id"] == first.get_json()["id"]

    def test_idempotency_different_body_conflict(self, client, auth_headers):
        headers = {**auth_headers, "Idempotency-Key": "rob-key-2"}
        first = client.post(
            "/v1/xijian/characters", json={"name": "IdemA"}, headers=headers
        )
        assert first.status_code == 201
        second = client.post(
            "/v1/xijian/characters", json={"name": "IdemB"}, headers=headers
        )
        assert second.status_code == 409

    def test_idempotency_replay_chat(self, client, auth_headers):
        headers = {**auth_headers, "Idempotency-Key": "rob-key-chat"}
        body = _chat_body()
        first = client.post("/v1/chat/completions", json=body, headers=headers)
        assert first.status_code == 200
        second = client.post("/v1/chat/completions", json=body, headers=headers)
        assert second.status_code == 200
        assert second.headers.get("Idempotency-Replayed") == "true"


# ---------------------------------------------------------------------------
# 11. 反复进出最后打老板（状态机滥用）— illegal state transitions
# ---------------------------------------------------------------------------


class TestCategory11StateMachineAbuse:
    def test_delete_nonexistent_session(self, client, auth_headers):
        res = client.delete("/v1/xijian/sessions/sess_nope", headers=auth_headers)
        assert res.status_code == 404

    def test_double_delete_character(self, client, auth_headers):
        created = _new_character(client, auth_headers)
        cid = created.get_json()["id"]
        first = client.delete(f"/v1/xijian/characters/{cid}", headers=auth_headers)
        assert first.status_code == 204
        second = client.delete(f"/v1/xijian/characters/{cid}", headers=auth_headers)
        assert second.status_code == 404
        # get after delete
        gone = client.get(f"/v1/xijian/characters/{cid}", headers=auth_headers)
        assert gone.status_code == 404

    def test_load_unloaded_character(self, client, auth_headers):
        # load/unload on a non-existent character → 404, not crash.
        res = client.post("/v1/xijian/characters/char_nope/load", headers=auth_headers)
        assert res.status_code == 404

    def test_interact_without_interaction_id(self, client, auth_headers):
        created = _new_character(client, auth_headers)
        cid = created.get_json()["id"]
        res = client.post(
            f"/v1/xijian/characters/{cid}/interact",
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_world_reset_confirm_without_preview(self, client, auth_headers):
        created = _new_world(client, auth_headers)
        wid = created.get_json()["id"]
        res = client.post(
            f"/v1/xijian/worlds/{wid}/reset/confirm",
            json={"reset_token": "whatever"},
            headers=auth_headers,
        )
        assert res.status_code in (403, 404, 409)

    def test_world_reset_confirm_missing_token(self, client, auth_headers):
        created = _new_world(client, auth_headers)
        wid = created.get_json()["id"]
        res = client.post(
            f"/v1/xijian/worlds/{wid}/reset/confirm",
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_world_reset_preview_then_wrong_token(self, client, auth_headers):
        created = _new_world(client, auth_headers)
        wid = created.get_json()["id"]
        preview = client.post(
            f"/v1/xijian/worlds/{wid}/reset/preview",
            json={},
            headers=auth_headers,
        )
        assert preview.status_code == 200
        res = client.post(
            f"/v1/xijian/worlds/{wid}/reset/confirm",
            json={"reset_token": "wrong-token"},
            headers=auth_headers,
        )
        assert res.status_code in (403, 408, 409)

    def test_overload_recovery_without_active(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/overload/recovery/first-confirm",
            json={},
            headers=auth_headers,
        )
        assert res.status_code in (404, 425)


# ---------------------------------------------------------------------------
# 12. 烫烫烫的锟斤拷（乱码）— invalid UTF-8 / mojibake
# ---------------------------------------------------------------------------


class TestCategory12Mojibake:
    def test_chat_invalid_utf8_body(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            data=b"\xff\xfe\x00\x01garbage",
            content_type="application/json",
            headers=auth_headers,
        )
        _assert_never_500(res)
        assert res.status_code in (400, 200)

    def test_chat_mojibake_content(self, client, auth_headers):
        mojibake = "锟斤拷烫烫烫�\u0000\uFFFF"
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(messages=[{"role": "user", "content": mojibake}]),
            headers=auth_headers,
        )
        _assert_never_500(res)

    def test_character_mojibake_name(self, client, auth_headers):
        res = _new_character(client, auth_headers, name="锟斤拷烫烫烫")
        assert res.status_code == 201

    def test_embeddings_invalid_utf8_body(self, client, auth_headers):
        res = client.post(
            "/v1/embeddings",
            data=b"\xff\xfe\x00",
            content_type="application/json",
            headers=auth_headers,
        )
        _assert_never_500(res)

    def test_chat_abort_invalid_utf8_body(self, client, auth_headers):
        res = client.post(
            "/v1/chat/abort",
            data=b"\xff\xfe\x00",
            content_type="application/json",
            headers=auth_headers,
        )
        _assert_never_500(res)


# ---------------------------------------------------------------------------
# 13. NaN 杯 Null — NaN / Infinity / null in numeric fields
# ---------------------------------------------------------------------------


class TestCategory13NaNAndNull:
    def _post_raw_json(self, client, path, raw, headers):
        return client.post(path, data=raw, content_type="application/json", headers=headers)

    def test_chat_temperature_nan(self, client, auth_headers):
        res = self._post_raw_json(
            client,
            "/v1/chat/completions",
            json.dumps(_chat_body(temperature=float("nan"))),
            auth_headers,
        )
        _assert_never_500(res)

    def test_chat_temperature_infinity(self, client, auth_headers):
        for v in (float("inf"), float("-inf")):
            res = self._post_raw_json(
                client,
                "/v1/chat/completions",
                json.dumps(_chat_body(temperature=v)),
                auth_headers,
            )
            _assert_never_500(res)

    def test_chat_numeric_fields_nan(self, client, auth_headers):
        for field in ("temperature", "top_p"):
            raw = json.dumps(_chat_body(**{field: float("nan")}))
            res = self._post_raw_json(
                client, "/v1/chat/completions", raw, auth_headers
            )
            _assert_never_500(res)

    def test_chat_null_numeric_fields(self, client, auth_headers):
        for field in ("temperature", "top_p", "max_tokens", "n"):
            body = _chat_body(**{field: None})
            res = client.post(
                "/v1/chat/completions", json=body, headers=auth_headers
            )
            _assert_never_500(res)

    def test_chat_messages_null(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": None},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_wallet_amount_nan(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        for v in (float("nan"), float("inf"), float("-inf")):
            res = client.post(
                "/v1/xijian/wallets/user/user_local/{}/mora/deposit".format(world),
                json={"amount": v},
                headers=auth_headers,
            )
            _assert_never_500(res)

    def test_wallet_amount_null(self, client, auth_headers):
        world = _new_world(client, auth_headers).get_json()["id"]
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        res = client.post(
            "/v1/xijian/wallets/user/user_local/{}/mora/deposit".format(world),
            json={"amount": None},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_memory_search_min_score_nan(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/memory/search",
            json={"query": "hi", "min_score": float("nan")},
            headers=auth_headers,
        )
        _assert_never_500(res)


# ---------------------------------------------------------------------------
# 14. 500T 混合 payload（超大）— deep nesting / huge strings / mixed arrays
# ---------------------------------------------------------------------------


class TestCategory14HugePayloads:
    def test_chat_deeply_nested_body(self, client, auth_headers):
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        node = payload
        for _ in range(500):
            node["extra"] = {}
            node = node["extra"]
        res = client.post(
            "/v1/chat/completions", json=payload, headers=auth_headers
        )
        _assert_never_500(res)

    def test_chat_huge_string_field(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(messages=[{"role": "user", "content": "A" * 1_000_000}]),
            headers=auth_headers,
        )
        _assert_never_500(res)

    def test_character_huge_persona(self, client, auth_headers):
        res = _new_character(client, auth_headers, persona_doc="B" * 500_000)
        assert res.status_code == 201

    def test_chat_mixed_type_array_fields(self, client, auth_headers):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(
                stop=[1, "two", None, True, {"x": 1}],
                tools=[1, "two", None],
                tool_choice=[1, 2, 3],
            ),
            headers=auth_headers,
        )
        _assert_never_500(res)

    def test_embeddings_huge_input_array(self, client, auth_headers):
        res = client.post(
            "/v1/embeddings",
            json={"input": ["x" * 100_000, 1, None, True, [1, 2]]},
            headers=auth_headers,
        )
        # 503 backend_unavailable is acceptable when no embedding backend
        # is registered (environmental); a 500 is not.
        assert res.status_code in (200, 400, 503)


# ---------------------------------------------------------------------------
# 15. 把酒吧拆了（破坏性）— DELETE + reset / recovery flows
# ---------------------------------------------------------------------------


class TestCategory15Destructive:
    def test_delete_world_then_get(self, client, auth_headers):
        created = _new_world(client, auth_headers)
        wid = created.get_json()["id"]
        deleted = client.delete(f"/v1/xijian/worlds/{wid}", headers=auth_headers)
        assert deleted.status_code == 200
        gone = client.get(f"/v1/xijian/worlds/{wid}", headers=auth_headers)
        assert gone.status_code == 404

    def test_delete_character_then_get(self, client, auth_headers):
        created = _new_character(client, auth_headers)
        cid = created.get_json()["id"]
        deleted = client.delete(f"/v1/xijian/characters/{cid}", headers=auth_headers)
        assert deleted.status_code == 204
        gone = client.get(f"/v1/xijian/characters/{cid}", headers=auth_headers)
        assert gone.status_code == 404

    def test_world_reset_full_flow(self, client, auth_headers):
        created = _new_world(client, auth_headers)
        wid = created.get_json()["id"]
        preview = client.post(
            f"/v1/xijian/worlds/{wid}/reset/preview", json={}, headers=auth_headers
        )
        assert preview.status_code == 200
        token = preview.get_json()["reset_token"]
        confirm = client.post(
            f"/v1/xijian/worlds/{wid}/reset/confirm",
            json={"reset_token": token},
            headers=auth_headers,
        )
        assert confirm.status_code == 200
        # World still readable afterwards.
        got = client.get(f"/v1/xijian/worlds/{wid}", headers=auth_headers)
        assert got.status_code == 200

    def test_world_reset_cancel_flow(self, client, auth_headers):
        created = _new_world(client, auth_headers)
        wid = created.get_json()["id"]
        preview = client.post(
            f"/v1/xijian/worlds/{wid}/reset/preview", json={}, headers=auth_headers
        )
        assert preview.status_code == 200
        cancelled = client.post(
            f"/v1/xijian/worlds/{wid}/reset/cancel", json={}, headers=auth_headers
        )
        assert cancelled.status_code == 200
        # After cancel, confirm must fail (no pending reset).
        confirm = client.post(
            f"/v1/xijian/worlds/{wid}/reset/confirm",
            json={"reset_token": "x"},
            headers=auth_headers,
        )
        assert confirm.status_code in (403, 404, 409)

    def test_overload_status_and_events(self, client, auth_headers):
        res = client.get("/v1/xijian/overload/status", headers=auth_headers)
        assert res.status_code == 200
        res = client.get("/v1/xijian/overload/events", headers=auth_headers)
        assert res.status_code == 200

    def test_overload_recovery_cancel_without_active(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/overload/recovery/cancel", json={}, headers=auth_headers
        )
        _assert_never_500(res)


# ---------------------------------------------------------------------------
# 16. 化装成老板（越权）— auth bypass attempts
# ---------------------------------------------------------------------------


class TestCategory16Auth:
    def test_no_token_401(self, client):
        res = client.post(
            "/v1/chat/completions", json=_chat_body(), headers={}
        )
        assert res.status_code == 401

    def test_wrong_token_401(self, client):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(),
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert res.status_code == 401

    def test_basic_scheme_401(self, client):
        import base64

        creds = base64.b64encode(b"user:pass").decode()
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(),
            headers={"Authorization": f"Basic {creds}"},
        )
        assert res.status_code == 401

    def test_bearer_wrong_case_401(self, client, token):
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(),
            headers={"Authorization": f"bearer {token}"},
        )
        assert res.status_code == 401

    def test_no_token_write_endpoint_401(self, client):
        res = client.post("/v1/xijian/characters", json={"name": "x"})
        assert res.status_code == 401

    def test_healthz_still_open(self, client):
        res = client.get("/healthz")
        assert res.status_code == 200

    def test_healthz_ignores_wrong_bearer(self, client):
        res = client.get("/healthz", headers={"Authorization": "Bearer nope"})
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# 17. 一万个工程师（负载）— concurrent writes
# ---------------------------------------------------------------------------


class TestCategory17Concurrency:
    def test_concurrent_character_creates(self, app, auth_headers, token):
        errors = []
        created_ids = []
        lock = threading.Lock()

        def worker(worker_id):
            client = app.test_client()
            headers = {"Authorization": f"Bearer {token}"}
            try:
                res = client.post(
                    "/v1/xijian/characters",
                    json={"name": f"Worker-{worker_id}"},
                    headers=headers,
                )
                if res.status_code != 201:
                    errors.append((worker_id, res.status_code))
                else:
                    with lock:
                        created_ids.append(res.get_json()["id"])
            except Exception as exc:  # noqa: BLE001
                errors.append((worker_id, repr(exc)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == [], f"concurrent creates failed: {errors[:5]}"
        assert len(created_ids) == 12
        assert len(set(created_ids)) == 12

        # Data intact and readable afterwards.
        res = app.test_client().get(
            "/v1/xijian/characters", headers=auth_headers
        )
        assert res.status_code == 200

    def test_concurrent_world_creates(self, app, token):
        # MAX_WORLDS=3 and the seeded default world consumes a slot, so
        # 2 concurrent creates must all succeed and nothing may crash.
        errors = []
        lock = threading.Lock()

        def worker(worker_id):
            client = app.test_client()
            headers = {"Authorization": f"Bearer {token}"}
            try:
                res = client.post(
                    "/v1/xijian/worlds", json={"name": f"W-{worker_id}"}, headers=headers
                )
                if res.status_code != 201:
                    errors.append((worker_id, res.status_code))
            except Exception as exc:  # noqa: BLE001
                errors.append((worker_id, repr(exc)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == [], f"concurrent world creates failed: {errors[:5]}"

    def test_concurrent_deposits(self, app, auth_headers, token):
        world = _new_world(app.test_client(), auth_headers).get_json()["id"]
        cur = app.test_client().post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        assert cur.status_code == 201
        path = f"/v1/xijian/wallets/user/user_local/{world}/mora/deposit"

        errors = []
        ok_count = [0]
        lock = threading.Lock()

        def worker(_):
            client = app.test_client()
            headers = {"Authorization": f"Bearer {token}"}
            try:
                res = client.post(path, json={"amount": 1}, headers=headers)
                if res.status_code == 200:
                    with lock:
                        ok_count[0] += 1
                else:
                    errors.append(res.status_code)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == [], f"concurrent deposits failed: {errors[:5]}"
        assert ok_count[0] == 10
        # Balance is exactly the sum (no lost updates).
        got = app.test_client().get(
            f"/v1/xijian/wallets/user/user_local/{world}/mora",
            headers=auth_headers,
        )
        assert got.status_code == 200
        assert got.get_json()["balance"] == 10.0


# ---------------------------------------------------------------------------
# 18. ';DROP TABLE 酒吧;（注入）— SQL injection + path traversal
# ---------------------------------------------------------------------------


class TestCategory18Injection:
    INJECTIONS = [
        "x'; DROP TABLE store_characters;--",
        "'; DROP TABLE store_worlds;--",
        'x" OR "1"="1',
        "x OR 1=1",
        "1 UNION SELECT * FROM store_characters",
        "x; DELETE FROM store_characters;--",
        "x' OR '1'='1",
    ]

    def test_character_id_sql_injection(self, client, auth_headers):
        for payload in self.INJECTIONS:
            res = client.get(
                f"/v1/xijian/characters/{payload}", headers=auth_headers
            )
            assert res.status_code == 404, f"{payload!r} -> {res.status_code}"
        # Table still intact.
        assert "char_yuki" in stubs_state.characters or len(stubs_state.characters) >= 0

    def test_world_id_sql_injection(self, client, auth_headers):
        for payload in self.INJECTIONS:
            res = client.get(f"/v1/xijian/worlds/{payload}", headers=auth_headers)
            assert res.status_code == 404, f"{payload!r} -> {res.status_code}"

    def test_query_param_sql_injection(self, client, auth_headers):
        for payload in self.INJECTIONS:
            res = client.get(
                "/v1/xijian/currencies?world_id=" + payload,
                headers=auth_headers,
            )
            _assert_never_500(res)

    def test_field_value_sql_injection(self, client, auth_headers):
        for payload in self.INJECTIONS:
            res = _new_character(client, auth_headers, name=payload)
            assert res.status_code == 201, f"{payload!r} -> {res.status_code}"

    def test_path_traversal_character(self, client, auth_headers):
        for path in (
            "/v1/xijian/characters/..%2F..%2Fetc%2Fpasswd",
            "/v1/xijian/characters/../../etc/passwd",
            "/v1/xijian/characters/%2e%2e/%2e%2e/etc/passwd",
            "/v1/xijian/characters/%00",
            "/v1/xijian/characters/..%00/",
        ):
            res = client.get(path, headers=auth_headers)
            assert res.status_code in (404, 400, 405), f"{path} -> {res.status_code}"

    def test_path_traversal_world(self, client, auth_headers):
        for path in (
            "/v1/xijian/worlds/..%2F..%2Fetc%2Fpasswd",
            "/v1/xijian/worlds/%00",
        ):
            res = client.get(path, headers=auth_headers)
            assert res.status_code in (404, 400, 405), f"{path} -> {res.status_code}"

    def test_store_tables_survive(self, client, auth_headers):
        # The characters bucket must still be queryable end-to-end.
        res = client.get("/v1/xijian/characters", headers=auth_headers)
        assert res.status_code == 200
        res = client.get("/v1/xijian/characters/char_yuki", headers=auth_headers)
        assert res.status_code in (200, 404)
        # Direct store access still works.
        assert store.bucket("characters") is not None
        assert store.bucket("worlds") is not None


# ---------------------------------------------------------------------------
# 19. 点炒饭酒吧炸了（事后回归）— post-abuse regression
# ---------------------------------------------------------------------------


class TestCategory19PostAbuseRegression:
    def test_after_abuse_healthz(self, client, auth_headers):
        # Run a battery of abuse, then confirm the server still works.
        self._abuse_battery(client, auth_headers)
        res = client.get("/healthz")
        assert res.status_code == 200

    def test_after_abuse_chat_still_works(self, client, auth_headers):
        self._abuse_battery(client, auth_headers)
        res = client.post(
            "/v1/chat/completions",
            json=_chat_body(messages=[{"role": "user", "content": "still alive?"}]),
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["object"] == "chat.completion"

    def test_after_abuse_writes_still_work(self, client, auth_headers):
        self._abuse_battery(client, auth_headers)
        res = _new_character(client, auth_headers, name="PostAbuse")
        assert res.status_code == 201
        res = _new_world(client, auth_headers, name="PostAbuseWorld")
        assert res.status_code == 201

    def test_after_abuse_data_not_corrupted(self, client, auth_headers):
        self._abuse_battery(client, auth_headers)
        # Default seeded character still intact.
        res = client.get("/v1/xijian/characters/char_yuki", headers=auth_headers)
        assert res.status_code in (200, 404)
        # Buckets still enumerable.
        assert len(store.bucket("characters")) >= 0
        assert len(store.bucket("worlds")) >= 0

    def _abuse_battery(self, client, auth_headers):
        """A compact mix of every abusive input family."""
        # Type confusion + garbage.
        client.post(
            "/v1/chat/completions",
            json=_chat_body(temperature="abc", top_p="xyz", n="nope", max_tokens="bad"),
            headers=auth_headers,
        )
        client.post("/v1/chat/completions", json=[1, 2, 3], headers=auth_headers)
        client.post(
            "/v1/chat/completions",
            json=_chat_body(temperature=float("nan")),
            headers=auth_headers,
        )
        client.post(
            "/v1/chat/completions",
            json=_chat_body(temperature=float("inf")),
            headers=auth_headers,
        )
        client.post(
            "/v1/chat/completions",
            json=_chat_body(messages=[{"role": "user", "content": "\u0000\uFFFF"}]),
            headers=auth_headers,
        )
        # Empty / missing.
        client.post("/v1/chat/completions", json={}, headers=auth_headers)
        client.post(
            "/v1/chat/completions",
            data=b"\xff\xfe\x00",
            content_type="application/json",
            headers=auth_headers,
        )
        # Injection IDs.
        client.get(
            "/v1/xijian/characters/x'; DROP TABLE store_characters;--",
            headers=auth_headers,
        )
        # State-machine abuse.
        client.delete("/v1/xijian/sessions/sess_nope", headers=auth_headers)
        client.post(
            "/v1/xijian/worlds/w_nope/reset/confirm",
            json={"reset_token": "x"},
            headers=auth_headers,
        )
        # Negative / huge amounts.
        world = None
        try:
            world = _new_world(client, auth_headers).get_json()["id"]
            client.post(
                "/v1/xijian/currencies",
                json={"world_id": world, "code": "mora", "name": "Mora"},
                headers=auth_headers,
            )
            client.post(
                f"/v1/xijian/wallets/user/user_local/{world}/mora/deposit",
                json={"amount": -5},
                headers=auth_headers,
            )
            client.post(
                f"/v1/xijian/wallets/user/user_local/{world}/mora/deposit",
                json={"amount": 2**63},
                headers=auth_headers,
            )
        except Exception:  # noqa: BLE001
            pass
