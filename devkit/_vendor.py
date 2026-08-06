"""内置辅助函数，使 DevKit 成为一个完全自包含的包。

为什么存在这个模块
----------------------

DevKit 曾经位于 ``xijian_api`` 包内（即 ``xijian_api.devkit``），
并从 API 包借用了三个小工具：

* ``xijian_api.errors.ApiError``      —— 基础错误类型
* ``xijian_api.utils.ids.gen_submission_id``
* ``xijian_api.utils.time.iso_now`` / ``now_ts``

一旦 DevKit **单独**发布，这种耦合就成了问题——它会被 PyInstaller
打包成可双击运行的应用程序，而 API 则以 wheel/服务形式构建（功能清单
v2.3，C5 打包拆分）。把 ``xijian_api``（并间接带入 **Flask**）拖进
冻结后的 DevKit 二进制包，会为了三个小函数而增大几十 MB，
还会破坏长期存在的“DevKit 绝不导入 Flask”约定。

因此我们在这里内置了最小化、零依赖的副本。这些副本刻意保持与
``xijian_api`` 原版逐字节行为兼容：

* :class:`ApiError` 与 ``xijian_api.errors.ApiError`` 的构造函数和属性
  （``status`` / ``message`` / ``type_`` / ``code`` /
  ``param`` / ``extra``）保持一致。Flask 渲染部分（``render_error`` /
  ``register_error_handlers``）刻意**不**复制——DevKit 通过
  :func:`devkit.api.serialize_error` 以纯 dict 形式呈现错误，
  从不使用 HTTP 信封格式。
* :func:`gen_submission_id` 与 ``xijian_api.utils.ids`` 保持一致
  （``sub_`` 前缀 + 12 位加密级十六进制字符）。
* :func:`iso_now` / :func:`now_ts` 与 ``xijian_api.utils.time`` 保持一致。

如果 API 侧原版将来改变了约定，请在此处同步修改
（参见 docs/notes.md → “没动的与原因”）。
"""

from __future__ import annotations

import datetime as _dt
import secrets
from typing import Any


# ---------------------------------------------------------------------------
# 错误基类（来自 xijian_api.errors.ApiError 的内置副本 —— 不含 Flask）
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """携带 OAI ``(status, type_, code)`` 三元组的结构化错误。

    与 ``xijian_api.errors.ApiError`` 行为兼容，因此 DevKit 的错误记录
    与项目其他部分保持完全相同的结构，但**零** Flask 依赖——DevKit
    将错误渲染为纯 dict（参见 :func:`devkit.api.serialize_error`），
    从不渲染为 HTTP 响应。

    参数
    ----------
    status:
        HTTP 风格状态码（例如 ``400``、``429``、``502``）。
    message:
        人类可读的消息。
    type_:
        OAI 错误类型（``server_error``、``invalid_request_error`` 等）。
    code:
        机器可读的错误码（例如 ``rate_limited``）。
    param:
        与该错误相关的可选参数名。
    **extra:
        要合并进序列化错误 dict 的任何其他字段。
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


# ---------------------------------------------------------------------------
# ID 生成（来自 xijian_api.utils.ids 的内置副本）
# ---------------------------------------------------------------------------

#: 短标识符的十六进制字符数（与 xijian_api.utils.ids 保持一致）。
_SHORT_HEX_LEN = 12


def gen_submission_id() -> str:
    """返回一个 DevKit 提交 ID（``sub_<12 位十六进制>``）。

    每次归档 / SMTP 提交都会获得自己的短 ID，以便接收方引用，
    而不会将敏感内容泄露到本地日志中。使用 :func:`secrets.token_hex`
    （加密级）。
    """
    return f"sub_{secrets.token_hex(_SHORT_HEX_LEN // 2)}"


# ---------------------------------------------------------------------------
# 时间辅助函数（来自 xijian_api.utils.time 的内置副本）
# ---------------------------------------------------------------------------


def now_ts() -> int:
    """返回当前 Unix 时间戳（自纪元以来的秒数）。"""
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


def iso_now() -> str:
    """返回带 ``Z`` 后缀的 ISO-8601 格式当前 UTC 时间。"""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["ApiError", "gen_submission_id", "now_ts", "iso_now"]
