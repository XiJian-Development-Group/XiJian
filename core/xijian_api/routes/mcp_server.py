"""``POST /v1/mcp`` — MCP JSON-RPC 2.0 端点。

单一端点，接受 JSON-RPC 2.0 请求（单个或批量），
并通过 MCP 协议处理器分发。处理器在执行任何工具前，
先将 ``tools/call`` 路由到 A5.2 门禁。

用法
====

.. code-block:: bash

    curl -X POST http://localhost:8000/v1/mcp \\
      -H 'Content-Type: application/json' \\
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

    curl -X POST http://localhost:8000/v1/mcp \\
      -H 'Content-Type: application/json' \\
      -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

    curl -X POST http://localhost:8000/v1/mcp \\
      -H 'Content-Type: application/json' \\
      -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
           "params":{"name":"character_list","arguments":{}}}'

通知（无 ``id`` 的请求）返回 ``202 Accepted`` 与空 body，依 JSON-RPC 2.0 §4。
"""

from __future__ import annotations

import logging

from flask import Blueprint, g, jsonify, request

from xijian_api.mcp.protocol import handle_batch


bp = Blueprint("mcp_server", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.mcp_server")


@bp.post("/v1/mcp")
def mcp_endpoint():
    """处理单个或批量 JSON-RPC 2.0 请求。"""
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32700,
                "message": "parse error: body is not valid JSON",
            },
        }), 400

    # 提取调用者信息，用于审计上下文。
    caller = getattr(g, "request_id", None) or request.headers.get("X-Request-Id")

    response = handle_batch(payload, caller=caller)

    if response is None:
        # 通知 — 依 JSON-RPC 2.0 §4，无响应 body。
        return "", 202

    if isinstance(response, list):
        return jsonify(response)

    # 检查是否为错误响应（含 "error" 键），以设置
    # 相应的 HTTP 状态码。
    if isinstance(response, dict) and "error" in response:
        code = response["error"].get("code", -32603)
        # 将 JSON-RPC 错误码映射为 HTTP 状态。
        if code == -32700:
            http_status = 400  # parse error
        elif code == -32600:
            http_status = 400  # invalid request
        elif code == -32601:
            http_status = 404  # method not found
        elif code == -32602:
            http_status = 400  # invalid params
        else:
            http_status = 500  # internal error
        return jsonify(response), http_status

    return jsonify(response)


__all__ = ["bp"]
