"""PyInstaller entry point for the standalone DevKit binary.

PyInstaller freezes *this* script as the program's ``__main__``.  We
keep it as a thin shim outside the package's own ``__main__.py`` so the
frozen entry has a stable, unambiguous module name and so ``import
devkit`` resolves through the package rather than through the frozen
top-level script.

Run (frozen)::

    ./dist/xijian-devkit/xijian-devkit        # onedir binary
    open "dist/隙间开发者工具.app"              # macOS .app bundle

Run (from source, equivalent)::

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
