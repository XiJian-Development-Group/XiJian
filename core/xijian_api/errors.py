"""Error types and dual-format rendering (OAI vs JSON-RPC).

错误类型及双格式渲染（OAI vs JSON-RPC）。

The server speaks both the OpenAI-style error envelope *and* a JSON-RPC
2.0 envelope.  Clients opt in via ``Accept: application/json-rpc``;
otherwise the OAI envelope is used.

服务器同时支持 OpenAI 风格的错误信封 *和* JSON-RPC 2.0 信封。
客户端通过 ``Accept: application/json-rpc`` 选择使用 JSON-RPC 格式；
否则默认使用 OAI 信封。

Per ``DESIGN.md`` §6 the JSON-RPC code mapping is:

根据 ``DESIGN.md`` §6，JSON-RPC 代码映射如下：

======================== =================
OAI type (and context)   JSON-RPC code
OAI 类型（及上下文）     JSON-RPC 代码
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

映射实现为一个以 ``(status, type_, code)`` 为键的表格，
使多种 OAI 类型能确定性地映射到 JSON-RPC 代码。
当没有规则匹配时，使用默认值 ``-32603``（内部错误）。
"""

from __future__ import annotations

from typing import Any

from flask import jsonify, request

from xijian_api.utils.log import get_logger

_LOGGER = get_logger()

# ---------------------------------------------------------------------------
# Exception types
# 异常类型
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """Raised anywhere in the application to short-circuit a request.

    在应用中任何地方抛出以短路请求。

    Parameters
    ----------
    status:
        HTTP status code (e.g. ``400``, ``404``, ``500``).
        HTTP 状态码（例如 ``400``、``404``、``500``）。
    message:
        Human-readable message returned to the client.
        返回给客户端的人类可读消息。
    type_:
        OAI error type — one of ``invalid_request_error``,
        ``server_error``, ``not_found_error``, ``conflict``,
        ``permission_error``, ``rate_limit_error``,
        ``backend_unavailable``, ``protection_error``,
        ``content_filter``.
        OAI 错误类型。
    code:
        Machine-readable code (e.g. ``invalid_api_key``).
        机器可读的错误代码（例如 ``invalid_api_key``）。
    param:
        Optional parameter name the error relates to.
        可选的参数名称，指定错误涉及的参数。
    **extra:
        Any additional fields to merge into the OAI envelope.
        合并到 OAI 信封中的任何额外字段。
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
    """401 Unauthorized — missing or invalid Bearer token.

    401 未授权 — 缺少或无效的 Bearer 令牌。
    """

    def __init__(self, message: str = "Unauthorized") -> None:
        super().__init__(
            status=401,
            message=message,
            type_="invalid_request_error",
            code="invalid_api_key",
        )


class BackendError(ApiError):
    """Base class for backend / AI layer errors.

    后端 / AI 层错误的基础类。
    """

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

    当正在进行的生成被客户端中止时抛出。

    This is a subclass of :class:`BackendError` (per ``DESIGN.md`` §9.1
    and ``ai-backend.md`` §1.1) so callers can catch backend failures
    broadly while still distinguishing a clean cancel from a real
    failure.

    这是 :class:`BackendError` 的子类（根据 ``DESIGN.md`` §9.1
    和 ``ai-backend.md`` §1.1），使调用者能广泛捕获后端故障，
    同时仍能区分正常取消和实际故障。
    """

    def __init__(self, message: str = "aborted by client") -> None:
        super().__init__(
            status=499,  # non-standard but signals "client closed request"
                        # 非标准状态码，但表示"客户端关闭了请求"
            message=message,
            type_="server_error",
            code="generation_aborted",
        )


# ---------------------------------------------------------------------------
# OAI ↔ JSON-RPC mapping
# OAI ↔ JSON-RPC 映射
# ---------------------------------------------------------------------------

#: Table from ``(status, type_, code)`` to a JSON-RPC code.
#: Looked up with progressively looser keys: exact → ``(None, type_, code)``
#: → ``(status, type_, None)`` → ``(None, type_, None)`` → default.
#: 从 ``(status, type_, code)`` 到 JSON-RPC 代码的映射表。
#: 查找使用逐渐宽松的键：精确 → ``(None, type_, code)``
#: → ``(status, type_, None)`` → ``(None, type_, None)`` → 默认值。
JSONRPC_CODE_TABLE: dict[tuple[int | None, str, str | None], int] = {
    # Parse errors (HTTP 400)
    # 解析错误（HTTP 400）
    (400, "invalid_request_error", "parse_error"): -32700,
    # Invalid Request (HTTP 400)
    # 无效请求（HTTP 400）
    (400, "invalid_request_error", None): -32600,
    # Auth failures (HTTP 401) also surface as invalid_request_error.
    # 认证失败（HTTP 401）也表现为 invalid_request_error。
    (401, "invalid_request_error", None): -32600,
    # Method not found (HTTP 404 with type invalid_request_error)
    # 方法未找到（HTTP 404，类型为 invalid_request_error）
    (404, "invalid_request_error", None): -32601,
    # Method not allowed (HTTP 405) — also "no such method on the resource".
    # 方法不允许（HTTP 405）
    (405, "invalid_request_error", None): -32601,
    # Invalid params (HTTP 422 with type invalid_request_error)
    # 无效参数（HTTP 422，类型为 invalid_request_error）
    (422, "invalid_request_error", None): -32602,
    # Internal server error
    # 内部服务器错误
    (500, "server_error", None): -32603,
    # Domain-specific mapping (status-driven)
    # 领域特定映射（基于状态码）
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
    """Map an OAI ``(status, type_, code)`` triple to a JSON-RPC code.

    将 OAI ``(status, type_, code)`` 三元组映射为 JSON-RPC 代码。
    """
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
# 响应构建器
# ---------------------------------------------------------------------------


def _accept_is_jsonrpc() -> bool:
    """Return ``True`` if the request's ``Accept`` asks for JSON-RPC.

    如果请求的 ``Accept`` 头要求 JSON-RPC 格式，返回 ``True``。
    """
    accept = request.headers.get("Accept", "")
    return "application/json-rpc" in accept


def render_error(err: ApiError):
    """Render an :class:`ApiError` as a Flask response.

    将 :class:`ApiError` 渲染为 Flask 响应。

    The format is chosen based on the ``Accept`` header:

    格式根据 ``Accept`` 头选择：

    * ``application/json-rpc`` → JSON-RPC 2.0 envelope
      (``{"jsonrpc": "2.0", "error": {"code": ..., "message": ...}, "id": null}``)
    * anything else → OAI envelope
      (``{"error": {"message": ..., "type": ..., "code": ...}}``)
    * 其他任何格式 → OAI 信封
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

    在 ``app`` 上注册 Flask 错误处理器。

    * :class:`ApiError` instances are converted to the appropriate
      JSON envelope (OAI or JSON-RPC).
    * ``404`` and ``405`` are also converted to OAI ``not_found_error``
      envelopes so clients get a consistent error contract.
    * Any uncaught exception becomes a 500 OAI ``server_error``.

    * :class:`ApiError` 实例被转换为适当的 JSON 信封（OAI 或 JSON-RPC）。
    * ``404`` 和 ``405`` 也被转换为 OAI ``not_found_error`` 信封，
      使客户端获得一致的错误契约。
    * 任何未捕获的异常变成 500 OAI ``server_error``。
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

    @app.errorhandler(413)
    def _handle_413(_err):  # type: ignore[no-redef]
        # S6 — MAX_CONTENT_LENGTH overflow surfaces as a 413
        # RequestEntityTooLarge (an HTTPException).  Without an
        # explicit handler the generic ``Exception`` handler below
        # would swallow it into a 500; register a dedicated handler
        # so oversized bodies get a clean JSON 413.
        # S6 — MAX_CONTENT_LENGTH 超限会以 413 RequestEntityTooLarge
        # （一个 HTTPException）形式出现。若没有显式处理器，下方的
        # 通用 ``Exception`` 处理器会把它吞成 500；注册专用处理器
        # 使超大体返回干净的 JSON 413。
        return render_error(
            ApiError(
                status=413,
                message="request body too large",
                type_="invalid_request_error",
                code="request_entity_too_large",
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
