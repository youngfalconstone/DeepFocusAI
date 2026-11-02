"""Launcher used by the packaged executable and for `python run.py`."""
import sys

from deepfocus.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
