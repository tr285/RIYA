"""AST and static security analysis for controlled self-improvement proposals."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re


PROTECTED_RELATIVE_PREFIXES: tuple[str, ...] = (
    "core/security/",
    "core/identity/",
    "core/database/",
    "data/",
    "models/",
    ".git/",
)

_FORBIDDEN_CALL_NAMES = frozenset({"eval", "exec", "compile", "__import__"})
_FORBIDDEN_MODULE_IMPORTS = frozenset(
    {
        "urllib",
        "urllib.request",
        "requests",
        "httpx",
        "http.client",
        "ftplib",
        "telnetlib",
        "socket",
        "pickle",
        "marshal",
    }
)
_DANGEROUS_ATTR_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "remove"),
    ("os", "unlink"),
    ("shutil", "rmtree"),
    ("pickle", "loads"),
    ("pickle", "load"),
    ("marshal", "loads"),
    ("yaml", "load"),
}
_SUSPICIOUS_SECURITY_RE = re.compile(
    r"\b(?:ConfirmationManager|PermissionManager|SecurityManager)\b.*\b(?:bypass|disable|allow_all|skip)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StaticSecurityReport:
    """Outcome of AST syntax and static security review on proposed code."""

    syntax_ok: bool
    security_ok: bool
    risk_level: str  # "low" | "medium" | "high" | "blocked"
    findings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.syntax_ok and self.security_ok


def resolve_safe_repo_target(project_root: Path, relative_path: str) -> tuple[Path, str]:
    """Validate and resolve a workspace-relative Python path inside project_root."""
    root = Path(project_root).expanduser().resolve()
    raw = str(relative_path or "").strip()
    if not raw or "\x00" in raw or raw.startswith("/"):
        raise ValueError(f"Target path must be relative to project root: {relative_path!r}")

    candidate = (root / raw).resolve()
    try:
        rel = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Target path escapes project root: {relative_path!r}") from exc

    # Ensure existing path components do not traverse via symlinks outside root.
    check_path = root
    for part in Path(raw).parts:
        if part in {"..", ".", ""}:
            if part == "..":
                raise ValueError(f"Path traversal ('..') is forbidden: {relative_path!r}")
            continue
        check_path = check_path / part
        if check_path.is_symlink():
            raise ValueError(f"Symlink targets are forbidden in self-improvement: {relative_path!r}")

    if not rel.endswith(".py"):
        raise ValueError("Self-improvement proposals currently only support .py modules.")

    for prefix in PROTECTED_RELATIVE_PREFIXES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            raise PermissionError(
                f"Target module '{rel}' is security-critical and cannot be modified by self-improvement."
            )

    return candidate, rel


def analyze_proposed_code(relative_path: str, proposed_content: str) -> StaticSecurityReport:
    """Run AST syntax validation and conservative static security inspection."""
    findings: list[str] = []
    syntax_ok = True
    security_ok = True

    if not isinstance(proposed_content, str) or not proposed_content.strip():
        return StaticSecurityReport(
            syntax_ok=False,
            security_ok=False,
            risk_level="blocked",
            findings=("Proposed source content cannot be empty.",),
        )

    if len(proposed_content) > 250_000:
        return StaticSecurityReport(
            syntax_ok=False,
            security_ok=False,
            risk_level="blocked",
            findings=("Proposed module size exceeds safe 250 KB limit.",),
        )

    for prefix in PROTECTED_RELATIVE_PREFIXES:
        if relative_path.startswith(prefix):
            findings.append(f"Protected path blocked: {relative_path}")
            security_ok = False

    if _SUSPICIOUS_SECURITY_RE.search(proposed_content):
        findings.append("Suspicious security bypass language detected.")
        security_ok = False

    try:
        tree = ast.parse(proposed_content, filename=relative_path)
    except SyntaxError as exc:
        return StaticSecurityReport(
            syntax_ok=False,
            security_ok=False,
            risk_level="blocked",
            findings=(f"SyntaxError at line {exc.lineno}: {exc.msg}",),
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                top = mod.split(".")[0]
                if mod in _FORBIDDEN_MODULE_IMPORTS or top in _FORBIDDEN_MODULE_IMPORTS:
                    findings.append(f"Disallowed module import in offline sandbox policy: '{mod}'")
                    security_ok = False
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0]
            if mod in _FORBIDDEN_MODULE_IMPORTS or top in _FORBIDDEN_MODULE_IMPORTS:
                findings.append(f"Disallowed import-from module: '{mod}'")
                security_ok = False
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALL_NAMES:
                findings.append(f"Forbidden dynamic execution call: '{node.func.id}()'")
                security_ok = False
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                pair = (node.func.value.id, node.func.attr)
                if pair in _DANGEROUS_ATTR_CALLS:
                    findings.append(f"Dangerous call blocked: '{pair[0]}.{pair[1]}()'")
                    security_ok = False
                if pair[0] == "subprocess":
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            findings.append("subprocess call with shell=True is forbidden.")
                            security_ok = False

    if not security_ok:
        risk = "blocked"
    elif relative_path.startswith("core/agent/") or relative_path.startswith("core/ai/"):
        risk = "medium"
    else:
        risk = "low"

    return StaticSecurityReport(
        syntax_ok=syntax_ok,
        security_ok=security_ok,
        risk_level=risk,
        findings=tuple(findings),
    )
