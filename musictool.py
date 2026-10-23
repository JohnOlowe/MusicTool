#!/usr/bin/env python3
"""Entry point: `python musictool.py <command>` or `./musictool.py <command>`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from musictool.cli import main  # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
