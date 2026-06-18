"""Unified configuration loader for the XiJian API server.

The single source of truth is a TOML file.  By default we look for
``config.toml`` next to the project root, but the location can be
overridden with the ``XIJIAN_CONFIG`` environment variable.

Sections:

* ``[server]`` — host, port, dev flags.
* ``[auth]`` — token-file template.
* ``[storage]`` — base directory plus per-type subfolders.
* ``[backends.<kind>]`` — per-task default/fallback backends.
* ``[[models]]`` — one row per registered model.
* ``[features]`` — optional subsystem toggles.
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

_MODEL_TYPES = ("chat", "embeddings", "tts", "stt", "image", "video")


def _config_search_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("XIJIAN_CONFIG")
    if env:
        paths.append(Path(env))
    paths.append(Path.cwd() / "config.toml")
    repo_root = Path(__file__).resolve().parent.parent.parent
    paths.append(repo_root / "config.toml")
    return paths


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    dev: bool = False
    keep_token_file: bool = False
    testing: bool = False
    api_version: str = API_VERSION


@dataclass(frozen=True)
class AuthConfig:
    token_file: str = "/tmp/xijian-{pid}.token"


@dataclass(frozen=True)
class StorageConfig:
    """Root layout shared by model checkpoints and user uploads.

    All files live under one ``base_dir``; per-type subfolders keep
    things tidy without forcing operators to configure each one.
    """

    base_dir: str = "~/.xijian"
    files_subdir: str = "files"
    snapshots_subdir: str = "snapshots"
    audit_subdir: str = "audit"

    @property
    def base_path(self) -> Path:
        return Path(os.path.expanduser(self.base_dir))

    @property
    def files_path(self) -> Path:
        return self.base_path / self.files_subdir

    @property
    def snapshots_path(self) -> Path:
        return self.base_path / self.snapshots_subdir

    @property
    def audit_path(self) -> Path:
        return self.base_path / self.audit_subdir

    def ensure_base(self) -> Path:
        """Make sure the base directory exists and return it."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        return self.base_path

    def model_dir(self, model_type: str, model_id: str) -> Path:
        """Resolve ``<base>/<type>/<model_id>`` (and create it)."""
        path = self.base_path / model_type / model_id
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass(frozen=True)
class BackendConfig:
    """Per-task backend selection."""

    default: str = ""
    fallbacks: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BackendsConfig:
    chat: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx", fallbacks=("gguf",)))
    embeddings: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    tts: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    stt: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    image: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))
    video: BackendConfig = field(default_factory=lambda: BackendConfig(default="mlx"))


@dataclass(frozen=True)
class ModelEntry:
    id: str
    type: str             # chat | embeddings | tts | stt | image | video
    backend: str          # mlx | gguf
    path: str             # absolute or relative-to-base_dir
    family: str = ""
    size_b: float = 0.0
    quant: str = ""
    context_length: int = 0
    min_ram_gb: int = 0
    loaded: bool = False
    extra: dict = field(default_factory=dict)

    def absolute_path(self, storage: StorageConfig) -> Path:
        p = Path(os.path.expanduser(self.path))
        if not p.is_absolute():
            p = storage.base_path / p
        return p

    def to_oai_metadata(self) -> dict:
        """Render the ``xijian`` extension block returned by /v1/models."""
        meta = {
            "backend": self.backend,
            "family": self.family,
            "size_b": self.size_b,
            "quant": self.quant,
            "context_length": self.context_length,
            "min_ram_gb": self.min_ram_gb,
            "loaded": self.loaded,
            "type": self.type,
            "path": self.path,
        }
        meta.update(self.extra)
        return meta


@dataclass(frozen=True)
class FeaturesConfig:
    seed_default_data: bool = False
    protection_module: bool = True
    rate_limit: bool = False
    dev_test_emit: bool = False


@dataclass(frozen=True)
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    backends: BackendsConfig = field(default_factory=BackendsConfig)
    models: tuple[ModelEntry, ...] = field(default_factory=tuple)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    source_path: str | None = None

    # Convenience properties used by the existing call sites.
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
        for m in self.models:
            if m.id == model_id:
                return m
        return None

    def models_of_type(self, model_type: str) -> list[ModelEntry]:
        return [m for m in self.models if m.type == model_type]

    # Factories --------------------------------------------------------------

    @classmethod
    def empty(cls) -> "Config":
        return cls()

    @classmethod
    def from_env(cls, *, testing: bool = False) -> "Config":
        data = _load_toml()
        config = _build_config(data, testing=testing)
        # Env overrides for the bits the parent UI process manages.
        if "XIJIAN_API_PORT" in os.environ:
            object.__setattr__(
                config.server, "port", int(os.environ["XIJIAN_API_PORT"])
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
        return _build_config(data, testing=testing)

    @classmethod
    def from_file(cls, path: Path, *, testing: bool = False) -> "Config":
        with Path(path).open("rb") as fp:
            data = tomllib.load(fp)
        return _build_config(data, testing=testing, source_path=str(path))


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _load_toml() -> dict[str, Any]:
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
    server_data = dict(data.get("server", {}))
    if testing:
        server_data.setdefault("testing", True)
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
    storage = StorageConfig(
        base_dir=storage_data.get("base_dir", "~/.xijian"),
        files_subdir=storage_data.get("files_subdir", "files"),
        snapshots_subdir=storage_data.get("snapshots_subdir", "snapshots"),
        audit_subdir=storage_data.get("audit_subdir", "audit"),
    )

    backends = _build_backends(data.get("backends", {}))

    models = _build_models(data.get("models", []))

    features_data = dict(data.get("features", {}))
    features = FeaturesConfig(
        seed_default_data=_truthy(features_data.get("seed_default_data", False)),
        protection_module=_truthy(features_data.get("protection_module", True)),
        rate_limit=_truthy(features_data.get("rate_limit", False)),
        dev_test_emit=_truthy(features_data.get("dev_test_emit", False)),
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
        models=models,
        features=features,
        source_path=source_path,
    )


def _build_backends(data: dict[str, Any]) -> BackendsConfig:
    kwargs: dict[str, BackendConfig] = {}
    for kind in _MODEL_TYPES:
        block = dict(data.get(kind, {}))
        kwargs[kind] = BackendConfig(
            default=block.get("default", ""),
            fallbacks=tuple(block.get("fallbacks", []) or ()),
        )
    return BackendsConfig(**kwargs)


def _build_models(items: list[Any]) -> tuple[ModelEntry, ...]:
    out: list[ModelEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not {"id", "type", "backend"}.issubset(item):
            continue
        known = {
            "id", "type", "backend", "path",
            "family", "size_b", "quant",
            "context_length", "min_ram_gb", "loaded",
        }
        extra = {k: v for k, v in item.items() if k not in known}
        out.append(
            ModelEntry(
                id=str(item["id"]),
                type=str(item["type"]),
                backend=str(item["backend"]),
                path=str(item.get("path") or item["id"]),
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
# ---------------------------------------------------------------------------


def token_file_path(pid: int | None = None, template: str | None = None) -> Path:
    if pid is None:
        pid = os.getpid()
    tmpl = template or "/tmp/xijian-{pid}.token"
    return Path(tmpl.format(pid=pid))


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
    "ModelEntry",
    "FeaturesConfig",
    "Config",
    "token_file_path",
]
