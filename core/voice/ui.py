"""Conversational Text + Voice User Interface (framework-independent VM + Tkinter view)."""

from __future__ import annotations

import math
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from core.voice.audio_input import list_input_devices, list_output_devices
from core.voice.config import (
    RESPONSE_STYLES,
    SUPPORTED_LANGUAGES,
    VERIFIED_LANGUAGES,
    VoiceConfig,
)
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import TranscriptEntry
from core.voice.state import ACTIVE_STATES, VoiceState
from core.voice.tts import LocalTTS


@dataclass(frozen=True)
class VoiceVisualSnapshot:
    """Thread-safe snapshot consumed by GUI/CLI views."""

    mode: str
    voice_state: VoiceState
    status_label: str
    microphone_active: bool
    assistant_speaking: bool
    audio_level: float
    waveform_bars: tuple[float, ...]
    transcript: tuple[TranscriptEntry, ...]
    error_message: str | None
    can_interrupt: bool
    config: VoiceConfig
    listening_indicator: str = field(default="mic_off", compare=False)
    active_task_progress: dict[str, Any] | None = field(default=None, compare=False)
    pending_confirmation: dict[str, Any] | None = field(default=None, compare=False)
    streaming_text: str = field(default="", compare=False)
    connectivity_status: str = field(default="Offline-First (Local Only)", compare=False)
    model_status_label: str = field(default="AI: Not Loaded", compare=False)
    operation_state: str = field(default="idle", compare=False)
    recovery_message: str | None = field(default=None, compare=False)
    recent_tool_results: tuple[dict[str, Any], ...] = field(default=(), compare=False)
    conversation_mode: str = field(default="continuous", compare=False)
    is_paused: bool = field(default=False, compare=False)
    is_recording: bool = field(default=False, compare=False)
    pending_tasks_count: int = field(default=0, compare=False)
    active_reminders_count: int = field(default=0, compare=False)
    daily_plan_summary: str | None = field(default=None, compare=False)


class VoiceUIController:
    """Connects UI actions cleanly to ``VoiceOrchestrator`` without state leakage."""

    BAR_COUNT = 24

    def __init__(self, orchestrator: VoiceOrchestrator):
        self.orchestrator = orchestrator
        self._phase = 0.0
        self.hydrate_user_history()

    @staticmethod
    def status_label_for(state: VoiceState, *, mode: str, is_paused: bool = False) -> str:
        if is_paused:
            return "Voice Paused"
        mapping = {
            VoiceState.IDLE: "Ready (Text mode)" if mode == "text" else "Voice Ready",
            VoiceState.LISTENING: "Listening...",
            VoiceState.TRANSCRIBING: "Transcribing speech...",
            VoiceState.THINKING: "Thinking...",
            VoiceState.SPEAKING: "Speaking...",
            VoiceState.INTERRUPTED: "Interrupted",
            VoiceState.ERROR: "Voice Error",
            VoiceState.STOPPING: "Stopping voice...",
        }
        return mapping.get(state, state.value)

    @staticmethod
    def language_options() -> list[tuple[str, str]]:
        """Return (code, honest_display_label) pairs for settings UI."""

        options: list[tuple[str, str]] = []
        for code, name in SUPPORTED_LANGUAGES.items():
            if code in VERIFIED_LANGUAGES:
                options.append((code, f"{name} ({code})"))
            else:
                options.append((code, f"{name} ({code} - untested)"))
        return options

    def advance_waveform(self, state: VoiceState, level: float) -> tuple[float, ...]:
        """Compute lightweight 24-bar animation envelope without busy polling."""

        self._phase = (self._phase + 0.35) % (2.0 * math.pi)
        bars: list[float] = []
        center = (self.BAR_COUNT - 1) / 2.0

        for idx in range(self.BAR_COUNT):
            dist_scale = 1.0 - 0.55 * (abs(idx - center) / max(1.0, center))
            if state == VoiceState.LISTENING:
                amp = 0.14 + 0.86 * min(1.0, max(0.0, level))
                wave = 0.5 + 0.5 * math.sin(self._phase + idx * 0.45)
                val = amp * dist_scale * (0.45 + 0.55 * wave)
            elif state == VoiceState.SPEAKING:
                amp = 0.22 + 0.78 * min(1.0, max(0.15, level))
                wave = 0.5 + 0.5 * math.sin(self._phase * 1.4 + idx * 0.55)
                val = amp * dist_scale * (0.40 + 0.60 * wave)
            elif state in (VoiceState.TRANSCRIBING, VoiceState.THINKING):
                pulse = 0.5 + 0.5 * math.sin(self._phase * 0.9 - idx * 0.35)
                val = 0.15 + 0.35 * pulse * dist_scale
            elif state == VoiceState.IDLE:
                val = 0.08 * dist_scale
            else:
                val = 0.05
            bars.append(round(max(0.04, min(1.0, val)), 3))
        return tuple(bars)

    def snapshot(self) -> VoiceVisualSnapshot:
        state = self.orchestrator.state
        mode = self.orchestrator.mode
        mic_active = self.orchestrator.session.microphone_active
        speaking = state == VoiceState.SPEAKING or self.orchestrator.player.is_playing
        if speaking:
            level = self.orchestrator.player.current_level
        elif mic_active:
            level = self.orchestrator.session.microphone_level
        else:
            level = 0.0

        if mic_active and state == VoiceState.LISTENING:
            indicator = "listening"
        elif state == VoiceState.TRANSCRIBING:
            indicator = "transcribing"
        elif state == VoiceState.THINKING:
            indicator = "thinking"
        elif speaking:
            indicator = "speaking"
        elif state == VoiceState.INTERRUPTED:
            indicator = "interrupted"
        elif state == VoiceState.ERROR:
            indicator = "error"
        else:
            indicator = "mic_off"

        task_prog = getattr(self.orchestrator, "active_task_progress", None)
        pending_conf = self.pending_confirmation_info()
        streaming = str(getattr(self.orchestrator, "streaming_text", "") or "")
        conn_info = self.connectivity_status()
        _, ai_label = self.ai_status_summary()

        assistant = getattr(self.orchestrator, "assistant", None)
        err_info = (
            assistant.last_error_info()
            if assistant is not None and hasattr(assistant, "last_error_info")
            else None
        )
        eff_error = self.orchestrator.last_error or (
            err_info.get("message") if isinstance(err_info, dict) else None
        )
        recovery_msg = (
            err_info.get("recovery_suggestion")
            if isinstance(err_info, dict)
            else (
                "Check microphone/speaker permissions or switch back to text mode."
                if self.orchestrator.last_error
                else None
            )
        )

        if pending_conf is not None:
            op_state = "awaiting_confirmation"
        elif state in (VoiceState.THINKING, VoiceState.TRANSCRIBING, VoiceState.SPEAKING):
            op_state = "running"
        elif isinstance(task_prog, dict) and task_prog.get("status"):
            op_state = str(task_prog.get("status"))
        elif eff_error:
            op_state = "failed"
        else:
            op_state = "idle"

        tool_results = tuple(self.last_tool_results(limit=15))
        is_paused = getattr(self.orchestrator, "is_paused", False)
        conv_mode = getattr(self.orchestrator, "conversation_mode", "continuous")
        is_rec = getattr(self.orchestrator.session, "is_recording", False)
        prod_summary = self.get_productivity_summary()

        return VoiceVisualSnapshot(
            mode=mode,
            voice_state=state,
            status_label=self.status_label_for(state, mode=mode, is_paused=is_paused),
            microphone_active=mic_active,
            assistant_speaking=speaking,
            audio_level=level,
            waveform_bars=self.advance_waveform(state, level),
            transcript=tuple(self.orchestrator.session.transcript_entries()),
            error_message=eff_error,
            can_interrupt=state in (VoiceState.SPEAKING, VoiceState.THINKING),
            config=self.orchestrator.config,
            listening_indicator=indicator,
            active_task_progress=task_prog,
            pending_confirmation=pending_conf,
            streaming_text=streaming,
            connectivity_status=str(conn_info.get("label", "Offline-First (Local Only)")),
            model_status_label=ai_label,
            operation_state=op_state,
            recovery_message=recovery_msg,
            recent_tool_results=tool_results,
            conversation_mode=conv_mode,
            is_paused=is_paused,
            is_recording=is_rec,
            pending_tasks_count=prod_summary.get("tasks_pending", 0),
            active_reminders_count=prod_summary.get("reminders_pending", 0),
            daily_plan_summary=self.render_productivity_dashboard(),
        )

    def get_productivity_summary(self) -> dict[str, Any]:
        """Return structured productivity counts and status for UI widgets."""
        assistant = getattr(self.orchestrator, "assistant", None)
        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        identity = resolve_id() if callable(resolve_id) else None
        if assistant is None or not hasattr(assistant, "productivity") or identity is None:
            return {
                "tasks_pending": 0,
                "reminders_pending": 0,
                "events_today": 0,
                "overdue_tasks": 0,
            }
        try:
            tasks = assistant.productivity.list_tasks(identity, status="pending")
            overdue = [t for t in tasks if getattr(t, "is_overdue", False)]
            reminders = assistant.productivity.list_reminders(identity, status="pending")
            schedule = assistant.productivity.get_daily_schedule(identity)
            return {
                "tasks_pending": len(tasks),
                "reminders_pending": len(reminders),
                "events_today": len(schedule),
                "overdue_tasks": len(overdue),
            }
        except Exception:
            return {
                "tasks_pending": 0,
                "reminders_pending": 0,
                "events_today": 0,
                "overdue_tasks": 0,
            }

    def render_productivity_dashboard(self) -> str:
        """Render a readable text dashboard of user productivity for CLI/UI views."""
        summary = self.get_productivity_summary()
        return (
            f"Tasks: {summary['tasks_pending']} pending ({summary['overdue_tasks']} overdue) | "
            f"Reminders: {summary['reminders_pending']} | "
            f"Today's Events: {summary['events_today']}"
        )

    def hydrate_user_history(self, limit: int = 50) -> list[TranscriptEntry]:
        if hasattr(self.orchestrator, "hydrate_user_history"):
            return self.orchestrator.hydrate_user_history(limit=limit)
        return self.orchestrator.session.transcript_entries()

    def conversation_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return user-scoped conversation history entries (session + persisted SQLite)."""
        entries = self.orchestrator.session.transcript_entries()
        if entries:
            return [
                {
                    "entry_id": e.entry_id,
                    "role": e.role,
                    "content": e.text,
                    "modality": e.modality,
                    "status": e.status,
                    "timestamp": e.timestamp,
                }
                for e in entries[-max(1, int(limit)):]
            ]
        assistant = getattr(self.orchestrator, "assistant", None)
        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        identity = resolve_id() if callable(resolve_id) else None
        history_store = getattr(assistant, "history_store", None) if assistant is not None else None
        if history_store is not None and identity is not None:
            try:
                return history_store.get_history(identity, limit=limit)
            except Exception:
                return []
        return []

    def clear_conversation_history(self, *, clear_persisted: bool = True) -> int:
        """Clear in-memory transcript, assistant context, and optionally persisted user history."""
        self.orchestrator.session.clear_transcript()
        assistant = getattr(self.orchestrator, "assistant", None)
        if assistant is not None and hasattr(assistant, "context") and assistant.context is not None:
            try:
                assistant.context.clear()
            except Exception:
                pass
        deleted = 0
        if clear_persisted and assistant is not None:
            history_store = getattr(assistant, "history_store", None)
            resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
            identity = resolve_id() if callable(resolve_id) else None
            if history_store is not None and identity is not None:
                try:
                    deleted = int(history_store.clear_history(identity))
                except Exception:
                    deleted = 0
        self.orchestrator._notify_observers()
        return deleted

    def pending_confirmation_info(self) -> dict[str, Any] | None:
        """Inspect any active pending confirmation request for the current user."""
        assistant = getattr(self.orchestrator, "assistant", None)
        if assistant is None:
            return None
        pending = getattr(assistant, "pending_action", None)
        if not isinstance(pending, dict):
            return None
        tool_name = str(pending.get("tool") or "").strip()
        token = str(pending.get("confirmation_token") or pending.get("token") or "").strip()
        if not tool_name or not token:
            return None

        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        identity = resolve_id() if callable(resolve_id) else None
        stamped_user = pending.get("user_id")
        if (
            stamped_user is not None
            and identity is not None
            and getattr(identity, "user_id", None) != stamped_user
        ):
            return None

        params = pending.get("parameters")
        safe_params = dict(params) if isinstance(params, dict) else {}
        if hasattr(assistant, "_format_action_preview"):
            preview = assistant._format_action_preview(tool_name, safe_params)
        else:
            preview = f"{tool_name}({safe_params})"

        perm_key = None
        risk_tier = "sensitive-confirm"
        try:
            from core.security.permissions import PERMISSION_SPECS, resolve_permissions_for_tool

            keys = resolve_permissions_for_tool(tool_name, safe_params)
            if keys:
                perm_key = keys[0]
                spec = PERMISSION_SPECS.get(perm_key)
                if spec is not None:
                    risk_tier = (
                        "protected-default-deny"
                        if spec.protected_by_default
                        else ("sensitive-confirm" if spec.sensitive else "standard")
                    )
        except Exception:
            pass

        reason = str(pending.get("reason") or "Explicit user confirmation is required before executing this action.")
        return {
            "tool": tool_name,
            "parameters": safe_params,
            "preview": preview,
            "confirmation_token": token,
            "task_id": pending.get("task_id"),
            "permission_key": perm_key,
            "risk_tier": risk_tier,
            "reason": reason,
            "prompt": f"{reason} Action: {preview}. Approve?",
        }

    def approve_pending_action(self) -> str:
        """Approve and execute the currently pending action for the authenticated user."""
        if self.pending_confirmation_info() is None:
            return "There is no pending action awaiting approval."
        return self.orchestrator.send_text("yes")

    def reject_pending_action(self) -> str:
        """Reject and cancel the currently pending action for the authenticated user."""
        if self.pending_confirmation_info() is None:
            return "There is no pending action awaiting approval."
        return self.orchestrator.send_text("no")

    def run_task(self, request: str) -> str:
        return self.orchestrator.run_voice_task(request)

    def submit_task(self, request: str) -> dict[str, Any]:
        """Submit a multi-step autonomous task and return its structured execution state."""
        summary = self.orchestrator.run_voice_task(request)
        task_res = getattr(self.orchestrator, "last_task_result", None)
        prog = getattr(self.orchestrator, "active_task_progress", None) or {}
        if task_res is not None:
            status_val = getattr(
                getattr(task_res, "status", None),
                "value",
                str(getattr(task_res, "status", prog.get("status", "completed"))),
            )
            return {
                "task_id": getattr(task_res, "task_id", prog.get("task_id", "")),
                "goal": getattr(task_res, "goal", request),
                "status": status_val,
                "summary": summary,
                "steps_completed": int(getattr(task_res, "steps_completed", 0)),
                "steps_total": int(getattr(task_res, "steps_total", 0)),
                "step_results": list(getattr(task_res, "step_results", ()) or ()),
                "requires_confirmation": bool(getattr(task_res, "requires_confirmation", False)),
                "verified": bool(getattr(task_res, "verified", False)),
                "progress_events": self.task_progress_history(),
            }
        return {
            "task_id": prog.get("task_id", ""),
            "goal": request,
            "status": prog.get("status", "completed"),
            "summary": summary,
            "steps_completed": int(prog.get("steps_completed", 0)),
            "steps_total": int(prog.get("steps_total", 0)),
            "step_results": list(prog.get("step_results", ()) or ()),
            "requires_confirmation": bool(prog.get("requires_confirmation", False)),
            "verified": bool(prog.get("verified", True)),
            "progress_events": self.task_progress_history(),
        }

    def resume_task(
        self,
        task_id: str,
        *,
        confirmation_token: str | None = None,
    ) -> dict[str, Any]:
        """Resume a paused or awaiting-confirmation task from its SQLite checkpoint."""
        assistant = self.orchestrator.assistant
        if not hasattr(assistant, "resume_task"):
            raise RuntimeError("Assistant does not support task resumption.")
        task_res = assistant.resume_task(
            task_id,
            confirmation_token=confirmation_token,
            progress_callback=self.orchestrator._on_task_progress,
        )
        summary = (
            assistant._format_task_result(task_res)
            if hasattr(assistant, "_format_task_result")
            else str(getattr(task_res, "summary", "") or task_res)
        )
        status_val = getattr(
            getattr(task_res, "status", None),
            "value",
            str(getattr(task_res, "status", "completed")),
        )
        final_prog = {
            "task_id": getattr(task_res, "task_id", task_id),
            "goal": getattr(task_res, "goal", ""),
            "status": status_val,
            "steps_completed": int(getattr(task_res, "steps_completed", 0)),
            "steps_total": int(getattr(task_res, "steps_total", 0)),
            "requires_confirmation": bool(getattr(task_res, "requires_confirmation", False)),
            "verified": bool(getattr(task_res, "verified", False)),
            "message": summary,
            "step_results": list(getattr(task_res, "step_results", ()) or ()),
        }
        self.orchestrator._on_task_progress(final_prog)
        self.orchestrator.session.append_transcript(
            role="assistant",
            text=summary,
            modality=self.orchestrator.mode,
        )
        self.orchestrator._notify_observers()
        return {
            "task_id": getattr(task_res, "task_id", task_id),
            "goal": getattr(task_res, "goal", ""),
            "status": status_val,
            "summary": summary,
            "steps_completed": int(getattr(task_res, "steps_completed", 0)),
            "steps_total": int(getattr(task_res, "steps_total", 0)),
            "step_results": list(getattr(task_res, "step_results", ()) or ()),
            "requires_confirmation": bool(getattr(task_res, "requires_confirmation", False)),
            "verified": bool(getattr(task_res, "verified", False)),
        }

    def cancel_task(self, task_id: str | None = None) -> bool:
        """Cancel the specified task (or currently active task) cleanly."""
        assistant = self.orchestrator.assistant
        target_id = task_id
        if not target_id:
            prog = getattr(self.orchestrator, "active_task_progress", None)
            if isinstance(prog, dict):
                target_id = prog.get("task_id")
            if not target_id:
                pending = getattr(assistant, "pending_action", None)
                if isinstance(pending, dict):
                    target_id = pending.get("task_id")
        if not target_id or not hasattr(assistant, "cancel_task"):
            return False
        cancelled = bool(assistant.cancel_task(str(target_id)))
        if cancelled:
            pending = getattr(assistant, "pending_action", None)
            if isinstance(pending, dict) and pending.get("task_id") == target_id:
                assistant.pending_action = None
            prog = getattr(self.orchestrator, "active_task_progress", None) or {}
            self.orchestrator._on_task_progress(
                {
                    **prog,
                    "task_id": str(target_id),
                    "status": "cancelled",
                    "requires_confirmation": False,
                    "message": "Cancelled by user.",
                }
            )
        return cancelled

    def get_task_detail(self, task_id: str) -> dict[str, Any] | None:
        assistant = self.orchestrator.assistant
        store = getattr(assistant, "task_store", None)
        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        identity = resolve_id() if callable(resolve_id) else None
        if store is None or identity is None:
            return None
        detail = store.get_task(identity, task_id)
        if detail is None:
            return None
        plan = detail.get("plan")
        enriched = dict(detail)
        if plan is not None:
            status_obj = getattr(plan, "status", None)
            enriched.setdefault("task_id", getattr(plan, "task_id", task_id))
            enriched.setdefault("goal", getattr(plan, "goal", ""))
            enriched.setdefault(
                "status",
                getattr(status_obj, "value", str(status_obj or "")),
            )
            enriched.setdefault("steps", list(getattr(plan, "steps", ()) or ()))
        return enriched

    def list_tasks(
        self,
        *,
        status: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        assistant = self.orchestrator.assistant
        if hasattr(assistant, "list_tasks"):
            return assistant.list_tasks(status=status, limit=limit)
        return []

    def list_recoverable_tasks(self) -> list[dict[str, Any]]:
        assistant = self.orchestrator.assistant
        if hasattr(assistant, "list_recoverable_tasks"):
            return assistant.list_recoverable_tasks()
        return []

    def task_progress_history(self) -> list[dict[str, Any]]:
        if hasattr(self.orchestrator, "task_progress_history"):
            return list(self.orchestrator.task_progress_history)
        return []

    def list_skills(self) -> list[dict[str, Any]]:
        assistant = self.orchestrator.assistant
        if hasattr(assistant, "list_skills"):
            return [
                {
                    "skill_id": s.skill_id,
                    "name": s.name,
                    "version": s.version,
                    "description": s.description,
                }
                for s in assistant.list_skills()
            ]
        return []

    def run_skill(
        self,
        skill_id: str,
        *,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a registered reusable skill and surface its structured outcome."""
        assistant = self.orchestrator.assistant
        if not hasattr(assistant, "run_skill"):
            raise RuntimeError("Assistant does not support skill execution.")
        res = assistant.run_skill(skill_id, inputs=inputs)
        summary = str(getattr(res, "summary", "") or res)
        self.orchestrator.session.append_transcript(
            role="user",
            text=f"[Skill: {skill_id}]",
            modality=self.orchestrator.mode,
        )
        self.orchestrator.session.append_transcript(
            role="assistant",
            text=summary,
            modality=self.orchestrator.mode,
        )
        self.orchestrator._notify_observers()
        return {
            "skill_id": getattr(res, "skill_id", skill_id),
            "status": getattr(res, "status", "success"),
            "success": bool(getattr(res, "success", getattr(res, "status", "") == "success")),
            "summary": summary,
            "outputs": dict(getattr(res, "output", None) or getattr(res, "outputs", None) or {}),
            "verified": bool(getattr(res, "verified", getattr(res, "success", False))),
            "error": getattr(res, "error", None),
        }

    def _active_identity(self) -> Any | None:
        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        return resolve_id() if callable(resolve_id) else None

    def last_tool_results(self, limit: int = 25) -> list[dict[str, Any]]:
        assistant = getattr(self.orchestrator, "assistant", None)
        if assistant is not None and hasattr(assistant, "recent_tool_results"):
            try:
                return assistant.recent_tool_results(limit=limit, identity=self._active_identity())
            except TypeError:
                return assistant.recent_tool_results(limit=limit)
        return []

    def last_error_info(self) -> dict[str, Any] | None:
        assistant = getattr(self.orchestrator, "assistant", None)
        if assistant is not None and hasattr(assistant, "last_error_info"):
            try:
                return assistant.last_error_info(identity=self._active_identity())
            except TypeError:
                return assistant.last_error_info()
        return None

    def clear_last_error(self) -> None:
        assistant = getattr(self.orchestrator, "assistant", None)
        if assistant is not None and hasattr(assistant, "clear_last_error"):
            try:
                assistant.clear_last_error(identity=self._active_identity())
            except TypeError:
                assistant.clear_last_error()
            self.orchestrator._notify_observers()

    def platform_info(self) -> dict[str, Any]:
        """Return detected platform identity and support metadata."""
        assistant = getattr(self.orchestrator, "assistant", None)
        agent = getattr(assistant, "agent", None) if assistant is not None else None
        platform_obj = getattr(agent, "platform", None) if agent is not None else None
        if platform_obj is not None and hasattr(platform_obj, "platform_info"):
            return platform_obj.platform_info().to_dict()
        from core.device import get_platform_adapter

        return get_platform_adapter().platform_info().to_dict()

    def platform_capabilities(self) -> dict[str, Any]:
        """Return structured platform capability support and permission mappings."""
        assistant = getattr(self.orchestrator, "assistant", None)
        agent = getattr(assistant, "agent", None) if assistant is not None else None
        platform_obj = getattr(agent, "platform", None) if agent is not None else None
        if platform_obj is not None and hasattr(platform_obj, "capabilities"):
            return platform_obj.capabilities().to_dict()
        from core.device import get_platform_adapter

        return get_platform_adapter().capabilities().to_dict()

    def submit_text(
        self,
        text: str,
        *,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        return self.orchestrator.send_text(text, on_stream_chunk=on_chunk)

    def toggle_voice_mode(self) -> str:
        target = "text" if self.orchestrator.mode == "voice" else "voice"
        return self.orchestrator.switch_mode(target)

    def stop_voice(self) -> None:
        self.orchestrator.stop_voice_mode()

    def interrupt(self) -> None:
        self.orchestrator.interrupt("user_ui_interrupt")

    def pause(self) -> None:
        """Pause listening without closing the voice session."""
        self.orchestrator.pause_listening()

    def resume(self) -> None:
        """Resume listening in the current voice session."""
        self.orchestrator.resume_listening()

    def set_conversation_mode(self, mode: str) -> None:
        """Set voice conversation mode ('continuous' or 'push_to_talk')."""
        self.orchestrator.set_conversation_mode(mode)

    def toggle_conversation_mode(self) -> str:
        """Toggle between 'continuous' and 'push_to_talk' modes."""
        return self.orchestrator.toggle_continuous_mode()

    def start_push_to_talk(self) -> None:
        """Begin push-to-talk utterance recording."""
        self.orchestrator.start_push_to_talk()

    def stop_push_to_talk(self) -> str | None:
        """Stop push-to-talk recording, transcribe and process with assistant."""
        return self.orchestrator.stop_push_to_talk_and_transcribe()

    def apply_settings(self, **changes: Any) -> VoiceConfig:
        new_cfg = self.orchestrator.config.updated(**changes)
        self.orchestrator.update_config(new_cfg)
        identity_mgr = getattr(self.orchestrator, "identity_manager", None)
        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        active_identity = resolve_id() if callable(resolve_id) else None
        if identity_mgr is not None and active_identity is not None:
            new_cfg.save_for_user(identity_mgr.users_root, active_identity.user_id)
        else:
            new_cfg.save()
        return new_cfg

    def _permission_context(self) -> tuple[Any, Any]:
        resolve_pm = getattr(self.orchestrator, "_resolve_permission_manager", None)
        pm = resolve_pm() if callable(resolve_pm) else getattr(
            self.orchestrator, "permission_manager", None
        )
        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        identity = resolve_id() if callable(resolve_id) else None
        if pm is None:
            raise PermissionError("PermissionManager is not configured.")
        if identity is None:
            raise PermissionError("Signed-in user session is required to manage permissions.")
        return pm, identity

    @staticmethod
    def permission_catalog():
        from core.security.permissions import PermissionManager

        return PermissionManager.list_catalog()

    def list_permissions(
        self,
        category: str | None = None,
        *,
        include_revoked: bool = False,
    ):
        pm, identity = self._permission_context()
        return pm.list_permissions(
            identity,
            category=category,
            include_revoked=include_revoked,
        )

    def grant_permission(
        self,
        permission_key: str,
        *,
        decision: str = "allow",
        lifetime: str = "session",
        resource_scope: str = "*",
        ttl_seconds: float | None = None,
        reason: str = "Granted via RIYA UI",
        explicit_user_approval: bool = False,
    ):
        pm, identity = self._permission_context()
        return pm.grant_permission(
            identity,
            permission_key,
            decision=decision,
            lifetime=lifetime,
            resource_scope=resource_scope,
            ttl_seconds=ttl_seconds,
            reason=reason,
            actor="user_ui",
            explicit_user_approval=explicit_user_approval,
        )

    def deny_permission(
        self,
        permission_key: str,
        *,
        resource_scope: str = "*",
        lifetime: str = "persistent",
        ttl_seconds: float | None = None,
        reason: str = "Denied via RIYA UI",
    ):
        pm, identity = self._permission_context()
        return pm.deny_permission(
            identity,
            permission_key,
            resource_scope=resource_scope,
            lifetime=lifetime,
            ttl_seconds=ttl_seconds,
            reason=reason,
            actor="user_ui",
        )

    def revoke_permission(
        self,
        permission_key: str,
        *,
        resource_scope: str | None = None,
        grant_id: str | None = None,
        reason: str = "Revoked via RIYA UI",
    ) -> int:
        pm, identity = self._permission_context()
        return pm.revoke_permission(
            identity,
            permission_key,
            resource_scope=resource_scope,
            grant_id=grant_id,
            reason=reason,
            actor="user_ui",
        )

    def reset_permissions(
        self,
        category: str | None = None,
        *,
        reason: str = "Reset via RIYA UI",
    ) -> int:
        pm, identity = self._permission_context()
        return pm.reset_permissions(
            identity,
            category=category,
            reason=reason,
            actor="user_ui",
        )

    def audit_history(self, limit: int = 25):
        pm, identity = self._permission_context()
        return pm.audit_logger.list_audit_events(identity, limit=limit)

    def current_user_label(self) -> str:
        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        identity = resolve_id() if callable(resolve_id) else None
        if identity is None:
            return "Signed out"
        return f"{identity.display_name} (@{identity.username})"

    def ai_status_summary(self) -> tuple[bool, str]:
        assistant = getattr(self.orchestrator, "assistant", None)
        if assistant is None:
            return False, "AI: Unavailable"
        ai_rt = getattr(assistant, "ai", None)
        loaded = bool(getattr(ai_rt, "is_loaded", False) is True)
        if loaded:
            backend = str(getattr(ai_rt, "selected_backend", "unknown"))
            return True, f"AI: Ready ({backend})"
        reason = getattr(assistant, "ai_unavailable_reason", None)
        if reason:
            return False, "AI: Limited Mode"
        return False, "AI: Not Loaded"

    def _scoped_memory_manager(self):
        assistant = getattr(self.orchestrator, "assistant", None)
        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        identity = resolve_id() if callable(resolve_id) else None
        if assistant is None or identity is None:
            return None
        mgr = getattr(getattr(assistant, "memory", None), "manager", None)
        if mgr is None:
            return None
        return mgr.for_identity(identity)

    def list_memories(self, category: str | None = None) -> list[dict[str, Any]]:
        scoped = self._scoped_memory_manager()
        if scoped is None:
            return []
        return scoped.list_memories(category=category)

    def search_memories(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        scoped = self._scoped_memory_manager()
        if scoped is None:
            return []
        return scoped.search(
            query,
            category=category,
            limit=limit,
            include_scores=True,
        )

    def remember_fact(
        self,
        key: str,
        value: str,
        *,
        category: str = "important_facts",
        overwrite: bool = True,
    ) -> str:
        scoped = self._scoped_memory_manager()
        if scoped is None:
            raise PermissionError("Signed-in user session required to store memories.")
        res = scoped.remember(category, key, value, allow_overwrite=overwrite)
        self.orchestrator._notify_observers()
        return res

    def forget_fact(
        self,
        key: str,
        *,
        category: str = "important_facts",
    ) -> bool:
        scoped = self._scoped_memory_manager()
        if scoped is None:
            raise PermissionError("Signed-in user session required to delete memories.")
        deleted = bool(scoped.forget(category, key))
        self.orchestrator._notify_observers()
        return deleted

    def connectivity_status(self) -> dict[str, Any]:
        """Return RIYA's honest offline-first vs allowlisted online status."""
        allow_online = False
        allowed_domains: list[str] = []
        try:
            from core.browser.adapter import _DEFAULT_BROWSER

            allow_online = bool(getattr(_DEFAULT_BROWSER, "allow_online", False))
            allowed_domains = sorted(getattr(_DEFAULT_BROWSER, "allowed_domains", ()))
        except Exception:
            pass
        label = (
            "Online (Allowlisted Web)"
            if allow_online
            else "Offline-First (Local Only)"
        )
        return {
            "offline_mode": not allow_online,
            "online_enabled": allow_online,
            "allowed_domains": allowed_domains,
            "label": label,
        }

    def model_status(self) -> dict[str, Any]:
        """Return detailed local model readiness, backend, filename, and metrics."""
        assistant = getattr(self.orchestrator, "assistant", None)
        loaded, label = self.ai_status_summary()
        status_dict: dict[str, Any] = {}
        if assistant is not None and hasattr(assistant, "ai_status"):
            try:
                status_dict = dict(assistant.ai_status())
            except Exception:
                status_dict = {}
        model_path = status_dict.get("model_path") or (
            str(getattr(assistant, "model_path", "")) if assistant is not None and getattr(assistant, "model_path", None) else None
        )
        from pathlib import Path

        model_name = Path(model_path).name if model_path else None
        return {
            "loaded": loaded,
            "degraded_mode": not loaded,
            "backend": status_dict.get("backend", "unloaded" if not loaded else "auto"),
            "model_name": model_name,
            "model_path": model_path,
            "unavailable_reason": status_dict.get("unavailable_reason") or getattr(assistant, "ai_unavailable_reason", None),
            "label": label,
            "metrics": self.ai_metrics_summary(),
        }

    def ai_metrics_summary(self) -> dict[str, Any]:
        assistant = getattr(self.orchestrator, "assistant", None)
        if assistant is not None and hasattr(assistant, "ai_metrics"):
            return assistant.ai_metrics()
        loaded, summary = self.ai_status_summary()
        return {"loaded": loaded, "summary": summary}

    def record_feedback(
        self,
        rating: str,
        *,
        correction_text: str | None = None,
    ):
        assistant = getattr(self.orchestrator, "assistant", None)
        resolve_id = getattr(self.orchestrator, "_resolve_active_identity", None)
        identity = resolve_id() if callable(resolve_id) else None
        if assistant is None or identity is None:
            raise RuntimeError("Signed-in session required to record feedback.")
        return assistant.record_feedback(
            rating,
            correction_text=correction_text,
            user_context=identity,
        )

    def clear_error(self) -> None:
        if hasattr(self.orchestrator, "clear_error"):
            self.orchestrator.clear_error()

    def clear_transcript_history(self) -> None:
        self.orchestrator.session.clear_transcript()


AssistantInterface = VoiceUIController
AssistantSnapshot = VoiceVisualSnapshot


def launch_voice_gui(
    orchestrator: VoiceOrchestrator,
    *,
    coordinator: Any | None = None,
    on_logout: Callable[[], None] | None = None,
    root_window: Any | None = None,
    run_mainloop: bool = True,
) -> str:
    """Launch RIYA's unified offline macOS desktop assistant interface (Tkinter).

    Returns ``"logout"`` when exited via Logout, ``"closed"`` on window close,
    or ``"ready"`` when ``run_mainloop=False``.
    """

    import tkinter as tk
    from tkinter import messagebox, ttk

    controller = VoiceUIController(orchestrator)
    owns_root = root_window is None
    root = root_window or tk.Tk()
    root.title("RIYA — Unified Offline Personal AI Assistant")
    root.geometry("980x740")
    root.configure(bg="#11141a")
    exit_action = {"reason": "closed"}

    ui_events: queue.Queue[str] = queue.Queue()
    observer_cb = lambda: ui_events.put("refresh")
    orchestrator.add_observer(observer_cb)

    # ---- Header / Mode bar ----------------------------------------------
    top = tk.Frame(root, bg="#181d26", pady=10, padx=14)
    top.pack(fill=tk.X)

    title_lbl = tk.Label(
        top,
        text="RIYA",
        fg="#f3f6fb",
        bg="#181d26",
        font=("Helvetica", 16, "bold"),
    )
    title_lbl.pack(side=tk.LEFT)

    user_var = tk.StringVar(value=controller.current_user_label())
    tk.Label(
        top,
        textvariable=user_var,
        fg="#93c5fd",
        bg="#181d26",
        font=("Helvetica", 10, "bold"),
    ).pack(side=tk.LEFT, padx=(10, 6))

    conn_init = controller.connectivity_status()
    net_var = tk.StringVar(value=str(conn_init.get("label", "Offline-First (Local Only)")))
    net_lbl = tk.Label(
        top,
        textvariable=net_var,
        fg="#34d399" if conn_init.get("offline_mode", True) else "#38bdf8",
        bg="#142624",
        padx=8,
        pady=3,
        font=("Helvetica", 9, "bold"),
    )
    net_lbl.pack(side=tk.LEFT, padx=4)

    ai_ok_init, ai_lbl_init = controller.ai_status_summary()
    ai_var = tk.StringVar(value=ai_lbl_init)
    ai_lbl = tk.Label(
        top,
        textvariable=ai_var,
        fg="#10b981" if ai_ok_init else "#fbbf24",
        bg="#1e293b",
        padx=8,
        pady=3,
        font=("Helvetica", 9, "bold"),
    )
    ai_lbl.pack(side=tk.LEFT, padx=4)

    status_var = tk.StringVar(value="Ready (Text mode)")
    mic_var = tk.StringVar(value="MIC OFF")
    mic_lbl = tk.Label(
        top,
        textvariable=mic_var,
        fg="#98a2b3",
        bg="#232a36",
        padx=10,
        pady=4,
        font=("Helvetica", 10, "bold"),
    )
    mic_lbl.pack(side=tk.LEFT, padx=(8, 6))

    status_lbl = tk.Label(
        top,
        textvariable=status_var,
        fg="#cbd5e1",
        bg="#181d26",
        font=("Helvetica", 11),
    )
    status_lbl.pack(side=tk.LEFT, padx=4)

    btn_bar = tk.Frame(root, bg="#151a22", pady=6, padx=14)
    btn_bar.pack(fill=tk.X)

    logout_btn = ttk.Button(btn_bar, text="Logout")
    logout_btn.pack(side=tk.RIGHT, padx=(6, 0))

    diag_btn = ttk.Button(btn_bar, text="System Status")
    diag_btn.pack(side=tk.RIGHT, padx=(6, 0))

    tasks_btn = ttk.Button(btn_bar, text="Tasks & Skills")
    tasks_btn.pack(side=tk.RIGHT, padx=(6, 0))

    memories_btn = ttk.Button(btn_bar, text="Memories")
    memories_btn.pack(side=tk.RIGHT, padx=(6, 0))

    perms_btn = ttk.Button(btn_bar, text="Permissions & Trust")
    perms_btn.pack(side=tk.RIGHT, padx=(6, 0))

    settings_btn = ttk.Button(btn_bar, text="Settings")
    settings_btn.pack(side=tk.RIGHT, padx=(6, 0))

    mode_btn_var = tk.StringVar(value="Start Voice")
    mode_btn = ttk.Button(btn_bar, textvariable=mode_btn_var)
    mode_btn.pack(side=tk.LEFT, padx=(0, 6))

    pause_btn_var = tk.StringVar(value="Pause")
    pause_btn = ttk.Button(btn_bar, textvariable=pause_btn_var)
    pause_btn.pack(side=tk.LEFT, padx=(0, 6))

    ptt_btn_var = tk.StringVar(value="Push to Talk")
    ptt_btn = ttk.Button(btn_bar, textvariable=ptt_btn_var)
    ptt_btn.pack(side=tk.LEFT, padx=(0, 6))

    interrupt_btn = ttk.Button(btn_bar, text="Interrupt")
    interrupt_btn.pack(side=tk.LEFT, padx=(0, 6))

    # ---- Active voice waveform card -------------------------------------
    wave_frame = tk.Frame(root, bg="#151a22", pady=8, padx=14)
    wave_frame.pack(fill=tk.X)
    canvas = tk.Canvas(
        wave_frame,
        height=72,
        bg="#151a22",
        highlightthickness=0,
    )
    canvas.pack(fill=tk.X)

    # ---- Live Task Progress Bar -----------------------------------------
    task_bar = tk.Frame(root, bg="#172030", padx=14, pady=6)
    task_bar.pack(fill=tk.X)
    task_status_var = tk.StringVar(value="Task Engine: Idle")
    tk.Label(
        task_bar,
        textvariable=task_status_var,
        fg="#93c5fd",
        bg="#172030",
        font=("Helvetica", 10, "bold"),
        anchor="w",
    ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    cancel_task_btn = ttk.Button(
        task_bar,
        text="Cancel Active Task",
        command=lambda: (controller.cancel_task(), _render()),
    )
    cancel_task_btn.pack(side=tk.RIGHT)

    # ---- Interactive Approval / Confirmation Banner ---------------------
    approval_bar = tk.Frame(root, bg="#3b2712", padx=14, pady=8)
    approval_var = tk.StringVar(value="")
    tk.Label(
        approval_bar,
        textvariable=approval_var,
        fg="#fde68a",
        bg="#3b2712",
        font=("Helvetica", 10, "bold"),
        anchor="w",
        justify="left",
        wraplength=680,
    ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _on_approve_pending():
        if busy_state["thinking"]:
            return
        busy_state["thinking"] = True

        def _worker():
            try:
                controller.approve_pending_action()
            except Exception as exc:
                err_var.set(str(exc))
            finally:
                busy_state["thinking"] = False
                ui_events.put("refresh")

        threading.Thread(target=_worker, name="riya-ui-approve", daemon=True).start()

    def _on_reject_pending():
        if busy_state["thinking"]:
            return
        busy_state["thinking"] = True

        def _worker():
            try:
                controller.reject_pending_action()
            except Exception as exc:
                err_var.set(str(exc))
            finally:
                busy_state["thinking"] = False
                ui_events.put("refresh")

        threading.Thread(target=_worker, name="riya-ui-reject", daemon=True).start()

    reject_btn = ttk.Button(approval_bar, text="Reject / Cancel", command=_on_reject_pending)
    reject_btn.pack(side=tk.RIGHT, padx=(6, 0))
    approve_btn = ttk.Button(approval_bar, text="Approve & Proceed", command=_on_approve_pending)
    approve_btn.pack(side=tk.RIGHT)

    # ---- Error & Recovery Banner ----------------------------------------
    err_bar = tk.Frame(root, bg="#11141a", padx=14, pady=2)
    err_bar.pack(fill=tk.X)
    err_var = tk.StringVar(value="")
    err_lbl = tk.Label(
        err_bar,
        textvariable=err_var,
        fg="#fca5a5",
        bg="#11141a",
        font=("Helvetica", 10),
        anchor="w",
        justify="left",
        wraplength=800,
    )
    err_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
    clear_err_btn = ttk.Button(
        err_bar,
        text="Clear Error",
        command=lambda: (controller.clear_error(), err_var.set(""), _render()),
    )
    clear_err_btn.pack(side=tk.RIGHT)

    # ---- Conversation transcript ----------------------------------------
    transcript_box = tk.Text(
        root,
        wrap=tk.WORD,
        bg="#0d1015",
        fg="#e2e8f0",
        insertbackground="#e2e8f0",
        relief=tk.FLAT,
        padx=14,
        pady=12,
        font=("Helvetica", 12),
        state=tk.DISABLED,
    )
    transcript_box.pack(fill=tk.BOTH, expand=True, padx=14, pady=(4, 10))
    transcript_box.tag_configure("user", foreground="#7dd3fc")
    transcript_box.tag_configure("assistant", foreground="#e2e8f0")
    transcript_box.tag_configure("streaming", foreground="#a7f3d0")
    transcript_box.tag_configure("meta", foreground="#64748b", font=("Helvetica", 9))

    # ---- Text + Task input bar ------------------------------------------
    bottom = tk.Frame(root, bg="#11141a", padx=14, pady=12)
    bottom.pack(fill=tk.X)

    entry = tk.Entry(
        bottom,
        bg="#1b212c",
        fg="#f8fafc",
        insertbackground="#f8fafc",
        relief=tk.FLAT,
        font=("Helvetica", 12),
    )
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 8))

    busy_state = {"thinking": False}

    def _on_send(_event=None):
        text = entry.get().strip()
        if not text or busy_state["thinking"]:
            return
        entry.delete(0, tk.END)
        busy_state["thinking"] = True
        status_var.set("Processing...")
        send_btn.configure(state=tk.DISABLED)
        run_task_btn.configure(state=tk.DISABLED)

        def _worker():
            try:
                controller.submit_text(text)
            except Exception as exc:
                err_var.set(str(exc))
            finally:
                busy_state["thinking"] = False
                ui_events.put("refresh")

        threading.Thread(target=_worker, name="riya-ui-turn", daemon=True).start()

    def _on_run_task():
        text = entry.get().strip()
        if not text or busy_state["thinking"]:
            return
        entry.delete(0, tk.END)
        busy_state["thinking"] = True
        status_var.set("Running autonomous task...")
        send_btn.configure(state=tk.DISABLED)
        run_task_btn.configure(state=tk.DISABLED)

        def _worker():
            try:
                controller.submit_task(text)
            except Exception as exc:
                err_var.set(str(exc))
            finally:
                busy_state["thinking"] = False
                ui_events.put("refresh")

        threading.Thread(target=_worker, name="riya-ui-task", daemon=True).start()

    run_task_btn = ttk.Button(bottom, text="Run Task", command=_on_run_task)
    run_task_btn.pack(side=tk.RIGHT, padx=(6, 0))
    send_btn = ttk.Button(bottom, text="Send", command=_on_send)
    send_btn.pack(side=tk.RIGHT)
    entry.bind("<Return>", _on_send)

    def _on_toggle_voice():
        try:
            controller.toggle_voice_mode()
        except Exception as exc:
            err_var.set(str(exc))
        _render()

    mode_btn.configure(command=_on_toggle_voice)
    interrupt_btn.configure(command=lambda: (controller.interrupt(), _render()))

    def _on_pause_toggle():
        if controller.orchestrator.is_paused:
            controller.resume()
        else:
            controller.pause()
        _render()

    pause_btn.configure(command=_on_pause_toggle)

    def _on_ptt_press(_event=None):
        controller.start_push_to_talk()
        _render()

    def _on_ptt_release(_event=None):
        def _worker():
            controller.stop_push_to_talk()
            ui_events.put("refresh")

        threading.Thread(target=_worker, name="riya-ui-ptt", daemon=True).start()
        _render()

    ptt_btn.bind("<ButtonPress-1>", _on_ptt_press)
    ptt_btn.bind("<ButtonRelease-1>", _on_ptt_release)

    def _open_settings():
        win = tk.Toplevel(root)
        win.title("Voice Settings")
        win.geometry("460x430")
        win.configure(bg="#181d26")

        cfg = orchestrator.config
        in_devs = ["Default"] + [d.name for d in list_input_devices()]
        out_devs = ["Default"] + [d.name for d in list_output_devices()]
        lang_opts = controller.language_options()
        lang_labels = [lbl for _, lbl in lang_opts]
        lang_codes = [code for code, _ in lang_opts]
        voice_names = sorted({cfg.voice} | {v["name"] for v in LocalTTS.list_voices()})

        fields_data: list[tuple[str, Any]] = []

        def _row(label_text: str, widget_fn):
            row = tk.Frame(win, bg="#181d26", pady=4, padx=14)
            row.pack(fill=tk.X)
            tk.Label(row, text=label_text, fg="#cbd5e1", bg="#181d26", width=18, anchor="w").pack(side=tk.LEFT)
            w = widget_fn(row)
            w.pack(side=tk.RIGHT, fill=tk.X, expand=True)
            fields_data.append((label_text, w))

        in_var = tk.StringVar(value=str(cfg.input_device or "Default"))
        out_var = tk.StringVar(value=str(cfg.output_device or "Default"))
        lang_idx = lang_codes.index(cfg.language) if cfg.language in lang_codes else 0
        lang_var = tk.StringVar(value=lang_labels[lang_idx])
        voice_var = tk.StringVar(value=cfg.voice)
        rate_var = tk.IntVar(value=cfg.speech_rate_wpm)
        vol_var = tk.DoubleVar(value=cfg.volume)
        style_var = tk.StringVar(value=cfg.response_style)
        resp_var = tk.BooleanVar(value=cfg.voice_responses_enabled)

        _row("Input Device", lambda p: ttk.Combobox(p, textvariable=in_var, values=in_devs, state="readonly"))
        _row("Output Device", lambda p: ttk.Combobox(p, textvariable=out_var, values=out_devs, state="readonly"))
        _row("Language", lambda p: ttk.Combobox(p, textvariable=lang_var, values=lang_labels, state="readonly"))
        _row("Voice", lambda p: ttk.Combobox(p, textvariable=voice_var, values=voice_names, state="readonly"))
        _row("Response Style", lambda p: ttk.Combobox(p, textvariable=style_var, values=list(RESPONSE_STYLES), state="readonly"))
        _row("Speed (WPM)", lambda p: tk.Scale(p, from_=110, to=300, orient=tk.HORIZONTAL, variable=rate_var, bg="#181d26", fg="#f8fafc"))
        _row("Volume", lambda p: tk.Scale(p, from_=0.0, to=1.0, resolution=0.05, orient=tk.HORIZONTAL, variable=vol_var, bg="#181d26", fg="#f8fafc"))

        chk_row = tk.Frame(win, bg="#181d26", pady=6, padx=14)
        chk_row.pack(fill=tk.X)
        tk.Checkbutton(
            chk_row,
            text="Enable spoken voice responses",
            variable=resp_var,
            bg="#181d26",
            fg="#f8fafc",
            selectcolor="#11141a",
        ).pack(anchor="w")

        def _save():
            sel_lang = lang_codes[lang_labels.index(lang_var.get())]
            try:
                controller.apply_settings(
                    input_device=None if in_var.get() == "Default" else in_var.get(),
                    output_device=None if out_var.get() == "Default" else out_var.get(),
                    language=sel_lang,
                    voice=voice_var.get(),
                    response_style=style_var.get(),
                    speech_rate_wpm=int(rate_var.get()),
                    volume=float(vol_var.get()),
                    voice_responses_enabled=bool(resp_var.get()),
                )
            except Exception as exc:
                err_var.set(str(exc))
            win.destroy()
            _render()

        ttk.Button(win, text="Save Settings", command=_save).pack(pady=14)

    def _open_permissions():
        win = tk.Toplevel(root)
        win.title("RIYA Permissions & Trust")
        win.geometry("680x560")
        win.configure(bg="#181d26")

        catalog = controller.permission_catalog()
        keys = [spec.permission_key for spec in catalog]
        spec_by_key = {spec.permission_key: spec for spec in catalog}

        key_var = tk.StringVar(value=keys[0] if keys else "")
        scope_var = tk.StringVar(value="*")
        life_var = tk.StringVar(value="one_time")
        ttl_var = tk.StringVar(value="300")
        # Security: persistent approval checkbox ALWAYS starts unchecked (never pre-selected).
        persistent_ack_var = tk.BooleanVar(value=False)
        status_msg_var = tk.StringVar(value="")

        form = tk.Frame(win, bg="#181d26", padx=14, pady=10)
        form.pack(fill=tk.X)

        tk.Label(form, text="Capability:", fg="#cbd5e1", bg="#181d26").grid(row=0, column=0, sticky="w", pady=4)
        key_combo = ttk.Combobox(form, textvariable=key_var, values=keys, state="readonly", width=28)
        key_combo.grid(row=0, column=1, sticky="ew", padx=6)

        tk.Label(form, text="Lifetime:", fg="#cbd5e1", bg="#181d26").grid(row=0, column=2, sticky="w", pady=4)
        life_combo = ttk.Combobox(
            form,
            textvariable=life_var,
            values=["one_time", "session", "time_limited", "persistent"],
            state="readonly",
            width=16,
        )
        life_combo.grid(row=0, column=3, sticky="ew", padx=6)

        tk.Label(form, text="Scope:", fg="#cbd5e1", bg="#181d26").grid(row=1, column=0, sticky="w", pady=4)
        tk.Entry(form, textvariable=scope_var, bg="#11141a", fg="#f8fafc").grid(row=1, column=1, sticky="ew", padx=6)

        tk.Label(form, text="TTL (sec):", fg="#cbd5e1", bg="#181d26").grid(row=1, column=2, sticky="w", pady=4)
        tk.Entry(form, textvariable=ttl_var, bg="#11141a", fg="#f8fafc", width=12).grid(row=1, column=3, sticky="ew", padx=6)

        desc_var = tk.StringVar(value="")
        tk.Label(win, textvariable=desc_var, fg="#fbbf24", bg="#181d26", anchor="w", wraplength=640).pack(fill=tk.X, padx=14)

        tk.Checkbutton(
            win,
            text="I explicitly confirm granting PERSISTENT permission (unchecked by default)",
            variable=persistent_ack_var,
            bg="#181d26",
            fg="#f8fafc",
            selectcolor="#11141a",
        ).pack(anchor="w", padx=14, pady=4)

        tk.Label(win, textvariable=status_msg_var, fg="#fca5a5", bg="#181d26", anchor="w").pack(fill=tk.X, padx=14)

        box = tk.Text(win, bg="#0d1015", fg="#e2e8f0", height=18, relief=tk.FLAT, padx=10, pady=8)
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)

        def _refresh_perm_view(*_args):
            spec = spec_by_key.get(key_var.get())
            if spec is not None:
                sens = "[SENSITIVE] " if spec.sensitive else ""
                desc_var.set(f"{sens}{spec.category}: {spec.description}")
            box.configure(state=tk.NORMAL)
            box.delete("1.0", tk.END)
            try:
                grants = controller.list_permissions()
                box.insert(tk.END, "=== Active User Permission Rules ===\n")
                if not grants:
                    box.insert(tk.END, "  (No explicit grants yet; protected capabilities use default-deny.)\n")
                for g in grants:
                    exp = f" expires={g.expires_at}" if g.expires_at else ""
                    box.insert(
                        tk.END,
                        f"  - {g.permission_key} [{g.decision.value}] lifetime={g.lifetime.value} scope={g.resource_scope}{exp}\n",
                    )
                box.insert(tk.END, "\n=== Recent Audit Log ===\n")
                for ev in controller.audit_history(limit=15):
                    box.insert(
                        tk.END,
                        f"  #{ev.audit_id} {ev.created_at} {ev.permission_key} ({ev.resource_or_tool}) -> {ev.decision}: {ev.reason}\n",
                    )
            except Exception as exc:
                box.insert(tk.END, f"Permission error: {exc}\n")
            box.configure(state=tk.DISABLED)

        def _apply(decision: str):
            try:
                life = life_var.get()
                ttl = float(ttl_var.get()) if life == "time_limited" else None
                if decision == "deny":
                    controller.deny_permission(
                        key_var.get(),
                        resource_scope=scope_var.get() or "*",
                        lifetime="session" if life == "one_time" else life,
                        ttl_seconds=ttl,
                    )
                else:
                    controller.grant_permission(
                        key_var.get(),
                        decision=decision,
                        lifetime=life,
                        resource_scope=scope_var.get() or "*",
                        ttl_seconds=ttl,
                        explicit_user_approval=bool(persistent_ack_var.get()),
                    )
                persistent_ack_var.set(False)
                status_msg_var.set(f"Updated {key_var.get()} -> {decision} ({life}).")
            except Exception as exc:
                status_msg_var.set(f"Error: {exc}")
            _refresh_perm_view()

        def _revoke():
            try:
                cnt = controller.revoke_permission(key_var.get())
                status_msg_var.set(f"Revoked {cnt} rule(s) for {key_var.get()}.")
            except Exception as exc:
                status_msg_var.set(f"Error: {exc}")
            _refresh_perm_view()

        def _reset():
            try:
                cnt = controller.reset_permissions()
                status_msg_var.set(f"Reset {cnt} active permission rule(s).")
            except Exception as exc:
                status_msg_var.set(f"Error: {exc}")
            _refresh_perm_view()

        btn_row = tk.Frame(win, bg="#181d26", padx=14, pady=6)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Grant Allow", command=lambda: _apply("allow")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="Require Confirm", command=lambda: _apply("confirm")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="Deny Rule", command=lambda: _apply("deny")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="Revoke Key", command=_revoke).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="Reset All", command=_reset).pack(side=tk.RIGHT, padx=4)

        key_combo.bind("<<ComboboxSelected>>", _refresh_perm_view)
        _refresh_perm_view()

    def _open_memories():
        win = tk.Toplevel(root)
        win.title("RIYA Memories & Conversation History (User Isolated)")
        win.geometry("720x560")
        win.configure(bg="#181d26")

        info_var = tk.StringVar(value="Memories and history are strictly isolated for your signed-in profile.")
        tk.Label(win, textvariable=info_var, fg="#cbd5e1", bg="#181d26", anchor="w").pack(
            fill=tk.X, padx=14, pady=(8, 4)
        )

        search_row = tk.Frame(win, bg="#181d26", padx=14, pady=4)
        search_row.pack(fill=tk.X)
        search_var = tk.StringVar(value="")
        tk.Label(search_row, text="Search:", fg="#cbd5e1", bg="#181d26").pack(side=tk.LEFT)
        tk.Entry(search_row, textvariable=search_var, bg="#11141a", fg="#f8fafc").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6
        )

        edit_row = tk.Frame(win, bg="#181d26", padx=14, pady=4)
        edit_row.pack(fill=tk.X)
        cat_var = tk.StringVar(value="important_facts")
        mkey_var = tk.StringVar(value="")
        mval_var = tk.StringVar(value="")
        tk.Label(edit_row, text="Category:", fg="#cbd5e1", bg="#181d26").pack(side=tk.LEFT)
        ttk.Combobox(
            edit_row,
            textvariable=cat_var,
            values=[
                "important_facts",
                "user_preferences",
                "personal_instructions",
                "projects",
                "frequently_used",
            ],
            state="readonly",
            width=18,
        ).pack(side=tk.LEFT, padx=4)
        tk.Label(edit_row, text="Key:", fg="#cbd5e1", bg="#181d26").pack(side=tk.LEFT)
        tk.Entry(edit_row, textvariable=mkey_var, bg="#11141a", fg="#f8fafc", width=16).pack(side=tk.LEFT, padx=4)
        tk.Label(edit_row, text="Value:", fg="#cbd5e1", bg="#181d26").pack(side=tk.LEFT)
        tk.Entry(edit_row, textvariable=mval_var, bg="#11141a", fg="#f8fafc").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )

        box = tk.Text(win, bg="#0d1015", fg="#e2e8f0", relief=tk.FLAT, padx=12, pady=10)
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=6)

        def _render_rows(rows: list[dict[str, Any]]):
            box.configure(state=tk.NORMAL)
            box.delete("1.0", tk.END)
            if not rows:
                box.insert(tk.END, "(No matching memories found for your profile.)\n")
            for row in rows:
                box.insert(
                    tk.END,
                    f"• [{row.get('category', 'general')}] {row.get('key')} = {row.get('value')}\n",
                )
            box.configure(state=tk.DISABLED)

        def _refresh_mem():
            try:
                q = search_var.get().strip()
                rows = controller.search_memories(q) if q else controller.list_memories()
                _render_rows(rows)
            except Exception as exc:
                info_var.set(f"Memory access error: {exc}")

        def _save_mem():
            try:
                msg = controller.remember_fact(
                    mkey_var.get().strip(),
                    mval_var.get().strip(),
                    category=cat_var.get().strip() or "important_facts",
                )
                info_var.set(msg)
                _refresh_mem()
            except Exception as exc:
                info_var.set(f"Save blocked: {exc}")

        def _delete_mem():
            try:
                ok = controller.forget_fact(
                    mkey_var.get().strip(),
                    category=cat_var.get().strip() or "important_facts",
                )
                info_var.set("Deleted memory." if ok else "Memory key not found.")
                _refresh_mem()
            except Exception as exc:
                info_var.set(f"Delete blocked: {exc}")

        def _clear_hist():
            removed = controller.clear_conversation_history(clear_persisted=True)
            info_var.set(f"Cleared conversation history ({removed} persisted record(s) removed).")
            _render()

        ttk.Button(search_row, text="Search", command=_refresh_mem).pack(side=tk.LEFT, padx=2)
        ttk.Button(search_row, text="Show All", command=lambda: (search_var.set(""), _refresh_mem())).pack(side=tk.LEFT, padx=2)

        btn_f = tk.Frame(win, bg="#181d26", pady=8, padx=14)
        btn_f.pack(fill=tk.X)
        ttk.Button(btn_f, text="Save Memory", command=_save_mem).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_f, text="Forget Key", command=_delete_mem).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_f, text="Clear Chat History", command=_clear_hist).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_f, text="Refresh", command=_refresh_mem).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_f, text="Close", command=win.destroy).pack(side=tk.RIGHT)
        _refresh_mem()

    def _open_tasks_and_skills():
        win = tk.Toplevel(root)
        win.title("RIYA Autonomous Tasks, Skills & Tool Results")
        win.geometry("760x580")
        win.configure(bg="#181d26")

        top_f = tk.Frame(win, bg="#181d26", padx=14, pady=8)
        top_f.pack(fill=tk.X)
        goal_var = tk.StringVar(value="")
        task_id_var = tk.StringVar(value="")
        skills = controller.list_skills()
        skill_ids = [s["skill_id"] for s in skills]
        skill_var = tk.StringVar(value=skill_ids[0] if skill_ids else "")
        msg_var = tk.StringVar(value="Inspect tasks, resume checkpoints, run skills, and view tool results.")

        tk.Label(top_f, text="New Task Goal:", fg="#cbd5e1", bg="#181d26").grid(row=0, column=0, sticky="w", pady=3)
        tk.Entry(top_f, textvariable=goal_var, bg="#11141a", fg="#f8fafc", width=42).grid(row=0, column=1, sticky="ew", padx=6)

        tk.Label(top_f, text="Task ID (Resume/Cancel):", fg="#cbd5e1", bg="#181d26").grid(row=1, column=0, sticky="w", pady=3)
        tk.Entry(top_f, textvariable=task_id_var, bg="#11141a", fg="#f8fafc", width=28).grid(row=1, column=1, sticky="w", padx=6)

        tk.Label(top_f, text="Built-in Skill:", fg="#cbd5e1", bg="#181d26").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(top_f, textvariable=skill_var, values=skill_ids, state="readonly", width=26).grid(row=2, column=1, sticky="w", padx=6)

        tk.Label(win, textvariable=msg_var, fg="#93c5fd", bg="#181d26", anchor="w").pack(fill=tk.X, padx=14)
        box = tk.Text(win, bg="#0d1015", fg="#e2e8f0", relief=tk.FLAT, padx=12, pady=10)
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)

        def _refresh_tasks():
            box.configure(state=tk.NORMAL)
            box.delete("1.0", tk.END)
            box.insert(tk.END, "=== User Tasks ===\n")
            tasks = controller.list_tasks(limit=15)
            if not tasks:
                box.insert(tk.END, "  (No tasks recorded for this user yet.)\n")
            for t in tasks:
                box.insert(
                    tk.END,
                    f"  • [{str(t.get('status', '')).upper()}] id={t.get('task_id')} — {t.get('goal')}\n",
                )
            box.insert(tk.END, "\n=== Registered Skills ===\n")
            for s in skills:
                box.insert(
                    tk.END,
                    f"  • {s['skill_id']} (v{s['version']}): {s['description']}\n",
                )
            box.insert(tk.END, "\n=== Recent Tool & Skill Execution Results ===\n")
            results = controller.last_tool_results(limit=15)
            if not results:
                box.insert(tk.END, "  (No tool executions in this session yet.)\n")
            for r in results:
                box.insert(
                    tk.END,
                    f"  • [{r.get('status')}] {r.get('preview') or r.get('tool')} -> verified={r.get('verified')} {r.get('output') or r.get('reason')}\n",
                )
            box.configure(state=tk.DISABLED)

        def _submit_from_dlg():
            g = goal_var.get().strip()
            if not g:
                return
            goal_var.set("")
            res = controller.submit_task(g)
            task_id_var.set(str(res.get("task_id", "")))
            msg_var.set(f"Task [{res.get('status')}]: {res.get('summary')}")
            _refresh_tasks()
            _render()

        def _resume_from_dlg():
            tid = task_id_var.get().strip()
            if not tid:
                return
            try:
                res = controller.resume_task(tid)
                msg_var.set(f"Resumed {tid} -> {res.get('status')}: {res.get('summary')}")
            except Exception as exc:
                msg_var.set(f"Resume failed: {exc}")
            _refresh_tasks()
            _render()

        def _cancel_from_dlg():
            tid = task_id_var.get().strip() or None
            ok = controller.cancel_task(tid)
            msg_var.set("Cancelled task." if ok else "No matching active task to cancel.")
            _refresh_tasks()
            _render()

        def _run_skill_from_dlg():
            sid = skill_var.get().strip()
            if not sid:
                return
            try:
                res = controller.run_skill(sid)
                msg_var.set(f"Skill {sid} [{res.get('status')}]: {res.get('summary')}")
            except Exception as exc:
                msg_var.set(f"Skill error: {exc}")
            _refresh_tasks()
            _render()

        act_bar = tk.Frame(win, bg="#181d26", padx=14, pady=6)
        act_bar.pack(fill=tk.X)
        ttk.Button(act_bar, text="Submit Task", command=_submit_from_dlg).pack(side=tk.LEFT, padx=3)
        ttk.Button(act_bar, text="Resume Task", command=_resume_from_dlg).pack(side=tk.LEFT, padx=3)
        ttk.Button(act_bar, text="Cancel Task", command=_cancel_from_dlg).pack(side=tk.LEFT, padx=3)
        ttk.Button(act_bar, text="Run Skill", command=_run_skill_from_dlg).pack(side=tk.LEFT, padx=3)
        ttk.Button(act_bar, text="Refresh", command=_refresh_tasks).pack(side=tk.LEFT, padx=3)
        ttk.Button(act_bar, text="Close", command=win.destroy).pack(side=tk.RIGHT)
        _refresh_tasks()

    def _open_diagnostics():
        win = tk.Toplevel(root)
        win.title("RIYA System Status & Diagnostics")
        win.geometry("680x500")
        win.configure(bg="#181d26")

        box = tk.Text(win, bg="#0d1015", fg="#e2e8f0", relief=tk.FLAT, padx=12, pady=10)
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

        def _refresh_diag():
            box.configure(state=tk.NORMAL)
            box.delete("1.0", tk.END)
            if coordinator is not None:
                report = coordinator.diagnostics.run_all(
                    identity_manager=coordinator._identity_manager,
                    permission_manager=coordinator._permission_manager,
                    identity=orchestrator._resolve_active_identity(),
                    assistant=getattr(orchestrator, "assistant", None),
                    selected_model_path=getattr(coordinator, "selected_model_path", None),
                    ai_verified=getattr(coordinator, "ai_inference_verified", False),
                )
                for chk in report.checks:
                    box.insert(
                        tk.END,
                        f"[{chk.status.value.upper()}] {chk.name} ({chk.category}): {chk.summary}\n"
                        f"    {chk.detail}\n",
                    )
                    if chk.recovery_action:
                        box.insert(tk.END, f"    Recovery: {chk.recovery_action}\n")
            else:
                ai_ok, ai_msg = controller.ai_status_summary()
                snap = controller.snapshot()
                box.insert(tk.END, f"User: {controller.current_user_label()}\n")
                box.insert(tk.END, f"Connectivity: {snap.connectivity_status}\n")
                box.insert(tk.END, f"{ai_msg} (loaded={ai_ok})\n")
                box.insert(tk.END, f"Voice Mode: {snap.mode} | State: {snap.voice_state.value}\n")
                box.insert(tk.END, f"Input devices: {len(list_input_devices())} | Output devices: {len(list_output_devices())}\n")
            box.configure(state=tk.DISABLED)

        btn_f = tk.Frame(win, bg="#181d26", pady=8, padx=14)
        btn_f.pack(fill=tk.X)
        ttk.Button(btn_f, text="Re-run Checks", command=_refresh_diag).pack(side=tk.LEFT)
        ttk.Button(btn_f, text="Close", command=win.destroy).pack(side=tk.RIGHT)
        _refresh_diag()

    tick_after_id: list[str | None] = [None]

    def _cancel_tick() -> None:
        if tick_after_id[0] is not None:
            try:
                root.after_cancel(tick_after_id[0])
            except Exception:
                pass
            tick_after_id[0] = None

    def _on_logout():
        if not messagebox.askyesno("Logout", "Sign out of your current RIYA profile and close voice mode?"):
            return
        exit_action["reason"] = "logout"
        _cancel_tick()
        orchestrator.close()
        controller.clear_transcript_history()
        if callable(on_logout):
            on_logout()
        if owns_root:
            root.destroy()
        else:
            root.quit()

    settings_btn.configure(command=_open_settings)
    perms_btn.configure(command=_open_permissions)
    memories_btn.configure(command=_open_memories)
    tasks_btn.configure(command=_open_tasks_and_skills)
    diag_btn.configure(command=_open_diagnostics)
    logout_btn.configure(command=_on_logout)

    last_rendered_count = -1
    last_streaming_text = ""

    def _render() -> None:
        nonlocal last_rendered_count, last_streaming_text
        snap = controller.snapshot()
        user_var.set(controller.current_user_label())
        net_var.set(snap.connectivity_status)
        ai_ok, ai_text = controller.ai_status_summary()
        ai_var.set(ai_text)
        ai_lbl.configure(fg="#10b981" if ai_ok else "#fbbf24")
        status_var.set("Processing..." if busy_state["thinking"] else snap.status_label)
        send_btn.configure(state=tk.DISABLED if busy_state["thinking"] else tk.NORMAL)
        run_task_btn.configure(state=tk.DISABLED if busy_state["thinking"] else tk.NORMAL)

        if snap.error_message:
            rec_suffix = f" | Recovery: {snap.recovery_message}" if snap.recovery_message else ""
            err_var.set(f"{snap.error_message}{rec_suffix}")
        else:
            err_var.set("")

        if snap.active_task_progress:
            tp = snap.active_task_progress
            t_status = str(tp.get("status", "running")).upper()
            done_s = tp.get("steps_completed", 0)
            tot_s = tp.get("steps_total", 0)
            goal_s = str(tp.get("goal") or tp.get("message") or "")[:64]
            task_status_var.set(f"Task [{t_status}] ({done_s}/{tot_s} steps): {goal_s}")
        else:
            task_status_var.set(f"Operation State: {snap.operation_state.upper()}")

        if snap.pending_confirmation is not None:
            pc = snap.pending_confirmation
            approval_var.set(
                f"APPROVAL REQUIRED [{pc.get('risk_tier', 'sensitive')}]: {pc.get('preview')} — {pc.get('reason')}"
            )
            if not approval_bar.winfo_ismapped():
                approval_bar.pack(fill=tk.X, before=transcript_box)
        else:
            approval_var.set("")
            if approval_bar.winfo_ismapped():
                approval_bar.pack_forget()

        if snap.microphone_active:
            mic_var.set("MIC LIVE")
            mic_lbl.configure(fg="#10b981", bg="#14332a")
        else:
            mic_var.set("MIC OFF")
            mic_lbl.configure(fg="#98a2b3", bg="#232a36")

        mode_btn_var.set("Stop Voice" if snap.mode == "voice" else "Start Voice")
        pause_btn_var.set("Resume" if snap.is_paused else "Pause")
        pause_btn.configure(state=tk.NORMAL if snap.mode == "voice" else tk.DISABLED)
        ptt_btn_var.set("Recording..." if snap.is_recording else "Push to Talk")
        interrupt_btn.configure(state=tk.NORMAL if snap.can_interrupt else tk.DISABLED)

        # Draw 24-bar audio waveform without excessive redraw cost.
        canvas.delete("all")
        w = max(240, canvas.winfo_width() or 780)
        h = 72
        bar_w = max(4, (w - 40) // (len(snap.waveform_bars) * 2))
        gap = bar_w
        color = "#38bdf8" if snap.assistant_speaking else ("#34d399" if snap.microphone_active else "#475569")
        start_x = (w - len(snap.waveform_bars) * (bar_w + gap)) // 2
        for idx, amp in enumerate(snap.waveform_bars):
            bh = max(4, int(amp * (h - 14)))
            x0 = start_x + idx * (bar_w + gap)
            y0 = (h - bh) // 2
            canvas.create_rectangle(x0, y0, x0 + bar_w, y0 + bh, fill=color, outline="")

        if len(snap.transcript) != last_rendered_count or snap.streaming_text != last_streaming_text:
            last_rendered_count = len(snap.transcript)
            last_streaming_text = snap.streaming_text
            transcript_box.configure(state=tk.NORMAL)
            transcript_box.delete("1.0", tk.END)
            for item in snap.transcript:
                tag = "user" if item.role == "user" else "assistant"
                speaker = "You" if item.role == "user" else "RIYA"
                transcript_box.insert(tk.END, f"{speaker} [{item.modality}]: ", (tag,))
                transcript_box.insert(tk.END, f"{item.text}\n", (tag,))
            if snap.streaming_text:
                transcript_box.insert(tk.END, "RIYA [streaming]: ", ("streaming",))
                transcript_box.insert(tk.END, f"{snap.streaming_text}\n", ("streaming",))
            transcript_box.see(tk.END)
            transcript_box.configure(state=tk.DISABLED)

    def _tick() -> None:
        drained = False
        while True:
            try:
                ui_events.get_nowait()
                drained = True
            except queue.Empty:
                break
        if drained or orchestrator.state in ACTIVE_STATES:
            _render()
        # Low-power interval when idle; smooth 15 fps only while active.
        interval_ms = 66 if orchestrator.state in ACTIVE_STATES else 350
        tick_after_id[0] = root.after(interval_ms, _tick)

    def _on_close() -> None:
        exit_action["reason"] = "closed"
        _cancel_tick()
        orchestrator.close()
        controller.clear_transcript_history()
        if owns_root:
            root.destroy()
        else:
            root.quit()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    _render()
    if not run_mainloop:
        root.update_idletasks()
        return "ready"
    tick_after_id[0] = root.after(100, _tick)
    if owns_root:
        root.mainloop()
    else:
        root.mainloop()
    return exit_action["reason"]
