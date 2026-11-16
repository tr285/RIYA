"""Modern macOS conversation view with rich message cards, markdown, voice controls, and streaming."""

from __future__ import annotations

import threading
import tkinter as tk
from typing import Any, Callable

from core.desktop.markdown import MarkdownRenderer
from core.desktop.theme import FontSystem, ThemePalette
from core.voice.session import TranscriptEntry
from core.voice.ui import VoiceUIController, VoiceVisualSnapshot


class ChatView(tk.Frame):
    """Main conversational interface combining text messaging, voice orchestration, and system banners."""

    def __init__(
        self,
        master: Any,
        controller: VoiceUIController,
        theme: ThemePalette,
        on_theme_toggle: Callable[[], None],
        on_refresh_requested: Callable[[], None],
        **kwargs: Any,
    ):
        super().__init__(master, bg=theme.bg_base, **kwargs)
        self._controller = controller
        self._theme = theme
        self._on_theme_toggle = on_theme_toggle
        self._on_refresh_requested = on_refresh_requested

        self._busy_state = {"thinking": False}
        self._last_rendered_count = 0
        self._rendered_streaming_len = 0

        self._build_ui()

    def _build_ui(self) -> None:
        th = self._theme

        # =====================================================================
        # 1. TOP HEADER / STATUS BAR
        # =====================================================================
        self._header = tk.Frame(self, bg=th.bg_surface, padx=16, pady=10)
        self._header.pack(fill=tk.X)

        # Left status group
        status_left = tk.Frame(self._header, bg=th.bg_surface)
        status_left.pack(side=tk.LEFT, fill=tk.Y)

        self._title_lbl = tk.Label(
            status_left,
            text="Conversation",
            font=FontSystem.SECTION_HEADER,
            fg=th.text_primary,
            bg=th.bg_surface,
        )
        self._title_lbl.pack(side=tk.LEFT, padx=(0, 12))

        # Model Status Chip
        self._ai_status_lbl = tk.Label(
            status_left,
            text="AI: Ready",
            font=FontSystem.CAPTION_BOLD,
            fg=th.status_success_fg,
            bg=th.status_success_bg,
            padx=8,
            pady=3,
        )
        self._ai_status_lbl.pack(side=tk.LEFT, padx=4)

        # Voice State Chip
        self._voice_state_lbl = tk.Label(
            status_left,
            text="Text Mode",
            font=FontSystem.CAPTION,
            fg=th.text_secondary,
            bg=th.bg_surface_elevated,
            padx=8,
            pady=3,
        )
        self._voice_state_lbl.pack(side=tk.LEFT, padx=4)

        # Microphone State Badge
        self._mic_badge = tk.Label(
            status_left,
            text="MIC OFF",
            font=FontSystem.CAPTION_BOLD,
            fg=th.waveform_mic_off,
            bg=th.bg_surface_elevated,
            padx=8,
            pady=3,
        )
        self._mic_badge.pack(side=tk.LEFT, padx=4)

        # Right header controls: Waveform canvas & Theme toggle
        status_right = tk.Frame(self._header, bg=th.bg_surface)
        status_right.pack(side=tk.RIGHT)

        # Theme toggle button
        self._theme_btn = tk.Button(
            status_right,
            text="☀️ Light" if th.name == "dark" else "🌙 Dark",
            font=FontSystem.CAPTION,
            bg=th.bg_surface_elevated,
            fg=th.text_primary,
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._on_theme_toggle,
        )
        self._theme_btn.pack(side=tk.RIGHT, padx=(8, 0))

        # Clear transcript button
        self._clear_btn = tk.Button(
            status_right,
            text="Clear",
            font=FontSystem.CAPTION,
            bg=th.bg_surface_elevated,
            fg=th.text_secondary,
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._on_clear_transcript,
        )
        self._clear_btn.pack(side=tk.RIGHT, padx=4)

        # Waveform Canvas
        self._canvas = tk.Canvas(
            status_right,
            width=160,
            height=28,
            bg=th.bg_surface,
            highlightthickness=0,
        )
        self._canvas.pack(side=tk.RIGHT, padx=8)

        # Divider
        self._header_divider = tk.Frame(self, bg=th.border, height=1)
        self._header_divider.pack(fill=tk.X)

        # =====================================================================
        # 2. BANNERS: CONFIRMATION, TASK PROGRESS, ERROR
        # =====================================================================
        # Confirmation / Approval Banner (Hidden by default)
        self._confirm_bar = tk.Frame(self, bg=th.status_warning_bg, padx=16, pady=8)
        self._confirm_label = tk.Label(
            self._confirm_bar,
            text="",
            font=FontSystem.ITEM_TITLE,
            fg=th.status_warning_fg,
            bg=th.status_warning_bg,
            anchor="w",
            wraplength=700,
            justify="left",
        )
        self._confirm_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._reject_btn = tk.Button(
            self._confirm_bar,
            text="Reject / Cancel",
            font=FontSystem.CAPTION_BOLD,
            bg=th.bg_surface,
            fg=th.status_error_fg,
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._on_reject_pending,
        )
        self._reject_btn.pack(side=tk.RIGHT, padx=(6, 0))

        self._approve_btn = tk.Button(
            self._confirm_bar,
            text="Approve & Proceed",
            font=FontSystem.CAPTION_BOLD,
            bg=th.status_success_fg,
            fg="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._on_approve_pending,
        )
        self._approve_btn.pack(side=tk.RIGHT)

        # Task Progress Banner (Hidden by default)
        self._task_bar = tk.Frame(self, bg=th.status_info_bg, padx=16, pady=6)
        self._task_label = tk.Label(
            self._task_bar,
            text="",
            font=FontSystem.ITEM_TITLE,
            fg=th.status_info_fg,
            bg=th.status_info_bg,
            anchor="w",
        )
        self._task_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._cancel_task_btn = tk.Button(
            self._task_bar,
            text="Cancel Task",
            font=FontSystem.CAPTION,
            bg=th.bg_surface,
            fg=th.status_error_fg,
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._on_cancel_task,
        )
        self._cancel_task_btn.pack(side=tk.RIGHT)

        # Error Banner (Hidden by default)
        self._err_bar = tk.Frame(self, bg=th.status_error_bg, padx=16, pady=6)
        self._err_label = tk.Label(
            self._err_bar,
            text="",
            font=FontSystem.BODY,
            fg=th.status_error_fg,
            bg=th.status_error_bg,
            anchor="w",
            wraplength=700,
            justify="left",
        )
        self._err_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._clear_err_btn = tk.Button(
            self._err_bar,
            text="Dismiss",
            font=FontSystem.CAPTION,
            bg=th.bg_surface,
            fg=th.status_error_fg,
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._on_clear_error,
        )
        self._clear_err_btn.pack(side=tk.RIGHT)

        # =====================================================================
        # 3. CONVERSATION TRANSCRIPT (RICH TEXT CONTAINER)
        # =====================================================================
        transcript_container = tk.Frame(self, bg=th.bg_base)
        transcript_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        self._scrollbar = tk.Scrollbar(transcript_container, orient=tk.VERTICAL)
        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._transcript = tk.Text(
            transcript_container,
            wrap=tk.WORD,
            bg=th.bg_base,
            fg=th.text_primary,
            insertbackground=th.text_primary,
            relief=tk.FLAT,
            padx=16,
            pady=14,
            font=FontSystem.BODY,
            yscrollcommand=self._scrollbar.set,
            state=tk.DISABLED,
            bd=0,
            highlightthickness=0,
        )
        self._transcript.pack(fill=tk.BOTH, expand=True)
        self._scrollbar.config(command=self._transcript.yview)

        # Configure markdown and message bubble tags
        self._configure_transcript_tags()

        # =====================================================================
        # 4. INPUT AREA & VOICE CONTROLS
        # =====================================================================
        self._footer_divider = tk.Frame(self, bg=th.border, height=1)
        self._footer_divider.pack(fill=tk.X)

        self._footer = tk.Frame(self, bg=th.bg_surface, padx=16, pady=12)
        self._footer.pack(fill=tk.X)

        # Controls row (Voice buttons: PTT, Continuous, Pause, Interrupt)
        ctrl_row = tk.Frame(self._footer, bg=th.bg_surface)
        ctrl_row.pack(fill=tk.X, pady=(0, 8))

        # Voice Mode Toggle
        self._mode_btn = tk.Button(
            ctrl_row,
            text="🎙️ Start Voice",
            font=FontSystem.CAPTION_BOLD,
            bg=th.bg_surface_elevated,
            fg=th.text_primary,
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._on_toggle_voice_mode,
        )
        self._mode_btn.pack(side=tk.LEFT, padx=(0, 6))

        # Push-to-Talk (Hold to talk)
        self._ptt_btn = tk.Button(
            ctrl_row,
            text="Push to Talk",
            font=FontSystem.CAPTION,
            bg=th.bg_surface_elevated,
            fg=th.text_primary,
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self._ptt_btn.pack(side=tk.LEFT, padx=(0, 6))
        self._ptt_btn.bind("<ButtonPress-1>", self._on_ptt_press)
        self._ptt_btn.bind("<ButtonRelease-1>", self._on_ptt_release)

        # Pause / Resume Voice
        self._pause_btn = tk.Button(
            ctrl_row,
            text="Pause",
            font=FontSystem.CAPTION,
            bg=th.bg_surface_elevated,
            fg=th.text_secondary,
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=4,
            cursor="hand2",
            command=self._on_toggle_pause,
        )
        self._pause_btn.pack(side=tk.LEFT, padx=(0, 6))

        # Interrupt Button (Barge-in)
        self._interrupt_btn = tk.Button(
            ctrl_row,
            text="⏹️ Interrupt",
            font=FontSystem.CAPTION,
            bg=th.bg_surface_elevated,
            fg=th.status_error_fg,
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=4,
            cursor="hand2",
            command=self._on_interrupt,
        )
        self._interrupt_btn.pack(side=tk.LEFT)

        # Status text for turn processing
        self._turn_status_lbl = tk.Label(
            ctrl_row,
            text="",
            font=FontSystem.CAPTION,
            fg=th.text_muted,
            bg=th.bg_surface,
        )
        self._turn_status_lbl.pack(side=tk.RIGHT)

        # Input row (Text entry + Send button)
        input_row = tk.Frame(self._footer, bg=th.bg_surface)
        input_row.pack(fill=tk.X)

        self._entry = tk.Entry(
            input_row,
            bg=th.bg_input,
            fg=th.text_primary,
            insertbackground=th.text_primary,
            relief=tk.SOLID,
            bd=1,
            font=FontSystem.BODY,
            highlightcolor=th.accent,
            highlightbackground=th.border,
            highlightthickness=1,
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 8))
        self._entry.bind("<Return>", self._on_send)

        self._send_btn = tk.Button(
            input_row,
            text="Send  ↑",
            font=FontSystem.BODY_BOLD,
            bg=th.accent,
            fg="#ffffff",
            activebackground=th.accent_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
            command=self._on_send,
        )
        self._send_btn.pack(side=tk.RIGHT)

    def _configure_transcript_tags(self) -> None:
        th = self._theme
        tw = self._transcript

        # Configure markdown renderer tags
        MarkdownRenderer.configure_tags(tw, th)

        # User bubble tag
        tw.tag_configure(
            "bubble_user",
            foreground=th.user_bubble_fg,
            background=th.user_bubble_bg,
            font=FontSystem.BODY_BOLD,
            lmargin1=120,
            lmargin2=130,
            rmargin=16,
            spacing1=6,
            spacing3=6,
        )
        tw.tag_configure(
            "user_header",
            foreground=th.accent,
            font=FontSystem.CAPTION_BOLD,
            lmargin1=120,
            spacing1=8,
            spacing3=2,
        )

        # Assistant bubble tag
        tw.tag_configure(
            "bubble_assistant",
            foreground=th.assistant_bubble_fg,
            background=th.assistant_bubble_bg,
            font=FontSystem.BODY,
            lmargin1=16,
            lmargin2=24,
            rmargin=60,
            spacing1=4,
            spacing3=6,
        )
        tw.tag_configure(
            "assistant_header",
            foreground=th.text_muted,
            font=FontSystem.CAPTION_BOLD,
            lmargin1=16,
            spacing1=10,
            spacing3=2,
        )

        # Meta & Streaming tags
        tw.tag_configure("msg_meta", foreground=th.text_muted, font=FontSystem.CAPTION)
        tw.tag_configure(
            "streaming_text",
            foreground=th.status_success_fg,
            font=FontSystem.BODY,
            lmargin1=16,
            lmargin2=24,
            spacing1=4,
            spacing3=4,
        )

    # =========================================================================
    # REFRESH & STATE SYNCHRONIZATION
    # =========================================================================
    def render_snapshot(self, snap: VoiceVisualSnapshot) -> None:
        """Update all visual indicators and append new transcript messages."""
        th = self._theme

        # 1. Update AI status chip
        ai_ok = "not loaded" not in snap.model_status_label.lower() and "limited" not in snap.model_status_label.lower()
        self._ai_status_lbl.configure(
            text=snap.model_status_label,
            fg=th.status_success_fg if ai_ok else th.status_warning_fg,
            bg=th.status_success_bg if ai_ok else th.status_warning_bg,
        )

        # 2. Update Voice State chip
        paused_txt = " (Paused)" if snap.is_paused else ""
        state_txt = f"{snap.status_label}{paused_txt}"
        self._voice_state_lbl.configure(text=state_txt)

        # 3. Update Microphone Badge
        if snap.is_paused:
            self._mic_badge.configure(text="MIC PAUSED", fg=th.waveform_mic_paused)
        elif snap.microphone_active:
            self._mic_badge.configure(text="MIC LIVE", fg=th.waveform_mic_live)
        else:
            self._mic_badge.configure(text="MIC OFF", fg=th.waveform_mic_off)

        # 4. Update Voice Buttons
        if snap.mode == "voice":
            self._mode_btn.configure(text="⏹️ Text Mode", fg=th.status_error_fg)
            self._pause_btn.configure(
                text="Resume" if snap.is_paused else "Pause",
                state=tk.NORMAL,
            )
            self._ptt_btn.configure(state=tk.NORMAL)
        else:
            self._mode_btn.configure(text="🎙️ Start Voice", fg=th.text_primary)
            self._pause_btn.configure(text="Pause", state=tk.DISABLED)
            self._ptt_btn.configure(state=tk.NORMAL)

        self._interrupt_btn.configure(
            state=tk.NORMAL if snap.can_interrupt else tk.DISABLED,
        )

        # 5. Confirmation Banner
        if snap.pending_confirmation:
            tool = snap.pending_confirmation.get("tool", "action")
            params = snap.pending_confirmation.get("parameters", {})
            self._confirm_label.configure(
                text=f"⚠️ Approval Required: Execute '{tool}' with parameters: {params}"
            )
            if not self._confirm_bar.winfo_manager():
                self._confirm_bar.pack(fill=tk.X, after=self._header_divider)
        else:
            if self._confirm_bar.winfo_manager():
                self._confirm_bar.pack_forget()

        # 6. Task Progress Banner
        if snap.active_task_progress:
            step = snap.active_task_progress.get("step_description", "Autonomous task in progress...")
            self._task_label.configure(text=f"⚡ Active Task: {step}")
            if not self._task_bar.winfo_manager():
                self._task_bar.pack(fill=tk.X, after=self._header_divider)
        else:
            if self._task_bar.winfo_manager():
                self._task_bar.pack_forget()

        # 7. Error Banner
        if snap.error_message:
            self._err_label.configure(text=f"❌ {snap.error_message}")
            if not self._err_bar.winfo_manager():
                self._err_bar.pack(fill=tk.X, after=self._header_divider)
        else:
            if self._err_bar.winfo_manager():
                self._err_bar.pack_forget()

        # 8. Draw Audio Waveform
        self._draw_waveform(snap)

        # 9. Update Transcript Content
        self._sync_transcript(snap)

    def _draw_waveform(self, snap: VoiceVisualSnapshot) -> None:
        th = self._theme
        cv = self._canvas
        cv.delete("all")

        bars = snap.waveform_bars or (0.1,) * 24
        width = 160
        height = 28
        bar_count = len(bars)
        bar_width = (width - (bar_count - 1) * 2) / bar_count
        mid_y = height / 2

        if snap.microphone_active and not snap.is_paused:
            bar_color = th.waveform_active
        elif snap.is_paused:
            bar_color = th.waveform_mic_paused
        else:
            bar_color = th.waveform_idle

        for i, val in enumerate(bars):
            h = max(2.0, val * (height - 4))
            x1 = i * (bar_width + 2)
            y1 = mid_y - h / 2
            x2 = x1 + bar_width
            y2 = mid_y + h / 2
            cv.create_rectangle(x1, y1, x2, y2, fill=bar_color, outline="")

    def _sync_transcript(self, snap: VoiceVisualSnapshot) -> None:
        tw = self._transcript
        entries = snap.transcript
        streaming = snap.streaming_text

        # If transcript was cleared externally, reset
        if len(entries) < self._last_rendered_count:
            tw.configure(state=tk.NORMAL)
            tw.delete("1.0", tk.END)
            tw.configure(state=tk.DISABLED)
            self._last_rendered_count = 0

        # Render newly added entries
        if len(entries) > self._last_rendered_count:
            tw.configure(state=tk.NORMAL)
            # Remove any active streaming indicator tag
            if "streaming_start" in tw.mark_names():
                tw.delete("streaming_start", tk.END)

            for entry in entries[self._last_rendered_count:]:
                self._render_message_bubble(entry)

            self._last_rendered_count = len(entries)
            tw.configure(state=tk.DISABLED)
            tw.see(tk.END)

        # Handle live streaming typewriter update
        if streaming and streaming != getattr(self, "_last_streaming_text", ""):
            self._last_streaming_text = streaming
            tw.configure(state=tk.NORMAL)
            if tw.tag_ranges("streaming_tag"):
                tw.delete("streaming_tag.first", "streaming_tag.last")
            tw.mark_set("streaming_start", tk.END)
            tw.insert(tk.END, f"\nRIYA: {streaming} ▍\n", ("streaming_text", "streaming_tag"))
            tw.configure(state=tk.DISABLED)
            tw.see(tk.END)
        elif not streaming and hasattr(self, "_last_streaming_text"):
            delattr(self, "_last_streaming_text")
            tw.configure(state=tk.NORMAL)
            if tw.tag_ranges("streaming_tag"):
                tw.delete("streaming_tag.first", "streaming_tag.last")
            tw.configure(state=tk.DISABLED)

    def _render_message_bubble(self, entry: TranscriptEntry) -> None:
        import time
        th = self._theme
        tw = self._transcript
        role = str(getattr(entry, "role", getattr(entry, "speaker", "assistant"))).lower()
        is_user = role == "user"
        ts = getattr(entry, "timestamp", "")
        if isinstance(ts, (int, float)):
            ts_str = time.strftime("%H:%M:%S", time.localtime(ts))
        else:
            ts_str = str(ts)[:8] if ts else time.strftime("%H:%M:%S")

        if is_user:
            tw.insert(tk.END, f"\nYou ({ts_str})\n", ("user_header",))
            tw.insert(tk.END, f"  {entry.text}  \n", ("bubble_user",))
        else:
            tw.insert(tk.END, f"\nRIYA ({ts_str})\n", ("assistant_header",))
            # Render markdown formatted assistant text
            MarkdownRenderer.render(
                tw,
                entry.text,
                th,
                base_tag="bubble_assistant",
            )
            # Add message action footer
            self._insert_assistant_actions(entry.text)

    def _insert_assistant_actions(self, message_text: str) -> None:
        th = self._theme
        tw = self._transcript

        action_frame = tk.Frame(tw, bg=th.bg_base, padx=16, pady=2)

        def _copy_msg():
            try:
                tw.clipboard_clear()
                tw.clipboard_append(message_text)
                btn_copy.configure(text="Copied!")
                tw.after(2000, lambda: btn_copy.configure(text="Copy Response"))
            except Exception:
                pass

        btn_copy = tk.Button(
            action_frame,
            text="Copy Response",
            font=FontSystem.CAPTION,
            bg=th.bg_surface_elevated,
            fg=th.text_secondary,
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=1,
            cursor="hand2",
            command=_copy_msg,
        )
        btn_copy.pack(side=tk.LEFT, padx=(0, 6))

        def _regen():
            self._controller.submit_text("Please try answering that again.")
            self._on_refresh_requested()

        btn_regen = tk.Button(
            action_frame,
            text="Regenerate",
            font=FontSystem.CAPTION,
            bg=th.bg_surface_elevated,
            fg=th.text_secondary,
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=1,
            cursor="hand2",
            command=_regen,
        )
        btn_regen.pack(side=tk.LEFT)

        tw.window_create(tk.END, window=action_frame)
        tw.insert(tk.END, "\n")

    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================
    def _on_send(self, _event=None) -> None:
        text = self._entry.get().strip()
        if not text or self._busy_state["thinking"]:
            return

        self._entry.delete(0, tk.END)
        self._busy_state["thinking"] = True
        self._turn_status_lbl.configure(text="Thinking...")
        self._send_btn.configure(state=tk.DISABLED)

        def _worker():
            try:
                self._controller.submit_text(text)
            except Exception as exc:
                try:
                    self._err_label.configure(text=f"❌ {exc}")
                    self._err_bar.pack(fill=tk.X, after=self._header_divider)
                except Exception:
                    pass
            finally:
                self._busy_state["thinking"] = False
                try:
                    self._turn_status_lbl.configure(text="")
                    self._send_btn.configure(state=tk.NORMAL)
                    self._on_refresh_requested()
                except Exception:
                    pass

        threading.Thread(target=_worker, name="riya-chat-turn", daemon=True).start()

    def _on_toggle_voice_mode(self) -> None:
        self._controller.toggle_voice_mode()
        self._on_refresh_requested()

    def _on_toggle_pause(self) -> None:
        if self._controller.orchestrator.is_paused:
            self._controller.resume()
        else:
            self._controller.pause()
        self._on_refresh_requested()

    def _on_ptt_press(self, _event=None) -> None:
        self._ptt_btn.configure(text="● Recording...", bg=self._theme.waveform_mic_live, fg="#ffffff")
        self._controller.start_push_to_talk()
        self._on_refresh_requested()

    def _on_ptt_release(self, _event=None) -> None:
        self._ptt_btn.configure(text="Push to Talk", bg=self._theme.bg_surface_elevated, fg=self._theme.text_primary)
        self._controller.stop_push_to_talk()
        self._on_refresh_requested()

    def _on_interrupt(self) -> None:
        self._controller.interrupt()
        self._on_refresh_requested()

    def _on_approve_pending(self) -> None:
        def _worker():
            try:
                self._controller.approve_pending_action()
            finally:
                self._on_refresh_requested()
        threading.Thread(target=_worker, daemon=True).start()

    def _on_reject_pending(self) -> None:
        def _worker():
            try:
                self._controller.reject_pending_action()
            finally:
                self._on_refresh_requested()
        threading.Thread(target=_worker, daemon=True).start()

    def _on_cancel_task(self) -> None:
        self._controller.cancel_task()
        self._on_refresh_requested()

    def _on_clear_error(self) -> None:
        self._controller.clear_error()
        self._err_bar.pack_forget()
        self._on_refresh_requested()

    def _on_clear_transcript(self) -> None:
        self._controller.clear_transcript_history()
        tw = self._transcript
        tw.configure(state=tk.NORMAL)
        tw.delete("1.0", tk.END)
        tw.configure(state=tk.DISABLED)
        self._last_rendered_count = 0
        self._on_refresh_requested()

    def apply_theme(self, theme: ThemePalette) -> None:
        """Dynamically re-apply theme tokens across the chat view."""
        self._theme = theme
        th = theme
        self.configure(bg=th.bg_base)
        self._header.configure(bg=th.bg_surface)
        self._header_divider.configure(bg=th.border)
        self._footer_divider.configure(bg=th.border)
        self._footer.configure(bg=th.bg_surface)
        self._title_lbl.configure(bg=th.bg_surface, fg=th.text_primary)
        self._theme_btn.configure(
            text="☀️ Light" if th.name == "dark" else "🌙 Dark",
            bg=th.bg_surface_elevated,
            fg=th.text_primary,
        )
        self._clear_btn.configure(bg=th.bg_surface_elevated, fg=th.text_secondary)
        self._canvas.configure(bg=th.bg_surface)
        self._transcript.configure(
            bg=th.bg_base,
            fg=th.text_primary,
            insertbackground=th.text_primary,
        )
        self._entry.configure(
            bg=th.bg_input,
            fg=th.text_primary,
            insertbackground=th.text_primary,
            highlightcolor=th.accent,
            highlightbackground=th.border,
        )
        self._send_btn.configure(bg=th.accent, activebackground=th.accent_hover)
        self._configure_transcript_tags()

