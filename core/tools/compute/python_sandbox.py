"""Strictly sandboxed Python snippet execution tool."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_SANDBOX_CODE_CHARS = 8000
MAX_SANDBOX_OUTPUT_CHARS = 4096
DEFAULT_SANDBOX_TIMEOUT_SECONDS = 5.0
MAX_SANDBOX_TIMEOUT_SECONDS = 15.0

_ALLOWED_IMPORTS = frozenset(
    {
        "collections",
        "datetime",
        "decimal",
        "fractions",
        "functools",
        "itertools",
        "json",
        "math",
        "re",
        "statistics",
        "string",
    }
)

_FORBIDDEN_NAMES = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "dir",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "memoryview",
        "open",
        "quit",
        "setattr",
        "vars",
    }
)

_FORBIDDEN_CALL_ATTRIBUTES = frozenset(
    {
        "system",
        "popen",
        "spawn",
        "fork",
        "execv",
        "execl",
        "unlink",
        "rmdir",
        "remove",
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
    }
)


class PythonSandboxSecurityError(PermissionError):
    """Raised when sandboxed Python code violates static or runtime boundaries."""


def validate_sandbox_code(code: str) -> str:
    """Inspect AST to reject unsafe imports, host I/O, reflection, or dunder access."""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Python sandbox code must be a non-empty string.")
    if len(code) > MAX_SANDBOX_CODE_CHARS:
        raise ValueError(f"Python sandbox code exceeds {MAX_SANDBOX_CODE_CHARS} characters.")

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"Syntax error in sandbox code: {exc.msg} (line {exc.lineno})") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".", 1)[0]
                if root_mod not in _ALLOWED_IMPORTS:
                    raise PythonSandboxSecurityError(
                        f"Import '{alias.name}' is not allowed in the Python sandbox."
                    )
        elif isinstance(node, ast.ImportFrom):
            root_mod = (node.module or "").split(".", 1)[0]
            if node.level != 0 or root_mod not in _ALLOWED_IMPORTS:
                raise PythonSandboxSecurityError(
                    f"Import from '{node.module}' is not allowed in the Python sandbox."
                )
        elif isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_NAMES or node.id.startswith("__"):
                raise PythonSandboxSecurityError(
                    f"Identifier '{node.id}' is not allowed in the Python sandbox."
                )
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in _FORBIDDEN_CALL_ATTRIBUTES:
                raise PythonSandboxSecurityError(
                    f"Attribute access '.{node.attr}' is not allowed in the Python sandbox."
                )
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            raise PythonSandboxSecurityError(
                "global/nonlocal statements are not permitted in the Python sandbox."
            )

    return code


_SANDBOX_RUNNER_HEADER = """
import builtins
import json
import sys

try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, ({cpu_limit}, {cpu_limit}))
    # 256 MB address space / data guard on platforms supporting RLIMIT_DATA
    try:
        resource.setrlimit(resource.RLIMIT_DATA, (256 * 1024 * 1024, 256 * 1024 * 1024))
    except Exception:
        pass
except Exception:
    pass

_SAFE_BUILTINS = {{
    "abs": builtins.abs,
    "all": builtins.all,
    "any": builtins.any,
    "bool": builtins.bool,
    "dict": builtins.dict,
    "divmod": builtins.divmod,
    "enumerate": builtins.enumerate,
    "filter": builtins.filter,
    "float": builtins.float,
    "frozenset": builtins.frozenset,
    "int": builtins.int,
    "isinstance": builtins.isinstance,
    "len": builtins.len,
    "list": builtins.list,
    "map": builtins.map,
    "max": builtins.max,
    "min": builtins.min,
    "pow": builtins.pow,
    "print": builtins.print,
    "range": builtins.range,
    "repr": builtins.repr,
    "reversed": builtins.reversed,
    "round": builtins.round,
    "set": builtins.set,
    "slice": builtins.slice,
    "sorted": builtins.sorted,
    "str": builtins.str,
    "sum": builtins.sum,
    "tuple": builtins.tuple,
    "zip": builtins.zip,
    "Exception": builtins.Exception,
    "ValueError": builtins.ValueError,
    "TypeError": builtins.TypeError,
    "ZeroDivisionError": builtins.ZeroDivisionError,
}}

_ALLOWED_IMPORTS = {allowed_imports!r}
_ORIG_IMPORT = builtins.__import__

def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0 or name.split(".", 1)[0] not in _ALLOWED_IMPORTS:
        raise ImportError(f"Blocked import in sandbox: {{name}}")
    return _ORIG_IMPORT(name, globals, locals, fromlist, level)

_SAFE_BUILTINS["__import__"] = _guarded_import

_USER_CODE = {user_code!r}
_ENV = {{"__builtins__": _SAFE_BUILTINS}}
exec(compile(_USER_CODE, "<sandbox>", "exec"), _ENV, _ENV)
if "result" in _ENV:
    print("__RIYA_RESULT__=" + repr(_ENV["result"]))
"""


def python_sandbox(
    code: str,
    timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS,
) -> str:
    """Execute validated Python snippet in an isolated subprocess with tight limits."""
    validated_code = validate_sandbox_code(code)
    timeout = max(0.5, min(float(timeout_seconds), MAX_SANDBOX_TIMEOUT_SECONDS))
    cpu_limit = max(2, int(timeout) + 1)

    runner_source = _SANDBOX_RUNNER_HEADER.format(
        cpu_limit=cpu_limit,
        allowed_imports=sorted(_ALLOWED_IMPORTS),
        user_code=validated_code,
    )

    with tempfile.TemporaryDirectory(prefix="riya_py_sandbox_") as sandbox_dir:
        script_path = Path(sandbox_dir) / "sandbox_run.py"
        script_path.write_text(runner_source, encoding="utf-8")
        clean_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": sandbox_dir,
            "TMPDIR": sandbox_dir,
        }
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(script_path)],
                cwd=sandbox_dir,
                env=clean_env,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Python sandbox timed out after {timeout:.1f}s.") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if len(stdout) > MAX_SANDBOX_OUTPUT_CHARS:
        stdout = stdout[:MAX_SANDBOX_OUTPUT_CHARS] + "\n...[truncated]"
    if len(stderr) > MAX_SANDBOX_OUTPUT_CHARS:
        stderr = stderr[:MAX_SANDBOX_OUTPUT_CHARS] + "\n...[truncated]"

    if proc.returncode != 0:
        raise RuntimeError(stderr or f"Sandbox exited with code {proc.returncode}.")

    if not stdout:
        return "Python sandbox completed with no output."

    result_marker = "__RIYA_RESULT__="
    lines = stdout.splitlines()
    for line in lines:
        if line.startswith(result_marker) and len(lines) == 1:
            return line[len(result_marker):]
    return stdout
