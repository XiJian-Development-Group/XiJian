"""进程级已加载模型实例注册表。

每个 :mod:`xijian_api.ai.backends` 中的后端实例处理一个已加载的检查点；
本注册表将 ``model_id``（在 ``config.toml`` 的 ``[[models]]`` 块中声明）
映射到已完成 :meth:`Backend.load` 的活后端实例。

本模块位于 :mod:`xijian_api.ai.registry` 之上，后者仅编目后端*类*
（mlx / gguf / mock / ...）。加载模型遵循四步流程：

1. 在 ``config.model_by_id(model_id)`` 中查找 :class:`ModelEntry`。
2. 为 ``entry.type`` + ``entry.backend`` 选择后端类
   （后者通常为 ``"mlx"`` 或 ``"gguf"``；测试也会注册合成的 ``"mock"`` 后端）。
3. 实例化该类，并通过 :meth:`ModelEntry.absolute_path` 解析磁盘路径，
   使所有模型都落在单一 ``<storage.base_dir>/<storage.models_subdir>`` 根目录下。
4. 用路径 + kwargs（默认上下文长度、模型 ``extra`` 块、调用者提供的覆盖参数）
   调用 :meth:`Backend.load`。

注册表是进程级单例 —— 见 :func:`get_registry`。
测试使用 :meth:`ModelRegistry.reset` 清空缓存。

Process-wide registry of loaded model instances.

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
    get_multimodal_backend,
    get_stt_backend,
    get_tts_backend,
    get_video_backend,
    get_video_understanding_backend,
)
from xijian_api.config import Config, ModelEntry


_TASK_GETTERS = {
    "chat": get_chat_backend,
    "embeddings": get_embedding_backend,
    "tts": get_tts_backend,
    "stt": get_stt_backend,
    "image": get_image_backend,
    "video": get_video_backend,
    "video_understanding": get_video_understanding_backend,
    "multimodal": get_multimodal_backend,
}


@dataclass
class LoadedModel:
    """一个活的、可调用的后端实例及其配置条目。

    A live, ready-to-call backend instance plus its config entry.
    """

    entry: ModelEntry
    instance: Any            # ChatBackend | EmbeddingBackend | ... — 运行时类型
    absolute_path: Path

    @property
    def task(self) -> str:
        return self.entry.type

    @property
    def backend_name(self) -> str:
        return self.entry.backend


class ModelRegistry:
    """进程级已加载模型实例池。Process-wide pool of loaded model instances."""

    def __init__(self) -> None:
        self._instances: dict[str, LoadedModel] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    # -- introspection / 内省 ------------------------------------------------------

    def list_loaded(self) -> list[str]:
        """返回当前已加载的每个 ``model_id`` 的排序列表。

        Return sorted ``model_id`` of every currently loaded model.
        """
        return sorted(self._instances.keys())

    def is_loaded(self, model_id: str) -> bool:
        return model_id in self._instances

    def get(self, model_id: str) -> LoadedModel:
        """返回 ``model_id`` 对应的活 :class:`LoadedModel`。

        当缓存中无实例时抛出 :class:`ModelNotLoaded`。

        Return the live :class:`LoadedModel` for ``model_id``.

        Raises :class:`ModelNotLoaded` when no instance is cached.
        """
        try:
            return self._instances[model_id]
        except KeyError as exc:
            raise ModelNotLoaded(f"model not loaded: {model_id}") from exc

    def entries(self) -> list[LoadedModel]:
        """返回每个已加载的 :class:`LoadedModel`（供 /v1/models 使用）。

        Return every loaded :class:`LoadedModel` (used by /v1/models).
        """
        return list(self._instances.values())

    # -- helpers / 辅助 ------------------------------------------------------------

    @staticmethod
    def entry_for(config: Config, model_id: str) -> ModelEntry:
        """按 id 查找 :class:`ModelEntry`；未找到抛出 :class:`ModelNotFound`。

        Look up a :class:`ModelEntry` by id; raise :class:`ModelNotFound`.
        """
        entry = config.model_by_id(model_id)
        if entry is None:
            raise ModelNotFound(
                f"model not registered in config: {model_id}",
                code="model_not_found",
            )
        return entry

    @staticmethod
    def _resolve_backend_class(task: str, backend_name: str) -> type:
        """查找 ``task``/``backend_name`` 对应的后端类。

        使用公共注册表助手，它们会回退到 ``is_available()``，
        并在请求的后端无法运行（如 Linux 上的 ``mlx``）时抛出
        :class:`BackendUnavailable`。

        Find the backend class for ``task``/``backend_name``.

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
        # 空回退列表 → 必须直接使用 ``backend_name``；不做静默替换。
        # 任何 ``BackendUnavailable`` 向上传播。
        instance = getter(name=backend_name, fallbacks=())
        return type(instance)

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(
        self,
        model_id: str,
        *,
        config: Config,
        **kwargs: Any,
    ) -> LoadedModel:
        """将 ``model_id`` 加载到后端实例并缓存。

        返回生成的 :class:`LoadedModel`。幂等：当同一 ``model_id``
        已加载时返回现有实例；kwargs 被忽略（需先 :meth:`unload` 再按新选项重载）。

        ``kwargs`` 会在从 :class:`ModelEntry` 派生的默认值之后
        转发给 :meth:`Backend.load`（调用者优先）。

        Load ``model_id`` into a backend instance and cache it.

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
            # [[models]] 表的 extra 字段先进入；调用者的 kwargs 覆盖，
            # 这模仿了注册表典型的 config + request 合并行为。
            load_kwargs.update(entry.extra)
            load_kwargs.update(kwargs)
            if entry.context_length and "context_length" not in load_kwargs:
                load_kwargs["context_length"] = entry.context_length

            # 对 ``backend = "openai"`` 的模型，传递全局 ``[backends.openai]``
            # 以便 :func:`resolve_config` 可将逐模型覆盖与全局默认合并。
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
        """卸载 ``model_id``。有移除返回 ``True``。

        Unload ``model_id``.  Returns ``True`` when something was removed.
        """
        lock = self._lock_for(model_id)
        with lock:
            loaded = self._instances.pop(model_id, None)
            if loaded is None:
                return False
            try:
                loaded.instance.unload()
            except Exception:
                # 别让后端卸载故障卡住注册表；
                # 实例无论如何已从缓存移除。
                pass
            return True

    def reset(self) -> None:
        """清空所有缓存实例。仅测试套件使用。

        后端 ``unload()`` 尽力而为 —— 失败被吞掉，以免卡住的后端
        阻止注册表清空。

        Drop every cached instance.  Used by the test suite only.

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

    # -- internals / 内部 ----------------------------------------------------------

    def _lock_for(self, model_id: str) -> threading.Lock:
        with self._global_lock:
            lock = self._locks.get(model_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[model_id] = lock
            return lock


# Module-level singleton / 模块级单例 -------------------------------------------------

_default_registry = ModelRegistry()


def get_registry() -> ModelRegistry:
    """返回进程级 :class:`ModelRegistry` 单例。

    Return the process-wide :class:`ModelRegistry` singleton.
    """
    return _default_registry


__all__ = [
    "LoadedModel",
    "ModelRegistry",
    "get_registry",
    # Re-exports so route code only needs one import site.
    # 重导出，路由代码只需一个导入点。
    "BackendError",
    "BackendUnavailable",
    "ModelNotFound",
    "ModelNotLoaded",
]