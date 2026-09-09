"""Run pytest without permitting real network connections."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _deny_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Offline tests must not connect to live sites")


def main() -> int:
    import pytest

    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    socket.create_connection = _deny_network
    return pytest.main(sys.argv[1:] or ["-q", "tests"])


if __name__ == "__main__":
    raise SystemExit(main())
