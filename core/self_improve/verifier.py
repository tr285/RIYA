"""Isolated temporary sandbox verification for self-improvement patches."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile


@dataclass(frozen=True)
class SandboxVerificationResult:
    """Result of compiling and testing a staged patch in an isolated directory."""

    passed: bool
    compile_passed: bool
    tests_passed: bool
    exit_code: int
    stdout: str
    stderr: str
    test_files: tuple[str, ...]


def run_isolated_verification(
    project_root: Path,
    relative_target: str,
    proposed_content: str,
    *,
    test_files: tuple[str, ...] = (),
    timeout_seconds: float = 45.0,
) -> SandboxVerificationResult:
    """
    Stage the candidate file inside an isolated temporary workspace copy and execute
    py_compile + pytest without ever modifying live repository files.
    """
    root = Path(project_root).expanduser().resolve()
    staged_rel_tests: list[str] = []
    for raw_t in test_files:
        cleaned = str(raw_t or "").strip()
        if not cleaned or ".." in Path(cleaned).parts or cleaned.startswith("/"):
            raise ValueError(f"Invalid test file path for sandbox verification: {raw_t!r}")
        test_src = (root / cleaned).resolve()
        if not test_src.is_file():
            raise FileNotFoundError(f"Sandbox test file not found: {cleaned}")
        test_src.relative_to(root)
        staged_rel_tests.append(cleaned)

    with tempfile.TemporaryDirectory(prefix="riya_patch_sandbox_") as tmp_dir:
        sandbox_root = Path(tmp_dir).resolve()
        # Copy minimal Python source tree (core + tests) cleanly, excluding heavy C++ builds/backups/db.
        for folder in ("core", "tests"):
            src_folder = root / folder
            if src_folder.is_dir():
                shutil.copytree(
                    src_folder,
                    sandbox_root / folder,
                    ignore=shutil.ignore_patterns(
                        "llama.cpp",
                        "__pycache__",
                        "*.pyc",
                        "*.before_*",
                        "*.bak",
                        "*.dylib",
                    ),
                )

        target_file = (sandbox_root / relative_target).resolve()
        target_file.relative_to(sandbox_root)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(proposed_content, encoding="utf-8")

        try:
            py_compile.compile(str(target_file), doraise=True)
            compile_ok = True
        except py_compile.PyCompileError as exc:
            return SandboxVerificationResult(
                passed=False,
                compile_passed=False,
                tests_passed=False,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                test_files=tuple(staged_rel_tests),
            )

        if not staged_rel_tests:
            return SandboxVerificationResult(
                passed=True,
                compile_passed=compile_ok,
                tests_passed=True,
                exit_code=0,
                stdout="py_compile passed (no test files specified).",
                stderr="",
                test_files=(),
            )

        env = dict(os.environ)
        env["PYTHONPATH"] = str(sandbox_root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        cmd = [sys.executable, "-m", "pytest", "-q", *staged_rel_tests]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(sandbox_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxVerificationResult(
                passed=False,
                compile_passed=True,
                tests_passed=False,
                exit_code=-1,
                stdout=str(exc.stdout or ""),
                stderr=f"Sandbox pytest timed out after {timeout_seconds}s.",
                test_files=tuple(staged_rel_tests),
            )

        tests_ok = proc.returncode == 0
        return SandboxVerificationResult(
            passed=(compile_ok and tests_ok),
            compile_passed=compile_ok,
            tests_passed=tests_ok,
            exit_code=int(proc.returncode),
            stdout=proc.stdout[-4000:],
            stderr=proc.stderr[-4000:],
            test_files=tuple(staged_rel_tests),
        )
