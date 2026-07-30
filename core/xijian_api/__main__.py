"""``python -m xijian_api`` entry point.

``python -m xijian_api`` 入口点。
"""

from __future__ import annotations

import sys

from xijian_api.app import main


if __name__ == "__main__":
    # ``sys.argv[0]`` is the script path; argparse expects the args
    # *after* the program name.
    # ``sys.argv[0]`` 是脚本路径；argparse 期望的参数在程序名 *之后*。
    raise SystemExit(main(sys.argv[1:]))
