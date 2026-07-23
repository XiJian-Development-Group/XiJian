"""Process-wide registry of loaded model instances.

Each backend instance in :mod:`xijian_api.ai.backends` handles one
loaded checkpoint; this registry maps a ``model_id`` (declared in the
``[[models]]`` block of ``config.toml``) to the live backend instance
that's already been :meth:`Backend.load` -ed.

This sits **on top of** :mod:`xijian_api.ai.registry`, which only
catalogs backend *classes* (mlx / gguf / mock / ...).  Loading a model
follows four steps:

1. Look up the :class:`ModelEntry` in ``config.model_by_id(model_id)``.
2. Pick the backend class for ``entry.type`` + ``entry.backend``
   (the latter usually being ``"mlx"`` or ``"gguf"``; tests register
   a synthetic ``"mock"`` backend as well).
3. Instantiate the class and resolve the on-disk path via
   :meth:`ModelEntry.absolute_path` so every model lands under the
   single ``<storage.base_dir>/<storage.models_subdir>`` root.
4. Call :meth:`Backend.load` with the path + kwargs (default context
   length, model ``extra`` block, caller-supplied overrides).

The registry is a process-wide singleton — see :func:`get_registry`.
Tests use :meth:`ModelRegistry.reset` to clear the cache.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xijian_api.ai.base import (
    BackendError,
    BackendUnavailable,
    ModelNotFound,
    ModelNotLoaded,
)
from xijian_api.ai.registry import (
    get_chat_backend,
    get_embedding_backend,
    get_image_backend,
    get_stt_backend,
    get_tts_backend,
    get_video_backend,
)
from xijian_api.config import Config, ModelEntry


_TASK_GETTERS = {
    "chat": get_chat_backend,
    "embeddings": get_embedding_backend,
    "tts": get_tts_backend,
    "stt": get_stt_backend,
    "image": get_image_backend,
    "video": get_video_backend,
}


@dataclass
class LoadedModel:
    """A live, ready-to-call backend instance plus its config entry."""

    entry: ModelEntry
    instance: Any            # ChatBackend | EmbeddingBackend | ... — runtime type
    absolute_path: Path

    @property
    def task(self) -> str:
        return self.entry.type

    @property
    def backend_name(self) -> str:
        return self.entry.backend


class ModelRegistry:
    """Process-wide pool of loaded model instances."""

    def __init__(self) -> None:
        self._instances: dict[str, LoadedModel] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    # -- introspection ------------------------------------------------------

    def list_loaded(self) -> list[str]:
        """Return sorted ``model_id`` of every currently loaded model."""
        return sorted(self._instances.keys())

    def is_loaded(self, model_id: str) -> bool:
        return model_id in self._instances

    def get(self, model_id: str) -> LoadedModel:
        """Return the live :class:`LoadedModel` for ``model_id``.

        Raises :class:`ModelNotLoaded` when no instance is cached.
        """
        try:
            return self._instances[model_id]
        except KeyError as exc:
            raise ModelNotLoaded(f"model not loaded: {model_id}") from exc

    def entries(self) -> list[LoadedModel]:
        """Return every loaded :class:`LoadedModel` (used by /v1/models)."""
        return list(self._instances.values())

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def entry_for(config: Config, model_id: str) -> ModelEntry:
        """Look up a :class:`ModelEntry` by id; raise :class:`ModelNotFound`."""
        entry = config.model_by_id(model_id)
        if entry is None:
            raise ModelNotFound(
                f"model not registered in config: {model_id}",
                code="model_not_found",
            )
        return entry

    @staticmethod
    def _resolve_backend_class(task: str, backend_name: str) -> type:
        """Find the backend class for ``task``/``backend_name``.

        Uses the public registry helpers, which fall through to
        ``is_available()`` and raise :class:`BackendUnavailable` when
        the requested backend can't run (e.g. ``mlx`` on Linux).
        """
        getter = _TASK_GETTERS.get(task)
        if getter is None:
            raise BackendError(
                f"unknown task: {task}",
                code="backend_error",
            )
        # Empty fallbacks → must use ``backend_name`` directly; no
        # silent substitution.  Any ``BackendUnavailable`` propagates.
        instance = getter(name=backend_name, fallbacks=())
        return type(instance)

    # -- lifecycle ----------------------------------------------------------

    def load(
        self,
        model_id: str,
        *,
        config: Config,
        **kwargs: Any,
    ) -> LoadedModel:
        """Load ``model_id`` into a backend instance and cache it.

        Returns the resulting :class:`LoadedModel`.  Idempotent: when
        the same ``model_id`` is already loaded, the existing
        instance is returned; kwargs are ignored (call
        :meth:`unload` first to re-load with new options).

        ``kwargs`` are forwarded to :meth:`Backend.load` after the
        defaults derived from the :class:`ModelEntry` (caller wins).
        """
        entry = self.entry_for(config, model_id)
        lock = self._lock_for(model_id)
        with lock:
            existing = self._instances.get(model_id)
            if existing is not None:
                return existing

            cls = self._resolve_backend_class(entry.type, entry.backend)
            absolute_path = entry.absolute_path(config.storage)

            try:
                instance = cls()
            except Exception as exc:
                raise BackendError(
                    f"backend init failed for {model_id}: {exc}",
                    code="backend_error",
                ) from exc

            load_kwargs: dict[str, Any] = {}
            # Extra fields from the [[models]] table land first; the
            # caller's kwargs override them, mirroring how registries
            # typically merge config + request.
            load_kwargs.update(entry.extra)
            load_kwargs.update(kwargs)
            if entry.context_length and "context_length" not in load_kwargs:
                load_kwargs["context_length"] = entry.context_length

            # For ``backend = "openai"`` models, pass the global
            # ``[backends.openai]`` section so :func:`resolve_config`
            # can merge per-model overrides with global defaults.
            if entry.backend == "openai":
                oai = config.backends.openai
                load_kwargs["_openai_section"] = {
                    "base_url": oai.base_url,
                    "api_key": oai.api_key,
                    "default_model": oai.default_model,
                    "transport": oai.transport,
                    "headers": dict(oai.headers),
                    "video_endpoint": oai.video_endpoint,
                }

            try:
                instance.load(absolute_path, **load_kwargs)
            except BackendError:
                raise
            except Exception as exc:
                raise BackendError(
                    f"backend.load failed for {model_id}: {exc}",
                    code="backend_error",
                ) from exc

            loaded = LoadedModel(
                entry=entry,
                instance=instance,
                absolute_path=absolute_path,
            )
            self._instances[model_id] = loaded
            return loaded

    def unload(self, model_id: str) -> bool:
        """Unload ``model_id``.  Returns ``True`` when something was removed."""
        lock = self._lock_for(model_id)
        with lock:
            loaded = self._instances.pop(model_id, None)
            if loaded is None:
                return False
            try:
                loaded.instance.unload()
            except Exception:
                # Don't let backend unload glitches wedge the registry;
                # the instance is gone from the cache either way.
                pass
            return True

    def reset(self) -> None:
        """Drop every cached instance.  Used by the test suite only.

        Backend ``unload()`` is best-effort — failures are swallowed so
        a stuck backend doesn't keep the registry from clearing.
        """
        with self._global_lock:
            for loaded in list(self._instances.values()):
                try:
                    loaded.instance.unload()
                except Exception:
                    pass
            self._instances.clear()
            self._locks.clear()

    # -- internals ----------------------------------------------------------

    def _lock_for(self, model_id: str) -> threading.Lock:
        with self._global_lock:
            lock = self._locks.get(model_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[model_id] = lock
            return lock


# Module-level singleton -------------------------------------------------

_default_registry = ModelRegistry()


def get_registry() -> ModelRegistry:
    """Return the process-wide :class:`ModelRegistry` singleton."""
    return _default_registry


__all__ = [
    "LoadedModel",
    "ModelRegistry",
    "get_registry",
    # Re-exports so route code only needs one import site.
    "BackendError",
    "BackendUnavailable",
    "ModelNotFound",
    "ModelNotLoaded",
]