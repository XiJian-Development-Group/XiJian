"""Process-wide in-memory state stubs.

This package hosts the empty containers that the rest of the codebase
fills in.  See :mod:`xijian_api.stubs.state` for the actual storage
and the various ``stubs/*.py`` modules (delivered in later tasks) for
the per-resource implementations (chat, files, etc.).

We intentionally keep the surface area small — these are not real
services.  They just need to satisfy the type and shape expectations
of the routes.
"""

from xijian_api.stubs import state

__all__ = ["state"]