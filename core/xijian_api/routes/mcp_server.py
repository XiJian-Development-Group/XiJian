"""``POST /v1/mcp`` — MCP JSON-RPC 2.0 endpoint.

Single endpoint that accepts a JSON-RPC 2.0 request (single or
batch) and dispatches it through the MCP protocol handler.  The
handler routes ``tools/call`` through the A5.2 gate before
executing any tool.

Usage
=====

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

Notifications (requests without ``id``) return ``202 Accepted``
with an empty body, per JSON-RPC 2.0 §4.
"""

from __future__ import annotations

import logging

from flask import Blueprint, g, jsonify, request

from xijian_api.mcp.protocol import handle_batch


bp = Blueprint("mcp_server", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.mcp_server")


@bp.post("/v1/mcp")
def mcp_endpoint():
    """Handle a single or batch JSON-RPC 2.0 request."""
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

    # Extract caller info for audit context.
    caller = getattr(g, "request_id", None) or request.headers.get("X-Request-Id")

    response = handle_batch(payload, caller=caller)

    if response is None:
        # Notification — no response body per JSON-RPC 2.0 §4.
        return "", 202

    if isinstance(response, list):
        return jsonify(response)

    # Check if it's an error response (has "error" key) to set
    # the HTTP status code accordingly.
    if isinstance(response, dict) and "error" in response:
        code = response["error"].get("code", -32603)
        # Map JSON-RPC error codes to HTTP status.
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
