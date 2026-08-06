"""独立 DevKit 二进制包的 PyInstaller 入口点。

PyInstaller 将*此*脚本冻结为程序的 ``__main__``。我们把它作为包自身
``__main__.py`` 之外的一个薄垫片（shim），使冻结后的入口拥有稳定、
无歧义的模块名，并让 ``import devkit`` 通过包解析，而不是通过
冻结后的顶层脚本解析。

运行（冻结版）::

    ./dist/xijian-devkit/xijian-devkit        # onedir 二进制
    open "dist/隙间开发者工具.app"              # macOS .app 应用包

运行（源码版，等价）::

    python -m devkit
    python devkit/app.py
"""

from __future__ import annotations

import sys


def _main() -> int:
    from devkit.main import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
