"""``python -m xijian_api.devkit`` entry point."""

from __future__ import annotations

import sys

from xijian_api.devkit.main import main as _main


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
