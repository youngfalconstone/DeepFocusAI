"""Entry point for both the packaged executable and `python -m deepfocus`.

    DeepFocus.exe                      -> open the app
    DeepFocus.exe helper <args...>     -> run the elevated worker (used by UAC launch and Task Scheduler)
"""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "helper":
        from .helper import main as helper_main
        return helper_main(sys.argv[2:])
    from .app import main as app_main
    app_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
