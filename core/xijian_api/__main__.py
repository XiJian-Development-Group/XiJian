"""``python -m xijian_api`` entry point."""

from __future__ import annotations

import sys

from xijian_api.app import main


if __name__ == "__main__":
    # ``sys.argv[0]`` is the script path; argparse expects the args
    # *after* the program name.
    raise SystemExit(main(sys.argv[1:]))
