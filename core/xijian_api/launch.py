"""PyInstaller-packaged entry point.

PyInstaller 打包后的入口点。

This module is referenced by the PyInstaller spec file's
``entry_script`` as the executable entry point.  Before calling
:func:`xijian_api.app.main` it performs the following initialisation:

本模块由 PyInstaller spec 文件的 ``entry_script`` 指定为可执行
文件的入口。它在调用 :func:`xijian_api.app.main` 之前完成以下
初始化工作：

1. Add ``external_libs/`` to ``sys.path`` (on-demand AI extension packs)
   将 ``external_libs/`` 加入 ``sys.path``（按需加载 AI 扩展包）
2. Create runtime directories (``logs/``, ``run/``, ``data/``)
   创建运行时目录（``logs/``、``run/``、``data/``）
3. Set default environment variables (only when not explicitly overridden)
   - ``XIJIAN_LOG_FILE`` → ``<exe_dir>/logs/xijian-api.log``
   - ``XIJIAN_DEV_TOKEN_FILE`` → ``1`` (always auto-generate token in packaged mode)
   - ``XIJIAN_CONFIG`` → ``<exe_dir>/config.toml`` (if it exists)
   设置默认环境变量（仅当用户未显式指定时）
   - ``XIJIAN_LOG_FILE`` → ``<exe_dir>/logs/xijian-api.log``
   - ``XIJIAN_DEV_TOKEN_FILE`` → ``1``（打包模式下始终自动生成 token）
   - ``XIJIAN_CONFIG`` → ``<exe_dir>/config.toml``（如果存在）

These defaults ensure the packaged executable can start with zero
configuration.  Users can still override via CLI args or environment
variables.

这些默认值确保打包后的可执行文件在零配置下也能启动。用户仍可
通过命令行参数或环境变量覆盖。
"""

from __future__ import annotations

import os
import sys


def _bootstrap() -> None:
    """Early initialisation for packaged mode.

    打包模式下的早期初始化。
    """
    from xijian_api import runtime

    # 1. Inject external dependency directory
    # 1. 注入外部依赖目录
    runtime.setup_external_libs()

    # 2. Create runtime directories
    # 2. 创建运行时目录
    runtime.ensure_runtime_dirs()

    # 3. Set default env vars (only when not explicitly overridden)
    # 3. 设置默认环境变量（仅当用户未显式指定时）
    if not os.environ.get("XIJIAN_LOG_FILE"):
        os.environ["XIJIAN_LOG_FILE"] = str(runtime.default_log_file())

    # Always auto-generate token in packaged mode (no pre-provisioned token file needed)
    # 打包模式下始终自动生成 token（用户无需预置 token 文件）
    os.environ.setdefault("XIJIAN_DEV_TOKEN_FILE", "1")

    # Auto-use config.toml if present alongside the executable
    # 如果可执行文件同级目录有 config.toml，则自动使用它
    cfg = runtime.default_config_path()
    if cfg.is_file() and not os.environ.get("XIJIAN_CONFIG"):
        os.environ["XIJIAN_CONFIG"] = str(cfg)


def main() -> int:
    """Packaged-mode entry function.

    打包模式入口函数。
    """
    _bootstrap()
    from xijian_api.app import main as app_main

    return app_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
