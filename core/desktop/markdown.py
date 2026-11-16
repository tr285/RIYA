"""Markdown parser and formatted renderer for Tkinter Text widgets."""

from __future__ import annotations

import re
from typing import Any

from core.desktop.theme import FontSystem, ThemePalette


class MarkdownRenderer:
    """Parses markdown text and inserts styled content into a Tkinter Text widget."""

    # Regex patterns for markdown constructs
    CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_\-+]*)\n(.*?)```", re.DOTALL)
    INLINE_CODE_RE = re.compile(r"`([^`]+)`")
    BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
    ITALIC_RE = re.compile(r"\*([^*]+)\*")
    HEADER_RE = re.compile(r"^(#{1,3})\s+(.*)$")
    LIST_ITEM_RE = re.compile(r"^(\s*[-*+]|\s*\d+\.)\s+(.*)$")
    BLOCKQUOTE_RE = re.compile(r"^>\s*(.*)$")

    @classmethod
    def configure_tags(cls, text_widget: Any, theme: ThemePalette) -> None:
        """Apply font and color tags matching the current theme palette."""
        tw = text_widget

        # Headings
        tw.tag_configure(
            "md_h1",
            font=("-apple-system", 15, "bold"),
            foreground=theme.text_primary,
            spacing1=8,
            spacing3=4,
        )
        tw.tag_configure(
            "md_h2",
            font=("-apple-system", 13, "bold"),
            foreground=theme.text_primary,
            spacing1=6,
            spacing3=3,
        )
        tw.tag_configure(
            "md_h3",
            font=("-apple-system", 12, "bold"),
            foreground=theme.text_secondary,
            spacing1=4,
            spacing3=2,
        )

        # Inline formatting
        tw.tag_configure("md_bold", font=FontSystem.BODY_BOLD)
        tw.tag_configure("md_italic", font=("-apple-system", 12, "italic"))
        tw.tag_configure(
            "md_inline_code",
            font=FontSystem.MONOSPACE_SMALL,
            background=theme.code_header_bg,
            foreground=theme.code_fg,
        )

        # Lists & Quotes
        tw.tag_configure("md_list_bullet", foreground=theme.accent, font=FontSystem.BODY_BOLD)
        tw.tag_configure("md_list_body", font=FontSystem.BODY, lmargin1=16, lmargin2=28)
        tw.tag_configure(
            "md_quote",
            font=("-apple-system", 12, "italic"),
            foreground=theme.text_secondary,
            lmargin1=18,
            lmargin2=18,
        )

        # Code Block
        tw.tag_configure(
            "md_code_header",
            background=theme.code_header_bg,
            foreground=theme.text_secondary,
            font=FontSystem.CAPTION_BOLD,
            spacing1=4,
            spacing3=2,
        )
        tw.tag_configure(
            "md_code_body",
            font=FontSystem.MONOSPACE,
            background=theme.code_bg,
            foreground=theme.code_fg,
            lmargin1=12,
            lmargin2=12,
            spacing1=2,
            spacing3=2,
        )

    @classmethod
    def render(
        cls,
        text_widget: Any,
        content: str,
        theme: ThemePalette,
        *,
        base_tag: str | None = None,
        tk_module: Any = None,
    ) -> None:
        """Render markdown text with styled headings, code blocks, lists, and inline formatting."""
        import tkinter as tk
        tw = text_widget
        tk_mod = tk_module or tk

        # Split content into code blocks vs non-code blocks
        pos = 0
        for match in cls.CODE_BLOCK_RE.finditer(content):
            start, end = match.span()
            # Render preceding text
            if start > pos:
                cls._render_prose(tw, content[pos:start], theme, base_tag)

            # Render code block
            lang = match.group(1).strip() or "text"
            code_text = match.group(2).rstrip("\n")
            cls._render_code_block(tw, lang, code_text, theme, tk_mod)
            pos = end

        # Render remaining text
        if pos < len(content):
            cls._render_prose(tw, content[pos:], theme, base_tag)

    @classmethod
    def _render_code_block(
        cls,
        tw: Any,
        lang: str,
        code_text: str,
        theme: ThemePalette,
        tk_mod: Any,
    ) -> None:
        """Render a formatted code block container with a copy-to-clipboard button."""
        import tkinter as tk

        # Embed a copyable code header
        header_frame = tk.Frame(tw, bg=theme.code_header_bg, padx=8, pady=3)
        lbl = tk.Label(
            header_frame,
            text=f"  {lang.upper()}",
            font=FontSystem.CAPTION_BOLD,
            fg=theme.text_secondary,
            bg=theme.code_header_bg,
        )
        lbl.pack(side=tk.LEFT)

        def _copy_code():
            try:
                tw.clipboard_clear()
                tw.clipboard_append(code_text)
                copy_btn.configure(text="Copied!")
                tw.after(2000, lambda: copy_btn.configure(text="Copy"))
            except Exception:
                pass

        copy_btn = tk.Button(
            header_frame,
            text="Copy",
            font=FontSystem.CAPTION,
            bg=theme.bg_surface_elevated,
            fg=theme.text_primary,
            activebackground=theme.accent,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=1,
            cursor="hand2",
            command=_copy_code,
        )
        copy_btn.pack(side=tk.RIGHT)

        tw.insert(tk.END, f"\n[{lang.upper()}]\n", ("md_code_header",))
        tw.window_create(tk.END, window=header_frame)
        tw.insert(tk.END, "\n")

        # Code block text
        lines = code_text.split("\n")
        for line in lines:
            tw.insert(tk.END, f"  {line}\n", ("md_code_body",))
        tw.insert(tk.END, "\n")

    @classmethod
    def _render_prose(
        cls,
        tw: Any,
        text: str,
        theme: ThemePalette,
        base_tag: str | None,
    ) -> None:
        """Render markdown prose lines handling headers, lists, quotes, and inline styles."""
        import tkinter as tk

        lines = text.split("\n")
        for line in lines:
            # Check Header
            header_m = cls.HEADER_RE.match(line)
            if header_m:
                level = len(header_m.group(1))
                h_text = header_m.group(2)
                tag = f"md_h{level}"
                tw.insert(tk.END, f"{h_text}\n", (tag,))
                continue

            # Check Blockquote
            quote_m = cls.BLOCKQUOTE_RE.match(line)
            if quote_m:
                q_text = quote_m.group(1)
                tw.insert(tk.END, f"│ {q_text}\n", ("md_quote",))
                continue

            # Check List Item
            list_m = cls.LIST_ITEM_RE.match(line)
            if list_m:
                bullet = list_m.group(1).strip()
                item_text = list_m.group(2)
                tw.insert(tk.END, f"  {bullet} ", ("md_list_bullet",))
                cls._render_inline_spans(tw, item_text, base_tag, extra_tag="md_list_body")
                tw.insert(tk.END, "\n")
                continue

            # Standard paragraph line
            if line.strip():
                cls._render_inline_spans(tw, line, base_tag)
                tw.insert(tk.END, "\n")
            else:
                tw.insert(tk.END, "\n")

    @classmethod
    def _render_inline_spans(
        cls,
        tw: Any,
        line: str,
        base_tag: str | None = None,
        extra_tag: str | None = None,
    ) -> None:
        """Parse bold, italic, and inline code spans inside a line of text."""
        import tkinter as tk

        tags: tuple[str, ...] = tuple(t for t in (base_tag, extra_tag) if t)

        # Tokenize inline code first to avoid interpreting markdown inside code spans
        segments = []
        last_idx = 0
        for m in cls.INLINE_CODE_RE.finditer(line):
            start, end = m.span()
            if start > last_idx:
                segments.append((line[last_idx:start], "text"))
            segments.append((m.group(1), "code"))
            last_idx = end
        if last_idx < len(line):
            segments.append((line[last_idx:], "text"))

        for text_part, kind in segments:
            if kind == "code":
                code_tags = tags + ("md_inline_code",)
                tw.insert(tk.END, f" {text_part} ", code_tags)
            else:
                # Parse bold and italic in normal text
                cls._render_bold_italic(tw, text_part, tags)

    @classmethod
    def _render_bold_italic(cls, tw: Any, text: str, base_tags: tuple[str, ...]) -> None:
        import tkinter as tk

        # Bold spans
        parts = []
        last_idx = 0
        for m in cls.BOLD_RE.finditer(text):
            start, end = m.span()
            if start > last_idx:
                parts.append((text[last_idx:start], False))
            parts.append((m.group(1), True))
            last_idx = end
        if last_idx < len(text):
            parts.append((text[last_idx:], False))

        for chunk, is_bold in parts:
            if is_bold:
                tw.insert(tk.END, chunk, base_tags + ("md_bold",))
            else:
                # Check italic inside non-bold
                sub_last = 0
                for im in cls.ITALIC_RE.finditer(chunk):
                    s, e = im.span()
                    if s > sub_last:
                        tw.insert(tk.END, chunk[sub_last:s], base_tags)
                    tw.insert(tk.END, im.group(1), base_tags + ("md_italic",))
                    sub_last = e
                if sub_last < len(chunk):
                    tw.insert(tk.END, chunk[sub_last:], base_tags)

