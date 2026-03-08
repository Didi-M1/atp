"""Shell and filesystem utilities shared across all test modules."""
from __future__ import annotations

import subprocess
from pathlib import Path


def run_cmd(
    cmd: str | list[str],
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run a shell command.

    Args:
        cmd:     String (run via sh -c) or list (execvp, no shell).
        timeout: Maximum seconds to wait before raising subprocess.TimeoutExpired.

    Returns:
        (returncode, stdout.strip(), stderr.strip())
    """
    is_shell = isinstance(cmd, str)
    result = subprocess.run(
        cmd,
        shell=is_shell,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def read_file(path: str) -> str | None:
    """Read a text file (sysfs, procfs, …).

    Returns the stripped content, or None if the file cannot be read.
    """
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def tool_exists(name: str) -> bool:
    """Return True if *name* is found on PATH (via `which`)."""
    rc, _, _ = run_cmd(f"which {name}")
    return rc == 0


def glob_paths(pattern: str) -> list[str]:
    """Expand a shell glob pattern and return matching paths as strings."""
    import glob as _glob
    return _glob.glob(pattern)
