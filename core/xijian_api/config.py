"""Unified configuration loader for the XiJian API server.

XiJian API 服务器的统一配置加载器。

The single source of truth is a TOML file.  By default we look for
``config.toml`` next to the project root, but the location can be
overridden with the ``XIJIAN_CONFIG`` environment variable.

唯一权威来源是一个 TOML 文件。默认情况下我们在项目根目录旁查找 ``config.toml``，
但可以通过 ``XIJIAN_CONFIG`` 环境变量覆盖该路径。

Sections:

* ``[server]`` — host, port, dev flags.
* ``[auth]`` — token-file template.
* ``[storage]`` — base directory plus per-type subfolders (including
  the unified ``models_subdir`` for all model checkpoints).
* ``[backends.<kind>]`` — per-task default/fallback backends.
* ``[ai]`` — runtime defaults shared by every backend (max tokens,
  context length, GGUF tuning, MLX cache location).
* ``[[models]]`` — one row per registered model.  ``filename`` is
  resolved against ``<storage.base_dir>/<storage.models_subdir>/<type>/<id>/``.
* ``[features]`` — optional subsystem toggles.

配置章节说明：
* ``[server]`` — 主机、端口、开发模式标志。
* ``[auth]`` — 令牌文件模板。
* ``[storage]`` — 基础目录及各类型子文件夹（包括所有模型检查点的统一 ``models_subdir``）。
* ``[backends.<kind>]`` — 各任务类型的默认/回退后端。
* ``[ai]`` — 所有后端共用的运行时默认值（最大令牌数、上下文长度、GGUF 调优、MLX 缓存位置）。
* ``[[models]]`` — 每行一个已注册模型。``filename`` 相对于 ``<storage.base_dir>/<storage.models_subdir>/<type>/<id>/`` 解析。
* ``[features]`` — 可选子系统开关。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


API_VERSION = "1.0.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18500

RATE_LIMIT_LIMIT_REQUESTS = 100000
RATE_LIMIT_REMAINING_REQUESTS = 99999
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
DEFAULT_STREAM_FORMAT = "sse"

_MODEL_TYPES = (
    "chat",
    "embeddings",
    "tts",
    "stt",
    "image",
    "video",
    "multimodal",
    "video_understanding",
)


def _config_search_paths() -> list[Path]:
    """Return candidate config file paths in priority order.

    返回按优先级排序的候选配置文件路径。
    """
    paths: list[Path] = []
    env = os.environ.get("XIJIAN_CONFIG")
    if env:
        paths.append(Path(env))
    # 打包模式：可执行文件同级目录的 config.toml 优先
    # Packaged mode: config.toml in the same directory as the executable takes priority
    from xijian_api.runtime import is_frozen, executable_dir
    if is_frozen():
        paths.append(executable_dir() / "config.toml")
    paths.append(Path.cwd() / "config.toml")
    repo_root = Path(__file__).resolve().parent.parent.parent
    paths.append(repo_root / "config.toml")
    return paths


def _truthy(value: Any) -> bool:
    """Convert a value to boolean, treating common truthy strings as True.

    将值转换为布尔值，将常见的真值字符串视为 True。
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Dataclasses
# 数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerConfig:
    """Server-level configuration: host, port, dev mode, etc.

    服务器级配置：主机、端口、开发模式等。
    """
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    dev: bool = False
    keep_token_file: bool = False
    testing: bool = False
    api_version: str = API_VERSION


@dataclass(frozen=True)
class AuthConfig:
    """Authentication configuration: bearer token file path template.

    认证配置：Bearer 令牌文件路径模板。
    """
    token_file: str = "/tmp/xijian-{pid}.token"


@dataclass(frozen=True)
class StorageConfig:
    """Root layout shared by model checkpoints and user uploads.

    模型检查点和用户上传文件的根目录布局。

    All files live under one ``base_dir``; per-type subfolders keep
    things tidy without forcing operators to configure each one.

    所有文件都位于一个 ``base_dir`` 下；按类型划分子文件夹保持整洁，
    无需操作员逐一配置。

    Model checkpoints are rooted at ``<base>/<models_subdir>``; each
    ``[[models]]`` entry resolves to
    ``<base>/<models_subdir>/<type>/<id>/<filename>``.  This is the
    single place operators edit to move all weights to a different
    filesystem (symlink, separate volume, etc.).

    模型检查点根目录为 ``<base>/<models_subdir>``；每个 ``[[models]]`` 条目
    解析为 ``<base>/<models_subdir>/<type>/<id>/<filename>``。
    这是操作员编辑以将所有权重移动到不同文件系统（符号链接、单独卷等）的唯一位置。
    """

    base_dir: str = "~/Library/Application Support/XiJian/Core"
    files_subdir: str = "files"
    models_subdir: str = "models"
    snapshots_subdir: str = "snapshots"
    audit_subdir: str = "audit"
    packs_subdir: str = "packs"

    @property
    def base_path(self) -> Path:
        return Path(os.path.expanduser(self.base_dir))

    @property
    def files_path(self) -> Path:
        return self.base_path / self.files_subdir

    @property
    def models_path(self) -> Path:
        """Single root for every model checkpoint on disk.

        磁盘上所有模型检查点的单一根目录。
        """
        return self.base_path / self.models_subdir

    @property
    def snapshots_path(self) -> Path:
        return self.base_path / self.snapshots_subdir

    @property
    def audit_path(self) -> Path:
        return self.base_path / self.audit_subdir

    @property
    def packs_path(self) -> Path:
        """Root directory for installed resource packs.

        已安装资源包（packs）的根目录。
        """
        return self.base_path / self.packs_subdir

    def ensure_base(self) -> Path:
        """Make sure the base directory exists and return it.

        确保基础目录存在并返回它。
        """
        self.base_path.mkdir(parents=True, exist_ok=True)
        return self.base_path

    def model_dir(self, model_type: str, model_id: str) -> Path:
        """Resolve ``<base>/<models_subdir>/<type>/<id>`` (and create it).

        解析并创建 ``<base>/<models_subdir>/<type>/<id>`` 路径。
        """
        path = self.models_path / model_type / model_id
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass(frozen=True)
class BackendConfig:
    """Per-task backend selection.

    按任务类型的后端选择。
    """

    default: str = ""
    fallbacks: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OpenAIBackendConfig:
    """Global defaults for ``backend = "openai"`` models.

    ``backend = "openai"`` 模型的全局默认值。

    Per-model ``[[models]].extra`` fields override these.  When a field
    is empty the :func:`resolve_config` helper falls back to the
    matching ``OPENAI_*`` environment variable.

    每个模型的 ``[[models]].extra`` 字段会覆盖这些值。当某个字段为空时，
    :func:`resolve_config` 辅助函数会回退到对应的 ``OPENAI_*`` 环境变量。
    """

    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    transport: str = "httpx"  # "httpx" | "openai_sdk"
    headers: dict = field(default_factory=dict)
    video_endpoint: str = "/video/generations"


@dataclass(frozen=True)
class BackendsConfig:
    """All backend configurations under one roof.

    所有后端配置的集合。
    """
    chat: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx", fallbacks=("gguf",)))
    embeddings: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    tts: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    stt: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    image: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    video: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    multimodal: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    # 注意：视频理解后端目前仅 openai/mock 实现（GGUF/MLX 尚无对应后端），
    # 默认指向 openai，避免解析到不存在的后端。
    # NB: video understanding is only implemented for openai/mock so far
    # (GGUF/MLX have no such backend); default to openai to avoid
    # resolving to a non-existent backend.
    video_understanding: BackendConfig = field(default_factory=lambda: BackendConfig(default="openai"))
    openai: OpenAIBackendConfig = field(default_factory=OpenAIBackendConfig)


@dataclass(frozen=True)
class ModelEntry:
    """A single registered model with its metadata.

    单个已注册模型及其元数据。
    """
    id: str
    type: str             # chat | embeddings | tts | stt | image | video | multimodal | video_understanding
    backend: str          # mlx | gguf | openai | mock
    filename: str         # file or directory name under model_dir(type, id)
    family: str = ""
    size_b: float = 0.0
    quant: str = ""
    context_length: int = 0
    min_ram_gb: int = 0
    loaded: bool = False
    extra: dict = field(default_factory=dict)

    def absolute_path(self, storage: StorageConfig) -> Path:
        """Resolve to ``<storage.models_path>/<type>/<id>/<filename>``.

        解析为 ``<storage.models_path>/<type>/<id>/<filename>``。

        Falls back to the file/directory name under
        ``storage.models_path`` when ``type``/``id`` is unknown, so the
        helper is still useful for ad-hoc lookups.

        当 ``type``/``id`` 未知时，回退到 ``storage.models_path`` 下的
        文件/目录名，因此该辅助函数对临时查找仍然有用。
        """
        return storage.model_dir(self.type, self.id) / self.filename

    def to_oai_metadata(self) -> dict:
        """Render the ``xijian`` extension block returned by /v1/models.

        渲染 /v1/models 返回的 ``xijian`` 扩展块。
        """
        meta = {
            "backend": self.backend,
            "family": self.family,
            "size_b": self.size_b,
            "quant": self.quant,
            "context_length": self.context_length,
            "min_ram_gb": self.min_ram_gb,
            "loaded": self.loaded,
            "type": self.type,
            "filename": self.filename,
        }
        meta.update(self.extra)
        return meta


@dataclass(frozen=True)
class AIConfig:
    """Cross-backend runtime defaults.

    跨后端的运行时默认值。

    Backends consult these values when a request does not pass them
    explicitly.  ``mlx_cache_dir`` is the one backend-specific knob we
    expose; the ``gguf_*`` fields are read by the GGUF backend at load
    time.

    当请求未显式传递这些值时，后端会查询这些默认值。
    ``mlx_cache_dir`` 是我们暴露的一个后端特定配置项；``gguf_*`` 字段
    由 GGUF 后端在加载时读取。
    """

    default_max_new_tokens: int = 1024
    default_context_length: int = 8192
    mlx_cache_dir: str = ""
    gguf_n_ctx: int = 4096
    gguf_n_threads: int = 0
    gguf_n_gpu_layers: int = 0


@dataclass(frozen=True)
class FeaturesConfig:
    """Optional feature toggles.

    可选功能开关。
    """
    seed_default_data: bool = False
    protection_module: bool = True
    rate_limit: bool = False
    dev_test_emit: bool = False


@dataclass(frozen=True)
class SnapshotsConfig:
    """A5.3 automatic-backup knobs.

    A5.3 自动备份旋钮。

    * ``compression_backend`` — ``zstd`` / ``zlib`` / ``auto``.
      ``auto`` prefers zstd (spec AC-3) and falls back to zlib
      when the ``zstandard`` package is missing.
    * ``max_single_snapshot_bytes`` — per-snapshot size cap;
      ``None`` keeps the stub's module-level default (500 MiB).
    """
    compression_backend: str = "auto"
    max_single_snapshot_bytes: int | None = None


@dataclass(frozen=True)
class Config:
    """Top-level configuration holding all sub-configs.

    持有所有子配置的顶层配置。
    """
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    backends: BackendsConfig = field(default_factory=BackendsConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    models: tuple[ModelEntry, ...] = field(default_factory=tuple)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    snapshots: SnapshotsConfig = field(default_factory=SnapshotsConfig)
    source_path: str | None = None

    # Convenience properties used by the existing call sites.
    # 现有调用点使用的便捷属性。
    @property
    def host(self) -> str:
        return self.server.host

    @property
    def dev(self) -> bool:
        return self.server.dev

    @property
    def testing(self) -> bool:
        return self.server.testing

    @property
    def keep_token_file(self) -> bool:
        return self.server.keep_token_file

    def model_by_id(self, model_id: str) -> ModelEntry | None:
        """Look up a model by its ID.

        按 ID 查找模型。
        """
        for m in self.models:
            if m.id == model_id:
                return m
        return None

    def models_of_type(self, model_type: str) -> list[ModelEntry]:
        """Return all models of a given type.

        返回给定类型的所有模型。
        """
        return [m for m in self.models if m.type == model_type]

    # Factories --------------------------------------------------------------
    # 工厂方法 --------------------------------------------------------------

    @classmethod
    def empty(cls) -> "Config":
        """Create a Config with all defaults (no TOML file)."""
        return cls()

    @classmethod
    def from_env(cls, *, testing: bool = False) -> "Config":
        """Load configuration from TOML, apply environment variable overrides.

        从 TOML 加载配置，并应用环境变量覆盖。
        """
        data = _load_toml()
        config = _build_config(data, testing=testing)
        # Env overrides for the bits the parent UI process manages.
        # 环境变量覆盖父 UI 进程管理的部分。
        if "XIJIAN_API_PORT" in os.environ:
            object.__setattr__(
                config.server, "port", int(os.environ["XIJIAN_API_PORT"])
            )
        if "XIJIAN_HOST" in os.environ:
            object.__setattr__(
                config.server, "host", os.environ["XIJIAN_HOST"]
            )
        if "XIJIAN_DEV" in os.environ:
            object.__setattr__(
                config.server, "dev", _truthy(os.environ["XIJIAN_DEV"])
            )
        if "XIJIAN_DEV_TOKEN_FILE" in os.environ:
            object.__setattr__(
                config.server,
                "keep_token_file",
                _truthy(os.environ["XIJIAN_DEV_TOKEN_FILE"]),
            )
        return config

    @classmethod
    def from_dict(cls, data: dict, *, testing: bool = False) -> "Config":
        """Build a Config from a raw dictionary (useful in tests).

        从原始字典构建 Config（在测试中很有用）。
        """
        return _build_config(data, testing=testing)

    @classmethod
    def from_file(cls, path: Path, *, testing: bool = False) -> "Config":
        """Build a Config from a TOML file.

        从 TOML 文件构建 Config。
        """
        with Path(path).open("rb") as fp:
            data = tomllib.load(fp)
        return _build_config(data, testing=testing, source_path=str(path))


# ---------------------------------------------------------------------------
# Builders
# 构建器
# ---------------------------------------------------------------------------


def _load_toml() -> dict[str, Any]:
    """Search for and load the first available TOML config file.

    搜索并加载第一个可用的 TOML 配置文件。
    """
    for candidate in _config_search_paths():
        if candidate and candidate.exists():
            with candidate.open("rb") as fp:
                return tomllib.load(fp)
    return {}


def _build_config(
    data: dict[str, Any],
    *,
    testing: bool,
    source_path: str | None = None,
) -> Config:
    """Build a Config from parsed TOML data.

    从解析的 TOML 数据构建 Config。
    """
    server_data = dict(data.get("server", {}))
    # The ``testing`` flag passed in by the caller (e.g. pytest)
    # always overrides whatever the on-disk TOML says.  Previously we
    # used ``setdefault`` which silently lost the override when the
    # file already had ``testing = false`` — that broke the test-suite
    # bootstrap.
    # 调用者传入的 ``testing`` 标志（例如 pytest）始终覆盖磁盘上 TOML 的内容。
    # 之前我们使用 ``setdefault``，当文件已有 ``testing = false`` 时会静默丢失覆盖
    # ——这导致测试套件引导失败。
    server_data["testing"] = bool(testing)
    server = ServerConfig(
        host=server_data.get("host", DEFAULT_HOST),
        port=int(server_data.get("port", DEFAULT_PORT)),
        dev=_truthy(server_data.get("dev")),
        keep_token_file=_truthy(server_data.get("keep_token_file")),
        testing=bool(server_data.get("testing", False)),
        api_version=server_data.get("api_version", API_VERSION),
    )

    auth = AuthConfig(token_file=data.get("auth", {}).get("token_file", AuthConfig.token_file))

    storage_data = dict(data.get("storage", {}))
    # ``XIJIAN_DATA_DIR`` is the unified override for the whole storage
    # root — it wins over the TOML ``base_dir`` (used by tests to keep
    # the suite hermetic, and by power users to relocate everything).
    # ``XIJIAN_DATA_DIR`` 是存储根目录的统一覆盖项 — 优先于 TOML ``base_dir``
    # （测试用它保持套件隔离，高级用户用它整体搬迁数据）。
    base_dir = (
        os.environ.get("XIJIAN_DATA_DIR")
        or storage_data.get("base_dir")
        or "~/Library/Application Support/XiJian/Core"
    )
    storage = StorageConfig(
        base_dir=base_dir,
        files_subdir=storage_data.get("files_subdir", "files"),
        models_subdir=storage_data.get("models_subdir", "models"),
        snapshots_subdir=storage_data.get("snapshots_subdir", "snapshots"),
        audit_subdir=storage_data.get("audit_subdir", "audit"),
        packs_subdir=storage_data.get("packs_subdir", "packs"),
    )

    backends = _build_backends(data.get("backends", {}))

    ai = _build_ai(data.get("ai", {}))

    models = _build_models(data.get("models", []))

    features_data = dict(data.get("features", {}))
    features = FeaturesConfig(
        seed_default_data=_truthy(features_data.get("seed_default_data", False)),
        protection_module=_truthy(features_data.get("protection_module", True)),
        rate_limit=_truthy(features_data.get("rate_limit", False)),
        dev_test_emit=_truthy(features_data.get("dev_test_emit", False)),
    )

    snap_data = dict(data.get("snapshots", {}))
    snapshots = SnapshotsConfig(
        compression_backend=str(snap_data.get("compression_backend", "auto") or "auto"),
        max_single_snapshot_bytes=snap_data.get("max_single_snapshot_bytes"),
    )
    if snapshots.max_single_snapshot_bytes is not None:
        try:
            snapshots = SnapshotsConfig(
                compression_backend=snapshots.compression_backend,
                max_single_snapshot_bytes=int(snapshots.max_single_snapshot_bytes),
            )
        except (TypeError, ValueError):
            raise ValueError(
                "[snapshots] max_single_snapshot_bytes must be an int, got %r"
                % snap_data.get("max_single_snapshot_bytes")
            ) from None
    if snapshots.compression_backend not in {"zstd", "zlib", "auto"}:
        raise ValueError(
            "[snapshots] compression_backend must be zstd|zlib|auto, got %r"
            % snapshots.compression_backend
        )

    if source_path is None:
        for candidate in _config_search_paths():
            if candidate and candidate.exists():
                source_path = str(candidate)
                break

    return Config(
        server=server,
        auth=auth,
        storage=storage,
        backends=backends,
        ai=ai,
        models=models,
        features=features,
        snapshots=snapshots,
        source_path=source_path,
    )


def _build_backends(data: dict[str, Any]) -> BackendsConfig:
    """Build BackendsConfig from the ``[backends]`` TOML section.

    从 ``[backends]`` TOML 章节构建 BackendsConfig。
    """
    kwargs: dict[str, BackendConfig] = {}
    for kind in _MODEL_TYPES:
        block = dict(data.get(kind, {}))
        kwargs[kind] = BackendConfig(
            default=block.get("default", ""),
            fallbacks=tuple(block.get("fallbacks", []) or ()),
        )
    # Optional [backends.openai] global section.
    # 可选的 [backends.openai] 全局章节。
    oai_block = dict(data.get("openai", {}))
    kwargs["openai"] = OpenAIBackendConfig(
        base_url=str(oai_block.get("base_url", "") or ""),
        api_key=str(oai_block.get("api_key", "") or ""),
        default_model=str(oai_block.get("default_model", "") or ""),
        transport=str(oai_block.get("transport", "httpx") or "httpx"),
        headers=dict(oai_block.get("headers", {}) or {}),
        video_endpoint=str(oai_block.get("video_endpoint", "/video/generations") or "/video/generations"),
    )
    return BackendsConfig(**kwargs)


def _build_ai(data: dict[str, Any]) -> AIConfig:
    """Build AIConfig from the ``[ai]`` TOML section.

    从 ``[ai]`` TOML 章节构建 AIConfig。
    """
    data = dict(data or {})
    return AIConfig(
        default_max_new_tokens=int(data.get("default_max_new_tokens", 1024)),
        default_context_length=int(data.get("default_context_length", 8192)),
        mlx_cache_dir=str(data.get("mlx_cache_dir", "") or ""),
        gguf_n_ctx=int(data.get("gguf_n_ctx", 4096)),
        gguf_n_threads=int(data.get("gguf_n_threads", 0)),
        gguf_n_gpu_layers=int(data.get("gguf_n_gpu_layers", 0)),
    )


def _build_models(items: list[Any]) -> tuple[ModelEntry, ...]:
    """Build :class:`ModelEntry` records from the ``[[models]]`` array.

    从 ``[[models]]`` 数组构建 :class:`ModelEntry` 记录。

    Each entry must declare ``id``, ``type``, and ``backend``.  The
    on-disk location is taken from ``filename`` (preferred) — resolved
    against ``<storage.models_path>/<type>/<id>/<filename>`` — or from
    the legacy ``path`` field when only that is present.

    每个条目必须声明 ``id``、``type`` 和 ``backend``。磁盘位置取自 ``filename``（首选）
    ——相对于 ``<storage.models_path>/<type>/<id>/<filename>`` 解析——
    或仅在存在旧版 ``path`` 字段时使用该字段。
    """
    out: list[ModelEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not {"id", "type", "backend"}.issubset(item):
            continue
        # Resolve the on-disk name.  Prefer ``filename``; fall back to
        # the legacy ``path`` field for older configs.
        # 解析磁盘名称：优先使用 ``filename``，回退到旧版 ``path`` 字段。
        filename = str(
            item.get("filename")
            or item.get("path")
            or item["id"]
        )
        known = {
            "id", "type", "backend", "filename", "path",
            "family", "size_b", "quant",
            "context_length", "min_ram_gb", "loaded",
        }
        extra = {k: v for k, v in item.items() if k not in known}
        out.append(
            ModelEntry(
                id=str(item["id"]),
                type=str(item["type"]),
                backend=str(item["backend"]),
                filename=filename,
                family=str(item.get("family", "")),
                size_b=float(item.get("size_b", 0.0) or 0.0),
                quant=str(item.get("quant", "")),
                context_length=int(item.get("context_length", 0) or 0),
                min_ram_gb=int(item.get("min_ram_gb", 0) or 0),
                loaded=bool(item.get("loaded", False)),
                extra=extra,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Helpers
# 辅助函数
# ---------------------------------------------------------------------------


def token_file_path(pid: int | None = None, template: str | None = None) -> Path:
    """Resolve the bearer token file path.

    解析 Bearer 令牌文件路径。
    """
    if pid is None:
        pid = os.getpid()
    if template:
        return Path(template.format(pid=pid))
    # 打包模式：使用可执行文件同级的 run/ 目录，避免 /tmp 被系统清理
    # Packaged mode: use the run/ directory alongside the executable to avoid /tmp being cleaned
    from xijian_api.runtime import is_frozen, default_token_file
    if is_frozen():
        return default_token_file(pid)
    return Path(f"/tmp/xijian-{pid}.token")


__all__ = [
    "API_VERSION",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "RATE_LIMIT_LIMIT_REQUESTS",
    "RATE_LIMIT_REMAINING_REQUESTS",
    "IDEMPOTENCY_TTL_SECONDS",
    "DEFAULT_STREAM_FORMAT",
    "ServerConfig",
    "AuthConfig",
    "StorageConfig",
    "BackendConfig",
    "BackendsConfig",
    "OpenAIBackendConfig",
    "ModelEntry",
    "AIConfig",
    "FeaturesConfig",
    "SnapshotsConfig",
    "Config",
    "token_file_path",
]
