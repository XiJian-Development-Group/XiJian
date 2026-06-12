"""``python -m xijian_api`` entry point."""

from __future__ import annotations

import sys

from xijian_api.app import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))