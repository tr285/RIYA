"""Strictly allowlisted non-destructive terminal command executor."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

from core.tools.filesystem.files import _is_sensitive_relative_path, _path_context

MAX_ARGS_LENGTH = 512
MAX_COMMAND_OUTPUT_CHARS = 8192
DEFAULT_TERMINAL_TIMEOUT_SECONDS = 15.0

_FORBIDDEN_SHELL_CHARS = frozenset({"&", "|", ";", "`", "$", ">", "<", "\n", "\r", "\x00"})

# Strict read-only / safe local developer verification commands.
# Never expose arbitrary shells (sh/bash/zsh) or destructive OS tools.
_ALLOWLISTED_COMMANDS: dict[str, tuple[str, ...]] = {
    "ls": ("/bin/ls",),
    "pwd": ("/bin/pwd",),
    "wc": ("/usr/bin/wc",),
    "head": ("/usr/bin/head",),
    "tail": ("/usr/bin/tail",),
    "sort": ("/usr/bin/sort",),
    "uniq": ("/usr/bin/uniq",),
    "cat": ("/bin/cat",),
    "stat": ("/usr/bin/stat",),
    "uname": ("/usr/bin/uname",),
    "date": ("/bin/date",),
    "git": ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git"),
    "pytest": (str(Path(sys.executable).parent / "pytest"),),
    "python3": (sys.executable,),
}

_ALLOWED_GIT_SUBCOMMANDS = frozenset({"status", "log", "diff", "branch", "show"})


class TerminalSecurityError(PermissionError):
    """Raised when a terminal command or argument violates RIYA allowlist rules."""


def _resolve_binary(command: str) -> str:
    candidates = _ALLOWLISTED_COMMANDS.get(command)
    if not candidates:
        raise TerminalSecurityError(f"Command '{command}' is not allowlisted.")
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    if command == "pytest":
        # Safe module fallback via sys.executable -m pytest
        return sys.executable
    raise TerminalSecurityError(f"Allowlisted executable for '{command}' was not found on this host.")


_SAFE_FLAG_RE = re.compile(r"^-{1,2}[A-Za-z0-9][A-Za-z0-9_-]{0,31}(?:=(?:short|line|no|auto|[0-9]+))?$")


def _validate_args(command: str, args: str | None, workdir: Path) -> list[str]:
    if args is None:
        parsed: list[str] = []
    elif isinstance(args, str):
        if len(args) > MAX_ARGS_LENGTH:
            raise TerminalSecurityError(f"Arguments exceed {MAX_ARGS_LENGTH} characters.")
        if any(ch in args for ch in _FORBIDDEN_SHELL_CHARS):
            raise TerminalSecurityError("Shell metacharacters are not permitted in terminal arguments.")
        parsed = shlex.split(args)
    else:
        raise TerminalSecurityError("Terminal arguments must be a string.")

    for token in parsed:
        if any(ch in token for ch in _FORBIDDEN_SHELL_CHARS):
            raise TerminalSecurityError("Shell metacharacters are not permitted.")
        if token.startswith("~") or os.path.isabs(token) or ".." in Path(token).parts:
            raise TerminalSecurityError("Path traversal or absolute paths outside the working directory are blocked.")
        if token.startswith("-"):
            if not _SAFE_FLAG_RE.fullmatch(token):
                raise TerminalSecurityError(f"Unsupported flag format: '{token}'")
            if command == "python3" and token in {"-c", "--command", "-i", "--interactive"}:
                raise TerminalSecurityError("Arbitrary inline Python execution (-c) is blocked here; use python_sandbox.")
            if command == "pytest" and token in {"-o", "--override-ini", "--rootdir", "--confcutdir", "-c"}:
                raise TerminalSecurityError(f"Pytest configuration override flag '{token}' is blocked.")
        else:
            rel_parts = Path(token).parts
            if _is_sensitive_relative_path(rel_parts):
                raise TerminalSecurityError(f"Access to sensitive path '{token}' is denied.")
            resolved = (workdir / token).resolve(strict=False)
            if resolved != workdir and workdir not in resolved.parents:
                raise TerminalSecurityError(f"Path '{token}' escapes the allowed working directory.")

    if command == "git":
        sub = next((tok for tok in parsed if not tok.startswith("-")), None)
        if sub not in _ALLOWED_GIT_SUBCOMMANDS:
            raise TerminalSecurityError(
                f"Only read-only git subcommands {sorted(_ALLOWED_GIT_SUBCOMMANDS)} are permitted."
            )

    if command == "python3":
        # Restrict python3 invocations strictly to syntax compilation checks.
        if len(parsed) < 3 or parsed[:2] != ["-m", "py_compile"]:
            raise TerminalSecurityError(
                "Terminal python3 only allows '-m py_compile <relative_file>' checks."
            )

    return parsed


def terminal_command(
    command: str,
    args: str = "",
    path: str = ".",
) -> str:
    """Execute a strictly allowlisted non-destructive command inside an allowed directory."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string.")
    cmd_name = command.strip()
    if any(ch.isspace() for ch in cmd_name):
        raise TerminalSecurityError("Supply the base command in 'command' and flags/targets in 'args'.")

    binary = _resolve_binary(cmd_name)
    ctx = _path_context(path or ".")
    if not ctx.resolved.is_dir():
        raise NotADirectoryError(f"Working directory does not exist: {ctx.resolved}")

    validated_args = _validate_args(cmd_name, args, ctx.resolved)
    argv = (
        [binary, "-m", "pytest", *validated_args]
        if (cmd_name == "pytest" and binary == sys.executable)
        else [binary, *validated_args]
    )

    clean_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(ctx.root),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
    }

    try:
        proc = subprocess.run(
            argv,
            cwd=str(ctx.resolved),
            env=clean_env,
            capture_output=True,
            text=True,
            shell=False,
            timeout=DEFAULT_TERMINAL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"Terminal command '{cmd_name}' timed out after {DEFAULT_TERMINAL_TIMEOUT_SECONDS:.0f}s."
        ) from exc

    combined = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if stderr:
        combined = f"{combined}\n[stderr]\n{stderr}".strip() if combined else f"[stderr]\n{stderr}"
    if len(combined) > MAX_COMMAND_OUTPUT_CHARS:
        combined = combined[:MAX_COMMAND_OUTPUT_CHARS] + "\n...[truncated]"

    if proc.returncode != 0:
        raise RuntimeError(combined or f"Command '{cmd_name}' failed with exit code {proc.returncode}.")
    return combined or f"Command '{cmd_name}' completed with exit code 0."
