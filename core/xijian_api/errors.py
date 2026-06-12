"""Error types and dual-format rendering (OAI vs JSON-RPC).

The server speaks both the OpenAI-style error envelope *and* a JSON-RPC
2.0 envelope.  Clients opt in via ``Accept: application/json-rpc``;
otherwise the OAI envelope is used.

Per ``DESIGN.md`` §6 the JSON-RPC code mapping is:

======================== =================
OAI type (and context)   JSON-RPC code
======================== =================
``invalid_request_error`` (Parse)        -32700
``invalid_request_error`` (Invalid Req)  -32600
``invalid_request_error`` (Method nf)    -32601
``invalid_request_error`` (Invalid parm) -32602
``server_error`` (Internal)              -32603
``not_found_error``                      -32001
``conflict``                             -32002
``permission_error``                     -32003
``rate_limit_error``                     -32004
``backend_unavailable``                  -32005
``protection_error``                     -32010
``content_filter``                       -32011
default                                 -32603
======================== =================

The mapping is implemented as a table keyed by ``(status, type_, code)``
so multiple OAI types map deterministically to a JSON-RPC code.  When
no rule matches, the default ``-32603`` (Internal Error) is used.
"""

from __future__ import annotations

from typing import Any

from flask import jsonify, request

from xijian_api.utils.log import get_logger

_LOGGER = get_logger()

# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """Raised anywhere in the application to short-circuit a request.

    Parameters
    ----------
    status:
        HTTP status code (e.g. ``400``, ``404``, ``500``).
    message:
        Human-readable message returned to the client.
    type_:
        OAI error type — one of ``invalid_request_error``,
        ``server_error``, ``not_found_error``, ``conflict``,
        ``permission_error``, ``rate_limit_error``,
        ``backend_unavailable``, ``protection_error``,
        ``content_filter``.
    code:
        Machine-readable code (e.g. ``invalid_api_key``).
    param:
        Optional parameter name the error relates to.
    **extra:
        Any additional fields to merge into the OAI envelope.
    """

    def __init__(
        self,
        status: int,
        message: str,
        type_: str,
        code: str | None = None,
        param: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.type_ = type_
        self.code = code
        self.param = param
        self.extra = extra


class AuthError(ApiError):
    """401 Unauthorized — missing or invalid Bearer token."""

    def __init__(self, message: str = "Unauthorized") -> None:
        super().__init__(
            status=401,
            message=message,
            type_="invalid_request_error",
            code="invalid_api_key",
        )


class BackendError(ApiError):
    """Base class for backend / AI layer errors."""

    def __init__(
        self,
        status: int,
        message: str,
        type_: str = "server_error",
        code: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(status, message, type_, code, **extra)


class GenerationAborted(BackendError):
    """Raised when an in-flight generation has been aborted by the client.

    This is a subclass of :class:`BackendError` (per ``DESIGN.md`` §9.1
    and ``ai-backend.md`` §1.1) so callers can catch backend failures
    broadly while still distinguishing a clean cancel from a real
    failure.
    """

    def __init__(self, message: str = "aborted by client") -> None:
        super().__init__(
            status=499,  # non-standard but signals "client closed request"
            message=message,
            type_="server_error",
            code="generation_aborted",
        )


# ---------------------------------------------------------------------------
# OAI ↔ JSON-RPC mapping
# ---------------------------------------------------------------------------

#: Table from ``(status, type_, code)`` to a JSON-RPC code.
#: Looked up with progressively looser keys: exact → ``(None, type_, code)``
#: → ``(status, type_, None)`` → ``(None, type_, None)`` → default.
JSONRPC_CODE_TABLE: dict[tuple[int | None, str, str | None], int] = {
    # Parse errors (HTTP 400)
    (400, "invalid_request_error", "parse_error"): -32700,
    # Invalid Request (HTTP 400)
    (400, "invalid_request_error", None): -32600,
    # Auth failures (HTTP 401) also surface as invalid_request_error.
    (401, "invalid_request_error", None): -32600,
    # Method not found (HTTP 404 with type invalid_request_error)
    (404, "invalid_request_error", None): -32601,
    # Method not allowed (HTTP 405) — also "no such method on the resource".
    (405, "invalid_request_error", None): -32601,
    # Invalid params (HTTP 422 with type invalid_request_error)
    (422, "invalid_request_error", None): -32602,
    # Internal server error
    (500, "server_error", None): -32603,
    # Domain-specific mapping (status-driven)
    (404, "not_found_error", None): -32001,
    (409, "conflict", None): -32002,
    (403, "permission_error", None): -32003,
    (429, "rate_limit_error", None): -32004,
    (503, "backend_unavailable", None): -32005,
    (403, "protection_error", None): -32010,
    (400, "content_filter", None): -32011,
}

DEFAULT_JSONRPC_CODE = -32603


def to_jsonrpc_code(status: int, type_: str, code: str | None) -> int:
    """Map an OAI ``(status, type_, code)`` triple to a JSON-RPC code."""
    if (status, type_, code) in JSONRPC_CODE_TABLE:
        return JSONRPC_CODE_TABLE[(status, type_, code)]
    if (status, type_, None) in JSONRPC_CODE_TABLE:
        return JSONRPC_CODE_TABLE[(status, type_, None)]
    if (None, type_, code) in JSONRPC_CODE_TABLE:
        return JSONRPC_CODE_TABLE[(None, type_, code)]
    if (None, type_, None) in JSONRPC_CODE_TABLE:
        return JSONRPC_CODE_TABLE[(None, type_, None)]
    return DEFAULT_JSONRPC_CODE


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _accept_is_jsonrpc() -> bool:
    """Return ``True`` if the request's ``Accept`` asks for JSON-RPC."""
    accept = request.headers.get("Accept", "")
    return "application/json-rpc" in accept


def render_error(err: ApiError):
    """Render an :class:`ApiError` as a Flask response.

    The format is chosen based on the ``Accept`` header:

    * ``application/json-rpc`` → JSON-RPC 2.0 envelope
      (``{"jsonrpc": "2.0", "error": {"code": ..., "message": ...}, "id": null}``)
    * anything else → OAI envelope
      (``{"error": {"message": ..., "type": ..., "code": ...}}``)
    """
    if _accept_is_jsonrpc():
        rpc_code = to_jsonrpc_code(err.status, err.type_, err.code)
        body = {
            "jsonrpc": "2.0",
            "error": {
                "code": rpc_code,
                "message": err.message,
                "data": {
                    "type": err.type_,
                    "code": err.code,
                    "param": err.param,
                    "status": err.status,
                },
            },
            "id": None,
        }
        response = jsonify(body)
        response.status_code = err.status
        return response

    error_payload: dict[str, Any] = {
        "message": err.message,
        "type": err.type_,
        "code": err.code,
    }
    if err.param is not None:
        error_payload["param"] = err.param
    if err.extra:
        error_payload.update(err.extra)

    body = {"error": error_payload}
    response = jsonify(body)
    response.status_code = err.status
    return response


def register_error_handlers(app) -> None:
    """Register Flask error handlers on ``app``.

    * :class:`ApiError` instances are converted to the appropriate
      JSON envelope (OAI or JSON-RPC).
    * ``404`` and ``405`` are also converted to OAI ``not_found_error``
      envelopes so clients get a consistent error contract.
    * Any uncaught exception becomes a 500 OAI ``server_error``.
    """

    @app.errorhandler(ApiError)
    def _handle_api_error(err: ApiError):  # type: ignore[no-redef]
        return render_error(err)

    @app.errorhandler(404)
    def _handle_404(_err):  # type: ignore[no-redef]
        return render_error(
            ApiError(
                status=404,
                message=f"route not found: {request.path}",
                type_="not_found_error",
                code="route_not_found",
            )
        )

    @app.errorhandler(405)
    def _handle_405(_err):  # type: ignore[no-redef]
        return render_error(
            ApiError(
                status=405,
                message=f"method not allowed: {request.method} {request.path}",
                type_="invalid_request_error",
                code="method_not_allowed",
            )
        )

    @app.errorhandler(Exception)
    def _handle_unexpected(err: Exception):  # type: ignore[no-redef]
        if isinstance(err, ApiError):
            return render_error(err)
        _LOGGER.exception("uncaught exception: %s", err)
        return render_error(
            ApiError(
                status=500,
                message="internal server error",
                type_="server_error",
                code="internal_error",
            )
        )


__all__ = [
    "ApiError",
    "AuthError",
    "BackendError",
    "GenerationAborted",
    "JSONRPC_CODE_TABLE",
    "DEFAULT_JSONRPC_CODE",
    "to_jsonrpc_code",
    "render_error",
    "register_error_handlers",
]