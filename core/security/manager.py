from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any

from core.identity.models import AuthenticatedIdentity


_SECRET_PATTERN = re.compile(
    r"(?i)(?:sk-[a-z0-9]{16,}|bearer\s+[a-z0-9._\-]{12,}|api[_-]?key\s*[:=]\s*\S+|password\s*[:=]\s*\S+)"
)


def looks_like_secret(text: str) -> bool:
    """Detect whether a given text snippet contains credentials or API keys."""
    return bool(_SECRET_PATTERN.search(str(text or "")))


class RiskLevel(str, Enum):
    """Execution risk classification for RIYA tools and autonomous actions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SecurityDecision:
    """Result of a RIYA security check."""

    allowed: bool
    requires_confirmation: bool
    reason: str = ""
    risk_level: RiskLevel = field(default=RiskLevel.LOW, compare=False)


class SecurityManager:
    """RIYA security and permission layer."""

    SAFE_ACTIONS = frozenset({
        "current_time",
        "battery_status",
        "system_information",
        "storage_status",
        "network_status",
        "check_application",
        "platform_capabilities",

        "list_files",
        "find_files",
        "read_file",
        "inspect_file_metadata",
        "search_files_advanced",
        "extract_document_text",
        "summarize_document",
        "find_duplicate_files",
        "file_insights",

        "recall_memory",
        "list_memories",
        "search_memory",

        "calculate",

        "desktop_list_apps",
        "desktop_running_apps",
        "desktop_app_state",
        "active_window_info",
        "desktop_search_apps",
        "desktop_app_details",
        "desktop_running_apps_detailed",
        "desktop_app_resources",

        "clipboard_read",
        "send_notification",

        "browser_inspect_page",
        "browser_extract_links",

        # Productivity (Phase 6.6)
        "create_task",
        "get_task",
        "list_tasks",
        "update_task",
        "complete_task",
        "reopen_task",
        "create_reminder",
        "list_reminders",
        "snooze_reminder",
        "create_calendar_event",
        "list_calendar_events",
        "get_daily_schedule",
        "get_weekly_schedule",
        "create_note",
        "get_note",
        "list_notes",
        "search_notes",
        "update_note",
        "get_daily_plan",
        "get_end_of_day_summary",
    })

    CONFIRMATION_ACTIONS = frozenset({
        "create_folder",
        "write_file",
        "copy_file",
        "move_file",
        "rename_file",
        "organize_files",

        "remember",
        "forget_memory",

        "open_application",
        "close_application",
        "take_screenshot",
        "set_volume",
        "mute_volume",
        "unmute_volume",
        "clipboard_write",

        "delete_file",
        "delete_folder",
        "send_message",
        "send_email",
        "shutdown",
        "restart",
        "lock_screen",

        "python_sandbox",
        "terminal_command",
        "local_db_query",

        "browser_open_local",
        "browser_fetch_url",
        "browser_submit_form",

        "desktop_focus_app",
        "desktop_terminate_app",
        "desktop_open_url",

        # Productivity high-risk deletions (Phase 6.6)
        "delete_task",
        "cancel_reminder",
        "delete_calendar_event",
        "delete_note",
    })

    HIGH_RISK_ACTIONS = frozenset({
        "delete_file",
        "delete_folder",
        "send_message",
        "send_email",
        "shutdown",
        "restart",
        "lock_screen",
        "python_sandbox",
        "terminal_command",
        "browser_fetch_url",
        "browser_submit_form",

        # Productivity high-risk deletions (Phase 6.6)
        "delete_task",
        "cancel_reminder",
        "delete_calendar_event",
        "delete_note",
    })

    def get_risk_level(self, tool_name: str) -> RiskLevel:
        """Return explicit RiskLevel (LOW, MEDIUM, HIGH) for a tool name."""
        if not isinstance(tool_name, str):
            return RiskLevel.HIGH
        if tool_name in self.SAFE_ACTIONS:
            return RiskLevel.LOW
        if tool_name in self.HIGH_RISK_ACTIONS:
            return RiskLevel.HIGH
        if tool_name in self.CONFIRMATION_ACTIONS:
            return RiskLevel.MEDIUM
        return RiskLevel.HIGH

    def check(self, tool_name: str) -> SecurityDecision:
        """Check whether a tool is allowed to execute."""

        if not isinstance(tool_name, str):
            return SecurityDecision(
                allowed=False,
                requires_confirmation=False,
                reason="Unknown or unauthorized tool.",
                risk_level=RiskLevel.HIGH,
            )

        if tool_name in self.SAFE_ACTIONS:
            return SecurityDecision(
                allowed=True,
                requires_confirmation=False,
                reason="Safe action.",
                risk_level=RiskLevel.LOW,
            )

        if tool_name in self.CONFIRMATION_ACTIONS:
            return SecurityDecision(
                allowed=True,
                requires_confirmation=True,
                reason="This action requires user confirmation.",
                risk_level=self.get_risk_level(tool_name),
            )

        return SecurityDecision(
            allowed=False,
            requires_confirmation=False,
            reason="Unknown or unauthorized tool.",
            risk_level=RiskLevel.HIGH,
        )

    def check_for_user(
        self,
        tool_name: str,
        *,
        identity: Any = None,
        permission_store: Any = None,
        permission_manager: Any = None,
        parameters: dict[str, Any] | None = None,
        workspace_dir: str | None = None,
        user_required: bool = False,
        consume_permission: bool = False,
        actor: str = "system",
    ) -> SecurityDecision:
        """Evaluate tool permission combining baseline policy, identity, and per-user grants."""

        base = self.check(tool_name)
        if not base.allowed:
            return base

        # Preview mode for organize_files is a safe, read-only plan preview
        if tool_name == "organize_files" and parameters is not None and not parameters.get("execute", False):
            base = SecurityDecision(
                allowed=True,
                requires_confirmation=False,
                reason="Previewing file organization plan does not modify files.",
                risk_level=RiskLevel.LOW,
            )

        if identity is not None and not isinstance(identity, AuthenticatedIdentity):
            return SecurityDecision(
                allowed=False,
                requires_confirmation=False,
                reason="Invalid user identity; expected AuthenticatedIdentity.",
            )

        if user_required and not isinstance(identity, AuthenticatedIdentity):
            return SecurityDecision(
                allowed=False,
                requires_confirmation=False,
                reason="Authenticated user identity is required to execute this tool.",
            )

        legacy_policy: str | None = None
        if isinstance(identity, AuthenticatedIdentity) and permission_store is not None:
            legacy_policy = permission_store.get_tool_policy(identity, tool_name)
            if legacy_policy == "deny":
                return SecurityDecision(
                    allowed=False,
                    requires_confirmation=False,
                    reason=f"Tool '{tool_name}' is denied for the active user.",
                )

        if isinstance(identity, AuthenticatedIdentity) and permission_manager is not None:
            if consume_permission:
                eval_res = permission_manager.consume_tool_if_authorized(
                    identity,
                    tool_name,
                    parameters,
                    workspace_dir=workspace_dir,
                    actor=actor,
                )
            else:
                eval_res = permission_manager.evaluate_tool(
                    identity,
                    tool_name,
                    parameters,
                    workspace_dir=workspace_dir,
                    actor=actor,
                )
            if eval_res.blocked:
                return SecurityDecision(
                    allowed=False,
                    requires_confirmation=False,
                    reason=eval_res.reason,
                )
            requires_confirmation = (
                base.requires_confirmation
                or eval_res.requires_confirmation
                or legacy_policy == "confirm"
            )
            return SecurityDecision(
                allowed=True,
                requires_confirmation=requires_confirmation,
                reason=eval_res.reason if eval_res.requires_confirmation else base.reason,
                risk_level=base.risk_level,
            )

        if legacy_policy == "confirm":
            return SecurityDecision(
                allowed=True,
                requires_confirmation=True,
                reason="User policy requires confirmation for this action.",
                risk_level=base.risk_level,
            )
        if legacy_policy == "allow":
            # Destructive / privileged actions still require confirmation unless explicitly safe.
            return SecurityDecision(
                allowed=True,
                requires_confirmation=base.requires_confirmation,
                reason=base.reason,
                risk_level=base.risk_level,
            )

        return base
