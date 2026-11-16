"""Initial 8 modular offline-capable RIYA skills built over ToolExecutor."""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
import statistics
from typing import Any

from core.security.manager import RiskLevel
from core.skills.base import SkillContext, SkillDefinition


def _handle_research_assistant(ctx: SkillContext, inputs: dict[str, Any]) -> dict[str, Any]:
    topic = str(inputs["topic"]).strip()
    search_path = str(inputs.get("path") or ".").strip()

    findings: list[str] = []
    mem_res = ctx.run_tool("search_memory", query=topic, limit=5)
    mem_text = str(mem_res.get("result") or "")
    if mem_res.get("status") == "success" and mem_text and not mem_text.startswith("No stored memories"):
        findings.append(f"[Memory Matches]\n{mem_text}")

    files_res = ctx.run_tool("find_files", name=topic.split()[0], path=search_path)
    if files_res.get("status") == "success":
        files_text = str(files_res.get("result") or "")
        if files_text and "no " not in files_text.lower():
            findings.append(f"[Matching Workspace Files]\n{files_text}")

    list_res = ctx.run_tool("list_files", path=search_path)
    dir_summary = str(list_res.get("result") or "") if list_res.get("status") == "success" else ""

    summary = (
        "\n\n".join(findings)
        if findings
        else f"Scanned '{search_path}' for '{topic}'. Workspace entries:\n{dir_summary or '(empty)'}"
    )
    return {
        "topic": topic,
        "findings": findings,
        "summary": summary,
    }


def _handle_coding_assistant(ctx: SkillContext, inputs: dict[str, Any]) -> dict[str, Any]:
    target_path = str(inputs.get("path") or ".").strip()
    check_file = str(inputs.get("file") or "").strip()

    listing = ctx.run_tool("list_files", path=target_path)
    files_text = str(listing.get("result") or "")
    file_preview = ""
    if check_file:
        read_res = ctx.run_tool("read_file", path=check_file)
        if read_res.get("status") != "success":
            return {
                "path": target_path,
                "inspected_file": check_file,
                "status": str(read_res.get("status") or "failed"),
                "preview": "",
                "summary": str(read_res.get("error") or read_res.get("reason") or "Failed to read file."),
            }
        file_preview = str(read_res.get("result") or "")[:1200]

    return {
        "path": target_path,
        "inspected_file": check_file,
        "status": "inspected",
        "preview": file_preview or files_text[:1200],
        "summary": (
            f"Inspected '{check_file}' ({len(file_preview)} chars)."
            if check_file
            else f"Listed workspace '{target_path}' for coding analysis."
        ),
    }


def _handle_file_management(ctx: SkillContext, inputs: dict[str, Any]) -> dict[str, Any]:
    target_path = str(inputs.get("path") or ".").strip()
    res = ctx.run_tool("list_files", path=target_path)
    if res.get("status") != "success":
        raise RuntimeError(str(res.get("error") or res.get("reason") or "Could not list files."))

    raw_text = str(res.get("result") or "")
    entries = [line.strip() for line in raw_text.splitlines() if line.strip()]
    by_extension: dict[str, int] = {}
    for entry in entries:
        ext = Path(entry.rstrip("/")).suffix.lower() or ("folder" if entry.endswith("/") else "no_ext")
        by_extension[ext] = by_extension.get(ext, 0) + 1

    return {
        "path": target_path,
        "entry_count": len(entries),
        "by_extension": by_extension,
        "summary": f"Found {len(entries)} entries in '{target_path}' across {len(by_extension)} file group(s).",
    }


def _handle_data_analysis(ctx: SkillContext, inputs: dict[str, Any]) -> dict[str, Any]:
    data_path = str(inputs["path"]).strip()
    read_res = ctx.run_tool("read_file", path=data_path)
    if read_res.get("status") != "success":
        raise RuntimeError(str(read_res.get("error") or read_res.get("reason") or "Failed to read data file."))

    raw = str(read_res.get("result") or "").strip()
    suffix = Path(data_path).suffix.lower()
    stats: dict[str, dict[str, float | int]] = {}
    row_count = 0

    if suffix == ".json":
        parsed = json.loads(raw)
        rows = parsed if isinstance(parsed, list) else [parsed]
        row_count = len(rows)
        numeric_cols: dict[str, list[float]] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            for k, v in item.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                    numeric_cols.setdefault(str(k), []).append(float(v))
        for col, vals in numeric_cols.items():
            stats[col] = {
                "count": len(vals),
                "min": min(vals),
                "max": max(vals),
                "mean": round(statistics.mean(vals), 4),
            }
    else:
        reader = csv.DictReader(io.StringIO(raw))
        numeric_cols = {}
        for row in reader:
            row_count += 1
            for col, raw_val in row.items():
                if col is None or raw_val is None:
                    continue
                try:
                    num = float(str(raw_val).strip())
                    if math.isfinite(num):
                        numeric_cols.setdefault(col.strip(), []).append(num)
                except ValueError:
                    continue
        for col, vals in numeric_cols.items():
            stats[col] = {
                "count": len(vals),
                "min": min(vals),
                "max": max(vals),
                "mean": round(statistics.mean(vals), 4),
            }

    summary = (
        f"Analyzed '{data_path}': {row_count} record(s), {len(stats)} numeric column(s)."
    )
    return {
        "path": data_path,
        "row_count": row_count,
        "numeric_stats": stats,
        "summary": summary,
    }


def _handle_study_assistant(ctx: SkillContext, inputs: dict[str, Any]) -> dict[str, Any]:
    topic = str(inputs["topic"]).strip()
    notes_path = str(inputs.get("notes_path") or "").strip()

    source_lines: list[str] = []
    mem_res = ctx.run_tool("search_memory", query=topic, limit=5)
    mem_text = str(mem_res.get("result") or "")
    if mem_res.get("status") == "success" and mem_text and not mem_text.startswith("No stored memories"):
        source_lines.extend(mem_text.splitlines())

    if notes_path:
        read_res = ctx.run_tool("read_file", path=notes_path)
        if read_res.get("status") == "success":
            source_lines.extend(
                line.strip() for line in str(read_res.get("result") or "").splitlines() if line.strip()
            )

    flashcards: list[dict[str, str]] = []
    for idx, line in enumerate(source_lines[:5], start=1):
        clean = line.lstrip("-* ").strip()
        flashcards.append(
            {
                "question": f"Q{idx} ({topic}): Explain '{clean[:80]}'",
                "answer": clean[:240],
            }
        )
    if not flashcards:
        flashcards.append(
            {
                "question": f"Core question: What are the key principles and examples of {topic}?",
                "answer": f"Review foundational definitions and practice exercises for {topic}.",
            }
        )

    return {
        "topic": topic,
        "flashcards": flashcards,
        "summary": f"Prepared {len(flashcards)} study card(s) for '{topic}'.",
    }


def _handle_project_management(ctx: SkillContext, inputs: dict[str, Any]) -> dict[str, Any]:
    workspace = str(inputs.get("path") or ".").strip()
    files_res = ctx.run_tool("list_files", path=workspace)
    files_text = str(files_res.get("result") or "")

    tasks_list: list[dict[str, Any]] = []
    if ctx.task_store is not None and ctx.identity is not None:
        tasks_list = ctx.task_store.list_tasks(ctx.identity, limit=25)

    active = [t for t in tasks_list if t.get("status") in {"pending", "running", "paused", "awaiting_confirmation"}]
    completed = [t for t in tasks_list if t.get("status") == "completed"]

    return {
        "workspace": workspace,
        "active_tasks": len(active),
        "completed_tasks": len(completed),
        "workspace_preview": files_text[:800],
        "summary": (
            f"Project status in '{workspace}': {len(active)} active task(s), "
            f"{len(completed)} completed task(s)."
        ),
    }


def _handle_system_diagnostics(ctx: SkillContext, inputs: dict[str, Any]) -> dict[str, Any]:
    sys_res = ctx.run_tool("system_information")
    bat_res = ctx.run_tool("battery_status")
    time_res = ctx.run_tool("current_time")

    sys_info = str(sys_res.get("result") or "")
    bat_info = str(bat_res.get("result") or "")
    time_info = str(time_res.get("result") or "")

    ai_status = {}
    if ctx.assistant is not None and hasattr(ctx.assistant, "ai_status"):
        ai_status = ctx.assistant.ai_status()

    summary = (
        f"Time: {time_info} | Battery: {bat_info} | "
        f"System: {sys_info.splitlines()[0] if sys_info else 'unavailable'}"
    )
    return {
        "system_information": sys_info,
        "battery_status": bat_info,
        "current_time": time_info,
        "ai_status": ai_status,
        "summary": summary,
    }


def _handle_personal_productivity(ctx: SkillContext, inputs: dict[str, Any]) -> dict[str, Any]:
    focus = str(inputs.get("focus") or "").strip()
    mem_res = (
        ctx.run_tool("search_memory", query=focus, limit=5)
        if focus
        else ctx.run_tool("list_memories")
    )
    mem_text = str(mem_res.get("result") or "")

    pending_tasks: list[dict[str, Any]] = []
    if ctx.task_store is not None and ctx.identity is not None:
        pending_tasks = ctx.task_store.list_recoverable_tasks(ctx.identity)

    checklist: list[str] = [f"Resume pending task: {t['goal']}" for t in pending_tasks[:5]]
    if not checklist and focus:
        checklist.append(f"Deep work block on: {focus}")
    elif not checklist:
        checklist.append("Review today's top goal and clear open tasks.")

    return {
        "focus": focus or "general",
        "checklist": checklist,
        "memory_notes": mem_text[:800],
        "summary": f"Productivity plan ({len(checklist)} action item(s)): " + "; ".join(checklist),
    }


def get_builtin_skills() -> list[SkillDefinition]:
    """Return the 8 modular built-in RIYA Phase 5 skills."""
    return [
        SkillDefinition(
            skill_id="research_assistant",
            name="Research Assistant",
            version="1.0.0",
            description="Search user memories and local workspace files to synthesize structured research notes.",
            required_permissions=("memory.read", "filesystem.read"),
            input_schema={
                "topic": {"type": str, "required": True, "allow_empty": False},
                "path": {"type": str, "required": False, "default": "."},
            },
            output_schema={"topic": "str", "findings": "list", "summary": "str"},
            handler=_handle_research_assistant,
            risk_level=RiskLevel.LOW,
            documentation="Searches memories and workspace files for a topic offline.",
        ),
        SkillDefinition(
            skill_id="coding_assistant",
            name="Coding Assistant",
            version="1.0.0",
            description="Inspect project files and source modules cleanly to prepare engineering changes.",
            required_permissions=("filesystem.read",),
            input_schema={
                "path": {"type": str, "required": False, "default": "."},
                "file": {"type": str, "required": False, "default": ""},
            },
            output_schema={
                "path": "str",
                "inspected_file": "str",
                "status": "str",
                "preview": "str",
                "summary": "str",
            },
            handler=_handle_coding_assistant,
            risk_level=RiskLevel.LOW,
            documentation="Reads project layout and target source files safely.",
        ),
        SkillDefinition(
            skill_id="file_management",
            name="File Management",
            version="1.0.0",
            description="Analyze directory contents and group files by type safely within allowed roots.",
            required_permissions=("filesystem.read",),
            input_schema={
                "path": {"type": str, "required": False, "default": "."},
            },
            output_schema={
                "path": "str",
                "entry_count": "int",
                "by_extension": "dict",
                "summary": "str",
            },
            handler=_handle_file_management,
            risk_level=RiskLevel.LOW,
            documentation="Audits directory entries and extension counts offline.",
        ),
        SkillDefinition(
            skill_id="data_analysis",
            name="Data Analysis",
            version="1.0.0",
            description="Compute summary statistics (count, min, max, mean) on local CSV or JSON datasets.",
            required_permissions=("filesystem.read",),
            input_schema={
                "path": {"type": str, "required": True, "allow_empty": False},
            },
            output_schema={
                "path": "str",
                "row_count": "int",
                "numeric_stats": "dict",
                "summary": "str",
            },
            handler=_handle_data_analysis,
            risk_level=RiskLevel.LOW,
            documentation="Reads local CSV/JSON data and calculates column summary statistics.",
        ),
        SkillDefinition(
            skill_id="study_assistant",
            name="Study Assistant",
            version="1.0.0",
            description="Generate structured flashcards and review questions from stored memories and notes.",
            required_permissions=("memory.read",),
            input_schema={
                "topic": {"type": str, "required": True, "allow_empty": False},
                "notes_path": {"type": str, "required": False, "default": ""},
            },
            output_schema={
                "topic": "str",
                "flashcards": "list",
                "summary": "str",
            },
            handler=_handle_study_assistant,
            risk_level=RiskLevel.LOW,
            documentation="Builds review flashcards for a study topic from user notes/memories.",
        ),
        SkillDefinition(
            skill_id="project_management",
            name="Project Management",
            version="1.0.0",
            description="Track project workspace files alongside active and completed user tasks.",
            required_permissions=("filesystem.read",),
            input_schema={
                "path": {"type": str, "required": False, "default": "."},
            },
            output_schema={
                "workspace": "str",
                "active_tasks": "int",
                "completed_tasks": "int",
                "workspace_preview": "str",
                "summary": "str",
            },
            handler=_handle_project_management,
            risk_level=RiskLevel.LOW,
            documentation="Summarizes workspace structure and task backlog counts.",
        ),
        SkillDefinition(
            skill_id="system_diagnostics",
            name="System Diagnostics",
            version="1.0.0",
            description="Inspect local macOS hardware info, battery, clock, and AI engine readiness.",
            required_permissions=("system.info",),
            input_schema={},
            output_schema={
                "system_information": "str",
                "battery_status": "str",
                "current_time": "str",
                "ai_status": "dict",
                "summary": "str",
            },
            handler=_handle_system_diagnostics,
            risk_level=RiskLevel.LOW,
            documentation="Collects local system and AI runtime diagnostics offline.",
        ),
        SkillDefinition(
            skill_id="personal_productivity",
            name="Personal Productivity",
            version="1.0.0",
            description="Build an actionable daily checklist from saved user memories and pending tasks.",
            required_permissions=("memory.read",),
            input_schema={
                "focus": {"type": str, "required": False, "default": ""},
            },
            output_schema={
                "focus": "str",
                "checklist": "list",
                "memory_notes": "str",
                "summary": "str",
            },
            handler=_handle_personal_productivity,
            risk_level=RiskLevel.LOW,
            documentation="Synthesizes user goals/memories and open tasks into a focus checklist.",
        ),
    ]
