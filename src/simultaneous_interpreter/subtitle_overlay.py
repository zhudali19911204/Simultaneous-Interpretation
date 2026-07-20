from __future__ import annotations

import re
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from typing import Callable

from . import ui_theme as ui


INTERIM_HOLD_MS = 6_000
MAX_SUBTITLE_CHARS = 96
MAX_ROLLING_ITEMS = 3
SOURCE_MISSING = "[原文缺失]"
TRANSLATION_MISSING = "[译文缺失]"
CHINESE_PENDING = "正在翻译…"
ENGLISH_PENDING = "Translating…"


@dataclass(frozen=True)
class OverlayText:
    role: str
    chinese: str
    english: str
    is_final: bool
    alignment_status: str = "matched"


def compact_subtitle(text: str, max_chars: int = MAX_SUBTITLE_CHARS) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return "…" + compact[-(max_chars - 1) :].lstrip()


def make_overlay_text(
    direction: str,
    source_text: str,
    translated_text: str,
    is_final: bool,
    alignment_status: str = "matched",
) -> OverlayText:
    source = compact_subtitle(source_text)
    translated = compact_subtitle(translated_text)
    if is_final and alignment_status == "source_only" and not translated:
        translated = TRANSLATION_MISSING
    elif is_final and alignment_status == "translation_only" and not source:
        source = SOURCE_MISSING
    if direction == "incoming":
        role = "对方说"
        chinese, english = translated, source
    elif direction == "outgoing":
        role = "我说"
        chinese, english = source, translated
    else:
        raise ValueError(f"不支持的字幕方向：{direction}")
    return OverlayText(
        role=role,
        chinese=chinese or CHINESE_PENDING,
        english=english or ENGLISH_PENDING,
        is_final=is_final,
        alignment_status=alignment_status,
    )


class RollingSubtitleBuffer:
    def __init__(self, max_items: int = MAX_ROLLING_ITEMS) -> None:
        if max_items < 1:
            raise ValueError("滚动字幕条目数必须大于 0")
        self._max_items = max_items
        self._history: deque[OverlayText] = deque(maxlen=max_items)
        self._current: OverlayText | None = None

    @property
    def entries(self) -> tuple[OverlayText, ...]:
        entries = [*self._history]
        if self._current:
            entries.append(self._current)
        return tuple(entries[-self._max_items :])

    def update(self, text: OverlayText) -> None:
        if text.is_final:
            if not self._history or self._history[-1] != text:
                self._history.append(text)
            self._current = None
        else:
            self._current = self._merge_interim(text)

    def clear_current(self) -> None:
        self._current = None

    def reset(self) -> None:
        self._history.clear()
        self._current = None

    def _merge_interim(self, text: OverlayText) -> OverlayText:
        current = self._current
        if not current or not self._same_utterance(current, text):
            return text
        chinese = text.chinese
        english = text.english
        if chinese == CHINESE_PENDING and current.chinese != CHINESE_PENDING:
            chinese = current.chinese
        if english == ENGLISH_PENDING and current.english != ENGLISH_PENDING:
            english = current.english
        return OverlayText(
            role=text.role,
            chinese=chinese,
            english=english,
            is_final=False,
            alignment_status=text.alignment_status,
        )

    @staticmethod
    def _same_utterance(previous: OverlayText, current: OverlayText) -> bool:
        if previous.role != current.role:
            return False
        if current.role == "我说":
            previous_source, current_source = previous.chinese, current.chinese
        else:
            previous_source, current_source = previous.english, current.english
        if not previous_source or not current_source:
            return False
        return previous_source.startswith(current_source) or current_source.startswith(
            previous_source
        )


class SubtitleOverlay:
    def __init__(
        self,
        owner: tk.Tk,
        fonts: ui.ThemeFonts,
        on_hidden: Callable[[], None],
    ) -> None:
        self._owner = owner
        self._fonts = fonts
        self._on_hidden = on_hidden
        self._window: tk.Toplevel | None = None
        self._subtitle_text: tk.Text | None = None
        self._clear_after_id: str | None = None
        self._notice_after_id: str | None = None
        self._connection_notice = ""
        self._connection_notice_kind = "warning"
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._positioned = False
        self._buffer = RollingSubtitleBuffer()

    @property
    def visible(self) -> bool:
        return bool(
            self._window
            and self._window.winfo_exists()
            and self._window.state() == "normal"
        )

    @property
    def connection_notice(self) -> str:
        return self._connection_notice

    def show(self) -> None:
        if not self._window or not self._window.winfo_exists():
            self._create_window()
        assert self._window is not None
        self._window.deiconify()
        self._window.attributes("-topmost", True)
        self._window.lift()
        if not self._positioned:
            self._place_at_screen_bottom()
        self._render()

    def hide(self, *, notify: bool = False) -> None:
        if self._window and self._window.winfo_exists():
            self._window.withdraw()
        if notify:
            self._on_hidden()

    def destroy(self) -> None:
        self._cancel_clear()
        self._cancel_notice_clear()
        if self._window and self._window.winfo_exists():
            self._window.destroy()
        self._window = None

    def set_connection_notice(
        self,
        text: str,
        *,
        kind: str = "warning",
        clear_after_ms: int | None = None,
    ) -> None:
        self._cancel_notice_clear()
        self._connection_notice = compact_subtitle(text, 120)
        self._connection_notice_kind = (
            kind if kind in {"warning", "error", "info"} else "warning"
        )
        self._render()
        if self._connection_notice and clear_after_ms:
            self._notice_after_id = self._owner.after(
                clear_after_ms,
                self.clear_connection_notice,
            )

    def clear_connection_notice(self) -> None:
        self._cancel_notice_clear()
        self._connection_notice = ""
        self._render()

    def update_translation(
        self,
        direction: str,
        source_text: str,
        translated_text: str,
        is_final: bool,
        alignment_status: str = "matched",
    ) -> None:
        self._buffer.update(
            make_overlay_text(
                direction,
                source_text,
                translated_text,
                is_final,
                alignment_status,
            )
        )
        self._render()
        self._cancel_clear()
        if not is_final:
            self._clear_after_id = self._owner.after(
                INTERIM_HOLD_MS,
                self._expire_interim,
            )

    def reset(self) -> None:
        self._clear_after_id = None
        self._buffer.reset()
        self._render()

    def _expire_interim(self) -> None:
        self._clear_after_id = None
        self._buffer.clear_current()
        self._render()

    @staticmethod
    def _waiting_text() -> OverlayText:
        return OverlayText(
            role="演示字幕",
            chinese="等待下一句…",
            english="Waiting for the next sentence…",
            is_final=False,
        )

    def _create_window(self) -> None:
        window = tk.Toplevel(self._owner)
        self._window = window
        window.withdraw()
        window.overrideredirect(True)
        window.configure(bg=ui.INFO)
        window.attributes("-topmost", True)
        try:
            window.attributes("-alpha", 0.94)
            window.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        accent = tk.Frame(window, bg=ui.INFO, height=3)
        accent.pack(fill="x")
        accent.pack_propagate(False)
        body = tk.Frame(
            window,
            bg=ui.TEXT_SURFACE,
            padx=24,
            pady=12,
        )
        body.pack(fill="both", expand=True, padx=1, pady=(0, 1))

        self._subtitle_text = tk.Text(
            body,
            wrap="word",
            width=1,
            height=6,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            bg=ui.TEXT_SURFACE,
            fg=ui.TEXT_PRIMARY,
            insertbackground=ui.TEXT_PRIMARY,
            selectbackground=ui.SELECTION,
            selectforeground="#FFFFFF",
            padx=4,
            pady=0,
            cursor="arrow",
            state="disabled",
        )
        self._subtitle_text.pack(fill="both", expand=True)
        self._subtitle_text.tag_configure(
            "role",
            foreground=ui.TEXT_MUTED,
            font=(self._fonts.ui, 9, "bold"),
        )
        self._subtitle_text.tag_configure(
            "zh",
            foreground=ui.INFO,
            font=(self._fonts.transcript, 17, "bold"),
            spacing3=2,
        )
        self._subtitle_text.tag_configure(
            "en_code",
            foreground=ui.TEXT_MUTED,
            font=(self._fonts.ui, 9, "bold"),
        )
        self._subtitle_text.tag_configure(
            "en",
            foreground=ui.TEXT_PRIMARY,
            font=(self._fonts.ui, 14),
            spacing3=5,
        )
        self._subtitle_text.tag_configure(
            "old",
            foreground=ui.TEXT_MUTED,
        )
        self._subtitle_text.tag_configure(
            "notice_warning",
            foreground=ui.WARNING,
            font=(self._fonts.ui, 10, "bold"),
        )
        self._subtitle_text.tag_configure(
            "notice_error",
            foreground=ui.DANGER,
            font=(self._fonts.ui, 10, "bold"),
        )
        self._subtitle_text.tag_configure(
            "notice_info",
            foreground=ui.INFO,
            font=(self._fonts.ui, 10, "bold"),
        )

        for widget in (
            window,
            accent,
            body,
            self._subtitle_text,
        ):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<Double-Button-1>", self._hide_from_overlay)
        self._subtitle_text.bind("<MouseWheel>", self._scroll_subtitles)

    def _place_at_screen_bottom(self) -> None:
        assert self._window is not None
        screen_width = self._window.winfo_screenwidth()
        screen_height = self._window.winfo_screenheight()
        width = max(640, min(1600, screen_width - 96))
        height = 220
        x = max(0, (screen_width - width) // 2)
        y = max(20, screen_height - height - 64)
        self._window.geometry(f"{width}x{height}+{x}+{y}")
        self._positioned = True

    def _render(self) -> None:
        if not self._subtitle_text:
            return
        entries = self._buffer.entries or (self._waiting_text(),)
        text = self._subtitle_text
        text.configure(state="normal")
        text.delete("1.0", "end")
        last_index = len(entries) - 1
        for index, entry in enumerate(entries):
            old_tag = ("old",) if index != last_index else ()
            text.insert("end", f"{entry.role}  ", ("role", *old_tag))
            text.insert("end", entry.chinese + "\n", ("zh", *old_tag))
            text.insert("end", "EN    ", ("en_code", *old_tag))
            text.insert("end", entry.english, ("en", *old_tag))
            if index != last_index:
                text.insert("end", "\n")
        if self._connection_notice:
            text.insert("end", "\n\n")
            text.insert(
                "end",
                self._connection_notice,
                (f"notice_{self._connection_notice_kind}",),
            )
        text.see("end")
        text.configure(state="disabled")

    def _cancel_clear(self) -> None:
        if self._clear_after_id:
            self._owner.after_cancel(self._clear_after_id)
            self._clear_after_id = None

    def _cancel_notice_clear(self) -> None:
        if self._notice_after_id:
            self._owner.after_cancel(self._notice_after_id)
            self._notice_after_id = None

    def _start_drag(self, event: tk.Event) -> None:
        if not self._window:
            return
        self._drag_origin = (
            event.x_root,
            event.y_root,
            self._window.winfo_x(),
            self._window.winfo_y(),
        )

    def _drag(self, event: tk.Event) -> None:
        if not self._window or not self._drag_origin:
            return
        start_x, start_y, window_x, window_y = self._drag_origin
        x = window_x + event.x_root - start_x
        y = window_y + event.y_root - start_y
        self._window.geometry(f"{x:+d}{y:+d}")

    def _scroll_subtitles(self, event: tk.Event) -> str:
        if self._subtitle_text:
            units = -1 if event.delta > 0 else 1
            self._subtitle_text.yview_scroll(units, "units")
        return "break"

    def _hide_from_overlay(self, _event: tk.Event) -> None:
        self.hide(notify=True)
