"""Entry point for StickOn."""

from __future__ import annotations

import sys

from stickon.app import run_app


def main() -> int:
    return run_app(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
