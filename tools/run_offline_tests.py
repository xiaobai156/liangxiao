"""Run pytest without real network connections or external child processes."""
from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _DeniedPopen(subprocess.Popen):
    """Keep Popen subclassable while refusing every child-process launch."""

    def __init__(self, *args: object, **kwargs: object) -> NoReturn:
        raise AssertionError(
            "Offline tests must not connect to live sites or launch child processes"
        )


def _deny_network(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError(
        "Offline tests must not connect to live sites or launch child processes"
    )


def main() -> int:
    # Avoid unrelated workstation plugins; warm up harmless OS metadata first.
    os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    platform.platform()
    import pytest

    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    socket.create_connection = _deny_network
    # Windows' asyncio module subclasses subprocess.Popen during import.  A
    # callable replacement breaks test collection, so retain a real class and
    # reject process creation only when the class is instantiated.
    subprocess.Popen = _DeniedPopen
    return pytest.main(sys.argv[1:] or ["-q", "tests"])


if __name__ == "__main__":
    raise SystemExit(main())
