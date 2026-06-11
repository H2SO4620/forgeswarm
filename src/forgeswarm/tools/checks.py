"""Verification runner: scoped, allowlisted command execution.

This is deliberately NOT an arbitrary code executor — MCP clients already
have those. It runs a small allowlist of build/test/lint commands with a hard
timeout and records the evidence (exit code + output) on the task, so
reviewers judge real check results instead of an agent's claim that
"tests pass".
"""

from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ..store import Store, StoreError

ALLOWED_COMMANDS = {
    "pytest", "python", "python3", "uv", "pip",
    "ruff", "mypy", "black", "flake8",
    "npm", "pnpm", "yarn", "npx", "node", "tsc", "eslint", "jest", "vitest",
    "go", "cargo", "make",
}

MAX_OUTPUT_CHARS = 20_000
MAX_TIMEOUT = 600


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text)} chars total]"


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.tool()
    def run_checks(task_id: int, command: str, cwd: str, timeout_seconds: int = 120) -> dict:
        """Run an allowlisted verification command (tests/linters/builds) for a task.

        Allowed executables: pytest, python, uv, pip, ruff, mypy, black, flake8,
        npm, pnpm, yarn, npx, node, tsc, eslint, jest, vitest, go, cargo, make.
        The command runs without a shell (no pipes/redirects), with a hard
        timeout, and the result is recorded on the task as review evidence.
        Include the relevant output in your submit_for_review content.
        """
        timeout_seconds = min(max(1, timeout_seconds), MAX_TIMEOUT)
        # POSIX-style splitting so quoted args work; put paths in `cwd`, not the command.
        argv = shlex.split(command)
        if not argv:
            raise StoreError("Empty command.")
        exe = Path(argv[0]).name.lower().removesuffix(".exe")
        if exe not in ALLOWED_COMMANDS:
            raise StoreError(
                f"'{exe}' is not in the allowlist {sorted(ALLOWED_COMMANDS)}."
                " run_checks is for verification commands only."
            )
        workdir = Path(cwd)
        if not workdir.is_dir():
            raise StoreError(f"cwd '{cwd}' is not a directory.")

        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
            )
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            exit_code = -1
            stdout = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = f"Timed out after {timeout_seconds}s."
        except FileNotFoundError:
            raise StoreError(f"Executable '{argv[0]}' not found on this machine.")
        duration = round(time.monotonic() - start, 2)

        record = store.record_check(
            task_id, command=command, exit_code=exit_code,
            stdout=_truncate(stdout), stderr=_truncate(stderr), duration_seconds=duration,
        )
        return {
            "check_id": record.id,
            "exit_code": exit_code,
            "passed": exit_code == 0,
            "duration_seconds": duration,
            "stdout": record.stdout,
            "stderr": record.stderr,
        }
