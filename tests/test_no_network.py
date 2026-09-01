"""Privacy guarantee test: the CLI must complete with all socket creation blocked.

This is the executable proof of the "nothing leaves your machine" claim — if
any future dependency or code path attempts a network call, the guard exits
with code 88 and this test fails.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"


def test_cli_runs_with_no_network(tmp_path: Path):
    probe = (
        "import socket, sys, runpy;"
        "socket.socket = lambda *a, **k: sys.exit(88);"
        "socket.create_connection = lambda *a, **k: sys.exit(88);"
        "sys.argv = ['mindprint', %r, '-o', %r];"
        "runpy.run_module('mindprint.cli', run_name='__main__')"
        % (str(FIXTURES / "chatgpt_export.zip"), str(tmp_path / "out"))
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"CLI failed (or attempted network access, rc=88) with sockets blocked: "
        f"rc={proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert (tmp_path / "out" / "mindprint.json").exists()
    assert (tmp_path / "out" / "mindprint.md").exists()
