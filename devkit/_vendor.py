"""Vendored helpers so the DevKit is a fully self-contained package.

Why this module exists
----------------------

The DevKit used to live inside ``xijian_api`` (as ``xijian_api.devkit``)
and borrowed three small utilities from the API package:

* ``xijian_api.errors.ApiError``      — the base error type
* ``xijian_api.utils.ids.gen_submission_id``
* ``xijian_api.utils.time.iso_now`` / ``now_ts``

That coupling is a problem once the DevKit ships **separately** — it is
PyInstaller-packaged into a double-clickable app while the API is built
as a wheel/service (function list v2.3, C5 packaging split).  Dragging
``xijian_api`` (and, transitively, **Flask**) into the frozen DevKit
binary would bloat it by tens of MB for three tiny functions and would
break the long-standing "DevKit never imports Flask" contract.

So we vendor minimal, dependency-free copies here.  These are
deliberately kept byte-for-byte behaviour-compatible with their
``xijian_api`` originals:

* :class:`ApiError` mirrors ``xijian_api.errors.ApiError``'s constructor
  and attributes (``status`` / ``message`` / ``type_`` / ``code`` /
  ``param`` / ``extra``).  The Flask rendering side (``render_error`` /
  ``register_error_handlers``) is intentionally **not** copied — the
  DevKit surfaces errors as plain dicts via
  :func:`devkit.api.serialize_error`, never as HTTP envelopes.
* :func:`gen_submission_id` mirrors ``xijian_api.utils.ids`` (``sub_``
  prefix + 12 crypto-grade hex chars).
* :func:`iso_now` / :func:`now_ts` mirror ``xijian_api.utils.time``.

If the API-side originals ever change their contract, mirror the change
here too (see docs/notes.md → "没动的与原因").
"""

from __future__ import annotations

import datetime as _dt
import secrets
from typing import Any


# ---------------------------------------------------------------------------
# Error base (vendored from xijian_api.errors.ApiError — no Flask)
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """Structured error carrying the OAI ``(status, type_, code)`` triple.

    Behaviour-compatible with ``xijian_api.errors.ApiError`` so DevKit
    error records keep the exact same shape the rest of the project uses,
    but with **zero** Flask dependency — the DevKit renders errors as
    plain dicts (see :func:`devkit.api.serialize_error`), never as HTTP
    responses.

    Parameters
    ----------
    status:
        HTTP-style status code (e.g. ``400``, ``429``, ``502``).
    message:
        Human-readable message.
    type_:
        OAI error type (``server_error``, ``invalid_request_error``, …).
    code:
        Machine-readable code (e.g. ``rate_limited``).
    param:
        Optional parameter name the error relates to.
    **extra:
        Any additional fields to merge into the serialised error dict.
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
# ID generation (vendored from xijian_api.utils.ids)
# ---------------------------------------------------------------------------

#: Number of hex chars in a short identifier (matches xijian_api.utils.ids).
_SHORT_HEX_LEN = 12


def gen_submission_id() -> str:
    """Return a Developer-Kit submission id (``sub_<12 hex>``).

    Every archive / SMTP submission gets its own short id so it can be
    referenced from the receiving side without leaking sensitive content
    into local logs.  Uses :func:`secrets.token_hex` (crypto-grade).
    """
    return f"sub_{secrets.token_hex(_SHORT_HEX_LEN // 2)}"


# ---------------------------------------------------------------------------
# Time helpers (vendored from xijian_api.utils.time)
# ---------------------------------------------------------------------------


def now_ts() -> int:
    """Return the current Unix timestamp (seconds since epoch)."""
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


def iso_now() -> str:
    """Return the current UTC time as ISO-8601 with a ``Z`` suffix."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["ApiError", "gen_submission_id", "now_ts", "iso_now"]
