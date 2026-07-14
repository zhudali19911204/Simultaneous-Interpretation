from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .audio_devices import AudioDeviceCatalog, AudioDeviceChoice
from .credential_store import (
    clear_credentials,
    clear_minutes_api_key,
    load_credentials,
    load_minutes_api_key,
    save_credentials,
    save_minutes_api_key,
)
from .meeting_minutes import (
    DEFAULT_MINUTES_MODEL,
    MeetingTurn,
    MinutesResult,
    OpenAICompatibleMeetingMinutesClient,
    normalize_model_name,
)
from .provider_config import (
    INTERPRETER_BY_ID,
    INTERPRETER_BY_LABEL,
    INTERPRETER_PROVIDERS,
    INTERPRETER_QWEN_COMPATIBLE,
    LLM_BY_ID,
    LLM_BY_LABEL,
    LLM_PROVIDERS,
    LLM_QWEN_WORKSPACE,
    parse_extra_body,
    resolve_minutes_url,
)
from .qwen_backend import (
    QwenInterpreterSession,
    TranslationEvent,
    UsageStats,
    build_api_url,
    normalize_realtime_model_name,
)
from .settings_store import AppSettings, load_settings, save_settings
from . import ui_theme as ui


@dataclass(frozen=True)
class UiMessage:
    kind: str
    payload: Any = None


class InterpreterApp:
    POLL_MS = 50
    VOICE_CHOICES = (
        ("Tina（女声·温暖）", "Tina"),
        ("Cindy（女声·甜美）", "Cindy"),
        ("Liora Mira（女声·柔和）", "Liora Mira"),
        ("Raymond（男声·清亮）", "Raymond"),
        ("Ethan（男声·阳光）", "Ethan"),
        ("Theo Calm（男声·沉稳）", "Theo Calm"),
        ("Harvey（男声·低沉）", "Harvey"),
        ("Evan（男声·年轻）", "Evan"),
    )
    VOICE_API_NAMES = dict(VOICE_CHOICES)
    MINUTES_MODEL_CHOICES = (
        "qwen3.5-flash",
        "qwen-plus",
        "qwen-max",
    )

    def __init__(self) -> None:
        try:
            saved_credentials = load_credentials()
        except OSError:
            saved_credentials = None
        try:
            saved_minutes_api_key = load_minutes_api_key()
        except OSError:
            saved_minutes_api_key = ""
        saved_settings = load_settings()

        self.root = tk.Tk()
        self.root.title("Teams 中英同声翻译助手")
        self.root.geometry("1120x780")
        self.root.minsize(820, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._events: queue.Queue[UiMessage] = queue.Queue()
        self._session: QwenInterpreterSession | None = None
        self._state = "idle"
        self._catalog = AudioDeviceCatalog()
        self._choice_maps: dict[str, dict[str, AudioDeviceChoice]] = {}
        self._meeting_turns: list[MeetingTurn] = []
        self._meeting_started_at: datetime | None = None
        self._meeting_ended_at: datetime | None = None
        self._minutes_generating = False
        self._minutes_window: tk.Toplevel | None = None
        self._settings_window: tk.Toplevel | None = None
        self._settings_snapshot: dict[str, str] | None = None
        self._settings_error_var: tk.StringVar | None = None
        self._settings_focus_targets: dict[str, tk.Widget] = {}
        self._audio_panel_expanded = True
        self._responsive_mode = ""
        self._audio_layout_mode = ""
        self._busy = False
        self._busy_after_id: str | None = None

        self.api_key_var = tk.StringVar(
            value=os.getenv("DASHSCOPE_API_KEY", "")
            or (saved_credentials.api_key if saved_credentials else "")
        )
        self.workspace_id_var = tk.StringVar(
            value=os.getenv("DASHSCOPE_WORKSPACE_ID", "")
            or (saved_credentials.workspace_id if saved_credentials else "")
        )
        interpreter_preset = INTERPRETER_BY_ID[saved_settings.interpreter_provider]
        minutes_preset = LLM_BY_ID[saved_settings.meeting_minutes_provider]
        self.interpreter_provider_var = tk.StringVar(value=interpreter_preset.label)
        self.interpreter_model_var = tk.StringVar(
            value=os.getenv("INTERPRETER_MODEL", "")
            or saved_settings.interpreter_model
        )
        self.interpreter_websocket_url_var = tk.StringVar(
            value=os.getenv("INTERPRETER_WEBSOCKET_URL", "")
            or saved_settings.interpreter_websocket_url
        )
        self.minutes_provider_var = tk.StringVar(value=minutes_preset.label)
        self.minutes_api_key_var = tk.StringVar(
            value=os.getenv("MINUTES_API_KEY", "") or saved_minutes_api_key
        )
        self.minutes_api_url_var = tk.StringVar(
            value=os.getenv("MINUTES_API_URL", "")
            or saved_settings.meeting_minutes_api_url
            or minutes_preset.api_url
        )
        self.minutes_extra_body_var = tk.StringVar(
            value=os.getenv("MINUTES_EXTRA_BODY", "")
            or saved_settings.meeting_minutes_extra_body
        )
        self.microphone_var = tk.StringVar()
        self.loopback_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.voice_var = tk.StringVar(value=self.VOICE_CHOICES[0][0])
        self.minutes_model_var = tk.StringVar(
            value=os.getenv("MINUTES_MODEL", "")
            or os.getenv("QWEN_MINUTES_MODEL", "")
            or saved_settings.meeting_minutes_model
            or DEFAULT_MINUTES_MODEL
        )
        self.status_var = tk.StringVar(value="尚未启动")
        self.usage_var = tk.StringVar(value="Token：0")
        self.incoming_interim_var = tk.StringVar(value="等待对方说英文…")
        self.outgoing_interim_var = tk.StringVar(value="等待你说中文…")
        self.always_on_top_var = tk.BooleanVar(value=False)
        self.silence_gate_var = tk.BooleanVar(value=True)
        self.audio_toggle_var = tk.StringVar(value="收起音频路由")
        self.show_api_key_var = tk.BooleanVar(value=False)
        self.show_minutes_api_key_var = tk.BooleanVar(value=False)

        self._configure_style()
        self._build_ui()
        self._refresh_devices(show_errors=False)
        self.root.bind("<Control-comma>", lambda _event: self._open_settings())
        self.root.bind("<F5>", self._on_refresh_shortcut)
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.after(self.POLL_MS, self._drain_events)

    def run(self) -> None:
        self.root.mainloop()

    def _configure_style(self) -> None:
        self._fonts = ui.configure_theme(self.root)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="Background.TFrame", padding=ui.SPACE_4)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)
        self.outer = outer

        header = ttk.Frame(outer, style="Background.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, ui.SPACE_3))
        header.columnconfigure(0, weight=1)

        title_group = ttk.Frame(header, style="Background.TFrame")
        title_group.grid(row=0, column=0, sticky="w")
        ttk.Label(
            title_group,
            text="Teams 中英同声翻译助手",
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            title_group,
            text="对方英文 → 你看中文   ·   你说中文 → 对方听英文",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(ui.SPACE_1, 0))

        header_actions = ttk.Frame(header, style="Background.TFrame")
        header_actions.grid(row=0, column=1, sticky="e")
        self.usage_label = ttk.Label(
            header_actions,
            textvariable=self.usage_var,
            style="Subtitle.TLabel",
        )
        self.usage_label.pack(side="left", padx=(0, ui.SPACE_3))
        self.status_label = ttk.Label(
            header_actions,
            textvariable=self.status_var,
            style=ui.status_style_for("idle"),
        )
        self.status_label.pack(side="left", padx=(0, ui.SPACE_2))
        self.settings_button = ttk.Button(
            header_actions,
            text="设置",
            command=self._open_settings,
            cursor="hand2",
        )
        self.settings_button.pack(side="left")

        self.audio_section = ttk.Frame(
            outer,
            style="Card.TFrame",
            padding=(ui.SPACE_3, ui.SPACE_2),
        )
        self.audio_section.grid(row=1, column=0, sticky="ew", pady=(0, ui.SPACE_3))
        self.audio_section.columnconfigure(0, weight=1)
        audio_header = ttk.Frame(self.audio_section, style="Surface.TFrame")
        audio_header.grid(row=0, column=0, sticky="ew")
        audio_header.columnconfigure(0, weight=1)
        audio_title = ttk.Frame(audio_header, style="Surface.TFrame")
        audio_title.grid(row=0, column=0, sticky="w")
        ttk.Label(
            audio_title,
            text="音频路由",
            style="SurfaceTitle.TLabel",
        ).pack(side="left")
        ttk.Label(
            audio_title,
            text="选择会议输入、回放捕获和英文输出",
            style="Helper.TLabel",
        ).pack(side="left", padx=(ui.SPACE_2, 0))
        self.refresh_button = ttk.Button(
            audio_header,
            text="刷新设备",
            command=self._refresh_devices,
        )
        self.refresh_button.grid(row=0, column=1, padx=(ui.SPACE_2, 0))
        self.audio_toggle_button = ttk.Button(
            audio_header,
            textvariable=self.audio_toggle_var,
            command=self._toggle_audio_panel,
        )
        self.audio_toggle_button.grid(row=0, column=2, padx=(ui.SPACE_2, 0))

        self.audio_content = ttk.Frame(self.audio_section, style="Surface.TFrame")
        self.audio_content.grid(row=1, column=0, sticky="ew", pady=(ui.SPACE_3, 0))
        self.audio_content.columnconfigure(0, weight=1)
        self.audio_content.columnconfigure(1, weight=1)
        self.audio_content.columnconfigure(2, weight=1)
        self.audio_content.columnconfigure(3, weight=1)

        self.microphone_combo = ttk.Combobox(
            self.audio_content,
            textvariable=self.microphone_var,
            state="readonly",
        )
        self.loopback_combo = ttk.Combobox(
            self.audio_content,
            textvariable=self.loopback_var,
            state="readonly",
        )
        self.output_combo = ttk.Combobox(
            self.audio_content,
            textvariable=self.output_var,
            state="readonly",
        )
        self.voice_combo = ttk.Combobox(
            self.audio_content,
            textvariable=self.voice_var,
            state="readonly",
            values=tuple(label for label, _ in self.VOICE_CHOICES),
        )
        self.audio_fields = (
            (
                ttk.Label(
                    self.audio_content,
                    text="你的实体麦克风",
                    style="Surface.TLabel",
                ),
                self.microphone_combo,
            ),
            (
                ttk.Label(
                    self.audio_content,
                    text="Teams 扬声器回放捕获",
                    style="Surface.TLabel",
                ),
                self.loopback_combo,
            ),
            (
                ttk.Label(
                    self.audio_content,
                    text="英文输出（CABLE Input）",
                    style="Surface.TLabel",
                ),
                self.output_combo,
            ),
            (
                ttk.Label(
                    self.audio_content,
                    text="英文声音",
                    style="Surface.TLabel",
                ),
                self.voice_combo,
            ),
        )
        self._sync_voice_choices()

        controls = ttk.Frame(outer, style="Background.TFrame")
        controls.grid(row=2, column=0, sticky="ew", pady=(0, ui.SPACE_2))
        controls.columnconfigure(1, weight=1)
        primary_actions = ttk.Frame(controls, style="Background.TFrame")
        primary_actions.grid(row=0, column=0, sticky="w")
        self.start_button = ttk.Button(
            primary_actions,
            text="开始同传",
            style="Primary.TButton",
            command=self._start,
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            primary_actions,
            text="停止",
            style="Danger.TButton",
            command=self._stop,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(ui.SPACE_2, 0))
        self.test_button = ttk.Button(
            primary_actions,
            text="测试输出声道",
            command=self._test_output,
            state="disabled",
        )
        self.test_button.pack(side="left", padx=(ui.SPACE_2, 0))

        preferences = ttk.Frame(controls, style="Background.TFrame")
        preferences.grid(row=0, column=2, sticky="e")
        self.silence_gate_check = ttk.Checkbutton(
            preferences,
            text="省流量静音门控",
            variable=self.silence_gate_var,
        )
        self.silence_gate_check.pack(side="left")
        self._sync_voice_choices()
        ttk.Checkbutton(
            preferences,
            text="窗口置顶",
            variable=self.always_on_top_var,
            command=self._toggle_topmost,
        ).pack(side="left", padx=(ui.SPACE_3, 0))

        self.operation_progress = ttk.Progressbar(
            outer,
            mode="indeterminate",
            style="App.Horizontal.TProgressbar",
        )
        self.operation_progress.grid(row=3, column=0, sticky="ew")
        self.operation_progress.grid_remove()

        self.panes = ttk.Frame(outer, style="Background.TFrame")
        self.panes.grid(row=4, column=0, sticky="nsew", pady=(ui.SPACE_1, 0))
        self.incoming_card, self.incoming_interim_label, self.incoming_history = (
            self._build_transcript_card(
                self.panes,
                role="对方",
                title="英文 → 中文字幕",
                interim_var=self.incoming_interim_var,
                role_style="Incoming.Role.TLabel",
            )
        )
        self.outgoing_card, self.outgoing_interim_label, self.outgoing_history = (
            self._build_transcript_card(
                self.panes,
                role="我",
                title="中文 → 英文语音",
                interim_var=self.outgoing_interim_var,
                role_style="Outgoing.Role.TLabel",
            )
        )

        footer = ttk.Frame(outer, style="Background.TFrame")
        footer.grid(row=5, column=0, sticky="ew", pady=(ui.SPACE_3, 0))
        footer.columnconfigure(0, weight=1)
        self.privacy_label = ttk.Label(
            footer,
            text="隐私提示：音频和字幕会发送到你选择的供应商；API Key 保存到 Windows 凭据管理器。",
            style="Subtitle.TLabel",
            wraplength=650,
        )
        self.privacy_label.grid(row=0, column=0, sticky="w")
        footer_actions = ttk.Frame(footer, style="Background.TFrame")
        footer_actions.grid(row=0, column=1, sticky="e")
        self.clear_history_button = ttk.Button(
            footer_actions,
            text="清空记录",
            style="Danger.TButton",
            command=self._clear_history,
        )
        self.clear_history_button.pack(side="right")
        self.minutes_button = ttk.Button(
            footer_actions,
            text="生成 AI 会议纪要",
            command=self._generate_minutes,
            state="disabled",
        )
        self.minutes_button.pack(side="right", padx=(0, ui.SPACE_2))

        self._reset_history(self.incoming_history)
        self._reset_history(self.outgoing_history)
        self._set_status("准备就绪", "idle")
        self.root.after_idle(self._apply_responsive_layout)

    def _build_transcript_card(
        self,
        parent: ttk.Frame,
        *,
        role: str,
        title: str,
        interim_var: tk.StringVar,
        role_style: str,
    ) -> tuple[ttk.Frame, ttk.Label, tk.Text]:
        card = ttk.Frame(
            parent,
            style="Card.TFrame",
            padding=ui.SPACE_4,
        )
        card_header = ttk.Frame(card, style="Surface.TFrame")
        card_header.pack(fill="x")
        ttk.Label(card_header, text=role, style=role_style).pack(side="left")
        ttk.Label(
            card_header,
            text=title,
            style="CardTitle.TLabel",
        ).pack(side="left", padx=(ui.SPACE_2, 0))
        interim = ttk.Label(
            card,
            textvariable=interim_var,
            style="Interim.TLabel",
            wraplength=480,
            justify="left",
        )
        interim.pack(fill="x", anchor="w", pady=(ui.SPACE_3, ui.SPACE_3))
        history = self._make_history(card)
        return card, interim, history

    @staticmethod
    def _reset_history(history: tk.Text) -> None:
        history.configure(state="normal")
        history.delete("1.0", "end")
        history.insert("1.0", "最终译文将在这里持续记录。", "placeholder")
        history.configure(state="disabled")

    def _toggle_audio_panel(self) -> None:
        self._set_audio_panel_expanded(not self._audio_panel_expanded)

    def _set_audio_panel_expanded(self, expanded: bool) -> None:
        self._audio_panel_expanded = expanded
        if expanded:
            self.audio_content.grid()
            self.audio_toggle_var.set("收起音频路由")
        else:
            self.audio_content.grid_remove()
            self.audio_toggle_var.set("展开音频路由")

    def _on_refresh_shortcut(self, _event: object = None) -> None:
        if self._state == "idle":
            self._refresh_devices()

    def _on_root_configure(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._apply_responsive_layout(event.width)

    def _apply_responsive_layout(self, width: int | None = None) -> None:
        if not hasattr(self, "panes"):
            return
        current_width = width or self.root.winfo_width()
        mode = "wide" if current_width >= 920 else "compact"
        if mode != self._responsive_mode:
            self._responsive_mode = mode
            for widget in (self.incoming_card, self.outgoing_card):
                widget.grid_forget()
            for row in (0, 1):
                self.panes.rowconfigure(row, weight=0)
            for column in (0, 1):
                self.panes.columnconfigure(column, weight=0)
            if mode == "wide":
                for card in (self.incoming_card, self.outgoing_card):
                    card.configure(padding=ui.SPACE_4)
                for interim in (
                    self.incoming_interim_label,
                    self.outgoing_interim_label,
                ):
                    interim.pack_configure(
                        pady=(ui.SPACE_3, ui.SPACE_3)
                    )
                self.panes.columnconfigure(0, weight=1)
                self.panes.columnconfigure(1, weight=1)
                self.panes.rowconfigure(0, weight=1)
                self.incoming_card.grid(
                    row=0,
                    column=0,
                    sticky="nsew",
                    padx=(0, ui.SPACE_2),
                )
                self.outgoing_card.grid(
                    row=0,
                    column=1,
                    sticky="nsew",
                    padx=(ui.SPACE_2, 0),
                )
            else:
                for card in (self.incoming_card, self.outgoing_card):
                    card.configure(padding=(ui.SPACE_4, ui.SPACE_2))
                for interim in (
                    self.incoming_interim_label,
                    self.outgoing_interim_label,
                ):
                    interim.pack_configure(
                        pady=(ui.SPACE_2, ui.SPACE_2)
                    )
                self.panes.columnconfigure(0, weight=1)
                self.panes.rowconfigure(0, weight=1)
                self.panes.rowconfigure(1, weight=1)
                self.incoming_card.grid(
                    row=0,
                    column=0,
                    sticky="nsew",
                    pady=(0, ui.SPACE_2),
                )
                self.outgoing_card.grid(
                    row=1,
                    column=0,
                    sticky="nsew",
                    pady=(ui.SPACE_2, 0),
                )

        audio_mode = "four" if current_width >= 820 else "two"
        if audio_mode != self._audio_layout_mode:
            self._audio_layout_mode = audio_mode
            self._layout_audio_fields(audio_mode == "four")

        wrap = max(300, (current_width - 96) // 2) if mode == "wide" else max(320, current_width - 80)
        self.incoming_interim_label.configure(wraplength=wrap)
        self.outgoing_interim_label.configure(wraplength=wrap)
        self.privacy_label.configure(wraplength=max(280, current_width - 430))

    def _layout_audio_fields(self, wide: bool) -> None:
        for column in range(4):
            self.audio_content.columnconfigure(column, weight=0)
        for label, field in self.audio_fields:
            label.grid_forget()
            field.grid_forget()
        columns = 4 if wide else 2
        for column in range(columns):
            self.audio_content.columnconfigure(column, weight=1)
        for index, (label, field) in enumerate(self.audio_fields):
            column = index if wide else index % 2
            row = 0 if wide else (index // 2) * 3
            left_pad = 0 if column == 0 else ui.SPACE_3
            label.grid(
                row=row,
                column=column,
                sticky="w",
                padx=(left_pad, 0),
            )
            field.grid(
                row=row + 1,
                column=column,
                sticky="ew",
                padx=(left_pad, 0),
                pady=(ui.SPACE_1, ui.SPACE_2 if not wide else 0),
            )

    def _set_status(
        self,
        text: str,
        kind: str = "idle",
        *,
        busy: bool | None = None,
    ) -> None:
        self.status_var.set(text)
        if hasattr(self, "status_label"):
            self.status_label.configure(style=ui.status_style_for(kind))
        if busy is not None:
            self._set_busy(busy)

    def _set_busy(self, active: bool) -> None:
        if not hasattr(self, "operation_progress") or self._busy == active:
            return
        self._busy = active
        if active:
            self._busy_after_id = self.root.after(300, self._show_busy_progress)
        else:
            if self._busy_after_id:
                self.root.after_cancel(self._busy_after_id)
                self._busy_after_id = None
            self.operation_progress.stop()
            self.operation_progress.grid_remove()

    def _show_busy_progress(self) -> None:
        self._busy_after_id = None
        if self._busy and self.root.winfo_exists():
            self.operation_progress.grid()
            self.operation_progress.start(12)

    def _settings_variables(self) -> dict[str, tk.StringVar]:
        return {
            "interpreter_provider": self.interpreter_provider_var,
            "interpreter_model": self.interpreter_model_var,
            "interpreter_api_key": self.api_key_var,
            "workspace_id": self.workspace_id_var,
            "interpreter_websocket_url": self.interpreter_websocket_url_var,
            "voice": self.voice_var,
            "minutes_provider": self.minutes_provider_var,
            "minutes_model": self.minutes_model_var,
            "minutes_api_key": self.minutes_api_key_var,
            "minutes_api_url": self.minutes_api_url_var,
            "minutes_extra_body": self.minutes_extra_body_var,
        }

    def _open_settings(self) -> None:
        if self._state != "idle":
            return
        if self._settings_window and self._settings_window.winfo_exists():
            self._settings_window.lift()
            self._settings_window.focus_force()
            return

        self._settings_snapshot = {
            name: variable.get()
            for name, variable in self._settings_variables().items()
        }
        window = tk.Toplevel(self.root)
        self._settings_window = window
        window.title("模型供应商设置")
        window.geometry("860x650")
        window.minsize(720, 560)
        window.configure(bg=ui.BACKGROUND)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._cancel_settings)
        window.bind("<Escape>", lambda _event: self._cancel_settings())
        window.bind("<Return>", lambda _event: self._save_and_close_settings())

        content = ttk.Frame(
            window,
            style="Background.TFrame",
            padding=ui.SPACE_4,
        )
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(content)
        notebook.grid(row=0, column=0, sticky="nsew")
        self.settings_notebook = notebook
        self.interpreter_settings_tab = ttk.Frame(
            notebook,
            style="Surface.TFrame",
            padding=ui.SPACE_4,
        )
        self.minutes_settings_tab = ttk.Frame(
            notebook,
            style="Surface.TFrame",
            padding=ui.SPACE_4,
        )
        notebook.add(self.interpreter_settings_tab, text="同声翻译")
        notebook.add(self.minutes_settings_tab, text="AI 会议纪要")
        self._build_interpreter_settings_tab(self.interpreter_settings_tab)
        self._build_minutes_settings_tab(self.minutes_settings_tab)

        self.interpreter_provider_combo.bind(
            "<<ComboboxSelected>>", self._on_interpreter_provider_changed
        )
        self.minutes_provider_combo.bind(
            "<<ComboboxSelected>>", self._on_minutes_provider_changed
        )

        self._settings_error_var = tk.StringVar()
        self.settings_error_label = ttk.Label(
            content,
            textvariable=self._settings_error_var,
            style="ErrorBanner.TLabel",
            wraplength=780,
        )
        self.settings_error_label.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(ui.SPACE_3, 0),
        )
        self.settings_error_label.grid_remove()

        actions = ttk.Frame(content, style="Background.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(ui.SPACE_3, 0))
        ttk.Button(
            actions,
            text="清除凭据",
            style="Danger.TButton",
            command=self._clear_credentials,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="取消",
            command=self._cancel_settings,
        ).pack(side="right")
        ttk.Button(
            actions,
            text="保存配置",
            style="Primary.TButton",
            command=self._save_and_close_settings,
        ).pack(side="right", padx=(0, 8))

        self._settings_focus_targets = {
            "interpreter_model": self.interpreter_model_combo,
            "interpreter_api_key": self.api_key_entry,
            "workspace": self.workspace_id_entry,
            "websocket": self.interpreter_websocket_url_entry,
            "minutes_model": self.minutes_model_combo,
            "minutes_api_key": self.minutes_api_key_entry,
            "minutes_url": self.minutes_api_url_entry,
            "minutes_extra": self.minutes_extra_body_entry,
        }
        self._apply_provider_controls()
        self._toggle_secret_visibility()
        window.grab_set()
        window.focus_force()

    def _build_interpreter_settings_tab(self, tab: ttk.Frame) -> None:
        for column in range(2):
            tab.columnconfigure(column, weight=1)
        ttk.Label(
            tab,
            text="配置实时语音识别、翻译和英文语音输出服务。",
            style="Helper.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, ui.SPACE_4))
        ttk.Label(tab, text="供应商 / 协议", style="Surface.TLabel").grid(
            row=1, column=0, sticky="w"
        )
        self.interpreter_model_label = ttk.Label(
            tab,
            text="实时模型",
            style="Surface.TLabel",
        )
        self.interpreter_model_label.grid(
            row=1,
            column=1,
            sticky="w",
            padx=(ui.SPACE_3, 0),
        )
        self.interpreter_provider_combo = ttk.Combobox(
            tab,
            textvariable=self.interpreter_provider_var,
            state="readonly",
            values=tuple(item.label for item in INTERPRETER_PROVIDERS),
        )
        self.interpreter_provider_combo.grid(row=2, column=0, sticky="ew")
        self.interpreter_model_combo = ttk.Combobox(
            tab,
            textvariable=self.interpreter_model_var,
            values=("qwen3.5-livetranslate-flash-realtime",),
        )
        self.interpreter_model_combo.grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(ui.SPACE_3, 0),
        )
        ttk.Label(
            tab,
            text="自定义兼容接口可填写其实际支持的模型名。",
            style="Helper.TLabel",
        ).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(ui.SPACE_1, ui.SPACE_4),
        )

        self.interpreter_api_key_label = ttk.Label(
            tab,
            text="API Key",
            style="Surface.TLabel",
        )
        self.interpreter_api_key_label.grid(row=4, column=0, sticky="w")
        self.interpreter_identity_label = ttk.Label(
            tab,
            text="WorkspaceId（百炼）",
            style="Surface.TLabel",
        )
        self.interpreter_identity_label.grid(
            row=4,
            column=1,
            sticky="w",
            padx=(ui.SPACE_3, 0),
        )

        api_key_field = ttk.Frame(tab, style="Surface.TFrame")
        api_key_field.grid(row=5, column=0, sticky="ew")
        api_key_field.columnconfigure(0, weight=1)
        self.api_key_entry = ttk.Entry(
            api_key_field,
            textvariable=self.api_key_var,
            show="●",
        )
        self.api_key_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            api_key_field,
            text="显示",
            variable=self.show_api_key_var,
            command=self._toggle_secret_visibility,
            style="Surface.TCheckbutton",
        ).grid(row=0, column=1, padx=(ui.SPACE_2, 0))
        self.workspace_id_entry = ttk.Entry(
            tab,
            textvariable=self.workspace_id_var,
        )
        self.workspace_id_entry.grid(
            row=5,
            column=1,
            sticky="ew",
            padx=(ui.SPACE_3, 0),
        )
        ttk.Label(
            tab,
            text="凭据仅保存到当前 Windows 用户的凭据管理器。",
            style="Helper.TLabel",
        ).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(ui.SPACE_1, ui.SPACE_4),
        )

        self.interpreter_websocket_url_label = ttk.Label(
            tab,
            text="WebSocket 地址（自定义 LiveTranslate 兼容接口）",
            style="Surface.TLabel",
        )
        self.interpreter_websocket_url_label.grid(
            row=7,
            column=0,
            columnspan=2,
            sticky="w",
        )
        self.interpreter_websocket_url_entry = ttk.Entry(
            tab,
            textvariable=self.interpreter_websocket_url_var,
        )
        self.interpreter_websocket_url_entry.grid(
            row=8,
            column=0,
            columnspan=2,
            sticky="ew",
        )
        self.interpreter_websocket_url_helper = ttk.Label(
            tab,
            text="请输入供应商提供的 ws:// 或 wss:// 实时翻译端点。",
            style="Helper.TLabel",
        )
        self.interpreter_websocket_url_helper.grid(
            row=9,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(ui.SPACE_1, 0),
        )
        self.websocket_widgets = (
            self.interpreter_websocket_url_label,
            self.interpreter_websocket_url_entry,
            self.interpreter_websocket_url_helper,
        )

    def _build_minutes_settings_tab(self, tab: ttk.Frame) -> None:
        for column in range(2):
            tab.columnconfigure(column, weight=1)
        ttk.Label(
            tab,
            text="使用兼容 Chat Completions 的 LLM 整理最终字幕。",
            style="Helper.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, ui.SPACE_4))
        ttk.Label(tab, text="LLM 供应商", style="Surface.TLabel").grid(
            row=1, column=0, sticky="w"
        )
        ttk.Label(tab, text="模型名称", style="Surface.TLabel").grid(
            row=1,
            column=1,
            sticky="w",
            padx=(ui.SPACE_3, 0),
        )
        self.minutes_provider_combo = ttk.Combobox(
            tab,
            textvariable=self.minutes_provider_var,
            state="readonly",
            values=tuple(item.label for item in LLM_PROVIDERS),
        )
        self.minutes_provider_combo.grid(row=2, column=0, sticky="ew")
        self.minutes_model_combo = ttk.Combobox(
            tab,
            textvariable=self.minutes_model_var,
            values=self.MINUTES_MODEL_CHOICES,
        )
        self.minutes_model_combo.grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(ui.SPACE_3, 0),
        )
        ttk.Label(
            tab,
            text="百炼可直接选择预设模型；其他供应商请填写其模型 ID。",
            style="Helper.TLabel",
        ).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(ui.SPACE_1, ui.SPACE_4),
        )

        ttk.Label(
            tab,
            text="API Key（百炼可复用同传；Ollama 可空）",
            style="Surface.TLabel",
        ).grid(row=4, column=0, sticky="w")
        ttk.Label(
            tab,
            text="Chat Completions 地址",
            style="Surface.TLabel",
        ).grid(
            row=4,
            column=1,
            sticky="w",
            padx=(ui.SPACE_3, 0),
        )

        key_field = ttk.Frame(tab, style="Surface.TFrame")
        key_field.grid(row=5, column=0, sticky="ew")
        key_field.columnconfigure(0, weight=1)
        self.minutes_api_key_entry = ttk.Entry(
            key_field,
            textvariable=self.minutes_api_key_var,
            show="●",
        )
        self.minutes_api_key_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            key_field,
            text="显示",
            variable=self.show_minutes_api_key_var,
            command=self._toggle_secret_visibility,
            style="Surface.TCheckbutton",
        ).grid(row=0, column=1, padx=(ui.SPACE_2, 0))
        self.minutes_api_url_entry = ttk.Entry(
            tab,
            textvariable=self.minutes_api_url_var,
        )
        self.minutes_api_url_entry.grid(
            row=5,
            column=1,
            sticky="ew",
            padx=(ui.SPACE_3, 0),
        )
        ttk.Label(
            tab,
            text="地址可填服务根路径或完整 /chat/completions 地址。",
            style="Helper.TLabel",
        ).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(ui.SPACE_1, ui.SPACE_4),
        )

        ttk.Label(
            tab,
            text="附加请求参数 JSON",
            style="Surface.TLabel",
        ).grid(row=7, column=0, columnspan=2, sticky="w")
        self.minutes_extra_body_entry = ttk.Entry(
            tab,
            textvariable=self.minutes_extra_body_var,
        )
        self.minutes_extra_body_entry.grid(
            row=8,
            column=0,
            columnspan=2,
            sticky="ew",
        )
        ttk.Label(
            tab,
            text='可选，例如 {"temperature": 0.2}；必须是 JSON 对象。',
            style="Helper.TLabel",
        ).grid(
            row=9,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(ui.SPACE_1, 0),
        )

    def _close_settings(self) -> None:
        window = self._settings_window
        self._settings_window = None
        self._settings_snapshot = None
        self._settings_error_var = None
        self._settings_focus_targets = {}
        if window and window.winfo_exists():
            try:
                window.grab_release()
            except tk.TclError:
                pass
            window.destroy()

    def _cancel_settings(self) -> None:
        if self._settings_snapshot is not None:
            variables = self._settings_variables()
            for name, value in self._settings_snapshot.items():
                variables[name].set(value)
        self._sync_voice_choices()
        self._close_settings()

    def _save_and_close_settings(self) -> None:
        if self._save_credentials():
            self._close_settings()

    def _toggle_secret_visibility(self) -> None:
        if hasattr(self, "api_key_entry"):
            self.api_key_entry.configure(
                show="" if self.show_api_key_var.get() else "●"
            )
        if hasattr(self, "minutes_api_key_entry"):
            self.minutes_api_key_entry.configure(
                show="" if self.show_minutes_api_key_var.get() else "●"
            )

    def _show_settings_error(self, message: str) -> None:
        if not self._settings_error_var or not self._settings_window:
            messagebox.showerror("保存失败", message)
            return
        self._settings_error_var.set(message)
        self.settings_error_label.grid()
        lowered = message.casefold()
        target = "interpreter_model"
        tab = self.interpreter_settings_tab
        if "websocket" in lowered or "ws://" in lowered or "wss://" in lowered:
            target = "websocket"
        elif "workspace" in lowered:
            target = "workspace"
        elif "同传 api key" in lowered:
            target = "interpreter_api_key"
        elif "同传模型" in lowered:
            target = "interpreter_model"
        elif "附加" in message or "json" in lowered:
            target = "minutes_extra"
            tab = self.minutes_settings_tab
        elif "chat completions" in lowered:
            target = "minutes_url"
            tab = self.minutes_settings_tab
        elif "会议纪要模型" in message:
            target = "minutes_model"
            tab = self.minutes_settings_tab
        elif "会议纪要" in message or "独立的 API Key" in message:
            target = "minutes_api_key"
            tab = self.minutes_settings_tab
        self.settings_notebook.select(tab)
        widget = self._settings_focus_targets.get(target)
        if widget:
            widget.state(["invalid"])
            self._settings_window.after_idle(widget.focus_set)

    def _clear_settings_error(self) -> None:
        if self._settings_error_var:
            self._settings_error_var.set("")
        if hasattr(self, "settings_error_label"):
            self.settings_error_label.grid_remove()
        for widget in self._settings_focus_targets.values():
            try:
                widget.state(["!invalid"])
            except tk.TclError:
                pass

    def _make_history(self, parent: ttk.Frame) -> tk.Text:
        history = tk.Text(
            parent,
            wrap="word",
            width=1,
            height=1,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=ui.BORDER,
            highlightcolor=ui.FOCUS,
            bg=ui.TEXT_SURFACE,
            fg=ui.TEXT_PRIMARY,
            insertbackground=ui.TEXT_PRIMARY,
            selectbackground=ui.SELECTION,
            selectforeground="#FFFFFF",
            font=(self._fonts.transcript, 11),
            padx=12,
            pady=10,
            state="disabled",
        )
        history.pack(fill="both", expand=True)
        history.tag_configure("source", foreground=ui.TEXT_MUTED, spacing1=8)
        history.tag_configure(
            "translation",
            foreground=ui.TEXT_PRIMARY,
            spacing3=12,
            font=(self._fonts.transcript, 12, "bold"),
        )
        history.tag_configure(
            "placeholder",
            foreground=ui.TEXT_MUTED,
            font=(self._fonts.transcript, 10, "italic"),
        )
        return history

    def _refresh_devices(self, show_errors: bool = True) -> None:
        try:
            self._catalog.refresh()
            self._set_combo(
                "microphone", self.microphone_combo, self.microphone_var,
                self._catalog.inputs, self._catalog.default_input_label,
            )
            self._set_combo(
                "loopback", self.loopback_combo, self.loopback_var,
                self._catalog.loopbacks, self._catalog.default_loopback_label,
            )
            self._set_combo(
                "output", self.output_combo, self.output_var,
                self._catalog.outputs, self._catalog.default_output_label,
            )
            if show_errors:
                self._set_status("音频设备已刷新", "info")
        except Exception as exc:
            self._set_status("无法读取音频设备", "error")
            if show_errors:
                messagebox.showerror("音频设备错误", str(exc))

    def _set_combo(
        self,
        name: str,
        combo: ttk.Combobox,
        variable: tk.StringVar,
        choices: list[AudioDeviceChoice],
        preferred: str,
    ) -> None:
        mapping = {choice.label: choice for choice in choices}
        self._choice_maps[name] = mapping
        combo.configure(values=tuple(mapping))
        if preferred in mapping:
            variable.set(preferred)
        elif mapping:
            variable.set(next(iter(mapping)))
        else:
            variable.set("")

    def _selected_device(self, group: str, label: str) -> Any:
        choice = self._choice_maps.get(group, {}).get(label)
        if choice is None:
            raise ValueError(f"没有选择有效的{group}设备")
        return choice.device

    def _interpreter_provider_id(self) -> str:
        preset = INTERPRETER_BY_LABEL.get(self.interpreter_provider_var.get())
        if preset is None:
            raise ValueError("请选择有效的同传供应商")
        return preset.provider_id

    def _minutes_provider_id(self) -> str:
        preset = LLM_BY_LABEL.get(self.minutes_provider_var.get())
        if preset is None:
            raise ValueError("请选择有效的会议纪要 LLM 供应商")
        return preset.provider_id

    def _current_settings(self) -> AppSettings:
        extra_body = self.minutes_extra_body_var.get().strip() or "{}"
        parse_extra_body(extra_body)
        return AppSettings(
            interpreter_provider=self._interpreter_provider_id(),
            interpreter_model=normalize_realtime_model_name(
                self.interpreter_model_var.get()
            ),
            interpreter_websocket_url=self.interpreter_websocket_url_var.get().strip(),
            meeting_minutes_provider=self._minutes_provider_id(),
            meeting_minutes_model=normalize_model_name(self.minutes_model_var.get()),
            meeting_minutes_api_url=self.minutes_api_url_var.get().strip(),
            meeting_minutes_extra_body=extra_body,
        )

    def _validate_interpreter_settings(self, settings: AppSettings) -> str:
        websocket_url = (
            settings.interpreter_websocket_url
            if settings.interpreter_provider == INTERPRETER_QWEN_COMPATIBLE
            else ""
        )
        if settings.interpreter_provider == INTERPRETER_QWEN_COMPATIBLE and not websocket_url:
            raise ValueError("自定义同传供应商必须填写 WebSocket 地址")
        return build_api_url(
            self.workspace_id_var.get(),
            settings.interpreter_model,
            websocket_url,
        )

    def _apply_provider_controls(self) -> None:
        if not self._settings_window or not self._settings_window.winfo_exists():
            return
        running = self._state != "idle"
        interpreter_id = self._interpreter_provider_id()
        minutes_id = self._minutes_provider_id()
        self.workspace_id_entry.configure(
            state="disabled"
            if running or interpreter_id == INTERPRETER_QWEN_COMPATIBLE
            else "normal"
        )
        self.interpreter_websocket_url_entry.configure(
            state="normal"
            if not running and interpreter_id == INTERPRETER_QWEN_COMPATIBLE
            else "disabled"
        )
        for widget in self.websocket_widgets:
            if interpreter_id == INTERPRETER_QWEN_COMPATIBLE:
                widget.grid()
            else:
                widget.grid_remove()
        self.minutes_api_url_entry.configure(
            state="disabled"
            if running or minutes_id == LLM_QWEN_WORKSPACE
            else "normal"
        )
        self.minutes_model_combo.configure(
            values=self.MINUTES_MODEL_CHOICES
            if minutes_id == LLM_QWEN_WORKSPACE
            else ()
        )

    def _on_interpreter_provider_changed(self, _event: object = None) -> None:
        self._clear_settings_error()
        self._apply_provider_controls()

    def _sync_voice_choices(self) -> None:
        labels = tuple(label for label, _ in self.VOICE_CHOICES)
        self.voice_combo.configure(values=labels)
        if self.voice_var.get() not in labels:
            self.voice_var.set(labels[0])
        if hasattr(self, "silence_gate_check"):
            self.silence_gate_check.configure(
                text="省流量静音门控",
                state="disabled" if self._state != "idle" else "normal",
            )

    def _on_minutes_provider_changed(self, _event: object = None) -> None:
        self._clear_settings_error()
        preset = LLM_BY_LABEL.get(self.minutes_provider_var.get())
        if preset and preset.api_url:
            self.minutes_api_url_var.set(preset.api_url)
        if preset and preset.provider_id == LLM_QWEN_WORKSPACE:
            if not self.minutes_model_var.get().strip():
                self.minutes_model_var.set(DEFAULT_MINUTES_MODEL)
        elif self.minutes_model_var.get().strip() in self.MINUTES_MODEL_CHOICES:
            self.minutes_model_var.set("")
        self._apply_provider_controls()
        if preset and preset.provider_id != LLM_QWEN_WORKSPACE:
            self._set_status("请填写该供应商实际开放的会议纪要模型名", "info")

    def _resolved_minutes_api_key(self, provider_id: str) -> str:
        configured = self.minutes_api_key_var.get().strip()
        if configured:
            return configured
        if provider_id == LLM_QWEN_WORKSPACE:
            key = self.api_key_var.get().strip()
            if not key:
                raise ValueError("百炼会议纪要需要单独填写 API Key，或先配置百炼同传 Key")
            return key
        if provider_id == "ollama":
            return ""
        raise ValueError("该会议纪要供应商需要填写独立的 API Key")

    def _save_credentials(self) -> bool:
        workspace_id = self.workspace_id_var.get().strip()
        self._clear_settings_error()
        try:
            settings = self._current_settings()
            self._validate_interpreter_settings(settings)
            minutes_url = resolve_minutes_url(
                settings.meeting_minutes_provider,
                workspace_id,
                settings.meeting_minutes_api_url,
            )
            minutes_key = self._resolved_minutes_api_key(
                settings.meeting_minutes_provider
            )
            extra_body = parse_extra_body(settings.meeting_minutes_extra_body)
            if settings.meeting_minutes_provider == LLM_QWEN_WORKSPACE:
                extra_body.setdefault("enable_thinking", False)
            OpenAICompatibleMeetingMinutesClient(
                minutes_key,
                minutes_url,
                settings.meeting_minutes_model,
                provider_name=LLM_BY_ID[settings.meeting_minutes_provider].label,
                extra_body=extra_body,
            )
            save_credentials(self.api_key_var.get(), workspace_id)
            if self.minutes_api_key_var.get().strip():
                save_minutes_api_key(self.minutes_api_key_var.get())
            else:
                clear_minutes_api_key()
            save_settings(settings)
        except (OSError, ValueError) as exc:
            self._show_settings_error(str(exc))
            return False
        self.interpreter_model_var.set(settings.interpreter_model)
        self.minutes_model_var.set(settings.meeting_minutes_model)
        self.minutes_extra_body_var.set(settings.meeting_minutes_extra_body)
        self._set_status("配置已保存", "info")
        return True

    def _clear_credentials(self) -> None:
        if not messagebox.askyesno(
            "清除凭据",
            "确定从 Windows 凭据管理器中删除百炼和会议纪要凭据吗？",
        ):
            return
        try:
            clear_credentials()
        except OSError as exc:
            messagebox.showerror("清除失败", str(exc))
            return
        self.api_key_var.set("")
        self.minutes_api_key_var.set("")
        self.workspace_id_var.set("")
        if self._settings_snapshot is not None:
            self._settings_snapshot["interpreter_api_key"] = ""
            self._settings_snapshot["minutes_api_key"] = ""
            self._settings_snapshot["workspace_id"] = ""
        self._set_status("凭据已清除", "warning")

    def _start(self) -> None:
        if self._state != "idle":
            return
        workspace_id = self.workspace_id_var.get().strip()
        try:
            interpreter_provider = self._interpreter_provider_id()
            interpreter_model = normalize_realtime_model_name(
                self.interpreter_model_var.get()
            )
            if not self.api_key_var.get().strip():
                raise ValueError("同传 API Key 不能为空")
            websocket_url = (
                self.interpreter_websocket_url_var.get().strip()
                if interpreter_provider == INTERPRETER_QWEN_COMPATIBLE
                else ""
            )
            if interpreter_provider == INTERPRETER_QWEN_COMPATIBLE and not websocket_url:
                raise ValueError("自定义同传供应商必须填写 WebSocket 地址")
            build_api_url(workspace_id, interpreter_model, websocket_url)
            microphone = self._selected_device("microphone", self.microphone_var.get())
            loopback = self._selected_device("loopback", self.loopback_var.get())
            output = self._selected_device("output", self.output_var.get())
        except ValueError as exc:
            messagebox.showwarning("同传配置未就绪", str(exc))
            return

        self._state = "starting"
        provider_label = INTERPRETER_BY_ID[interpreter_provider].label
        self._set_status(f"正在连接{provider_label}…", "busy", busy=True)
        self.usage_var.set("Token：0")
        self._set_controls_running(True, ready=False)
        session_args = {
            "microphone": microphone,
            "teams_loopback": loopback,
            "virtual_output": output,
            "english_voice": self.VOICE_API_NAMES[self.voice_var.get()],
            "on_incoming": lambda event: self._post("incoming", event),
            "on_outgoing": lambda event: self._post("outgoing", event),
            "on_usage": lambda usage: self._post("usage", usage),
            "on_error": lambda message: self._post("error", message),
            "use_silence_gate": self.silence_gate_var.get(),
        }
        self._session = QwenInterpreterSession(
            api_key=self.api_key_var.get(),
            workspace_id=workspace_id,
            model=interpreter_model,
            websocket_url=websocket_url,
            **session_args,
        )
        threading.Thread(target=self._start_worker, name="session-start", daemon=True).start()

    def _start_worker(self) -> None:
        try:
            assert self._session is not None
            self._session.start()
            self._post("started")
        except Exception as exc:
            self._post("start_failed", str(exc))

    def _stop(self) -> None:
        if self._state not in {"running", "starting"}:
            return
        self._state = "stopping"
        self._set_status("正在停止…", "busy", busy=True)
        self.stop_button.configure(state="disabled")
        self.test_button.configure(state="disabled")
        threading.Thread(target=self._stop_worker, name="session-stop", daemon=True).start()

    def _stop_worker(self) -> None:
        session = self._session
        if session:
            session.stop()
        self._post("stopped")

    def _test_output(self) -> None:
        if self._state == "running" and self._session:
            self._session.test_output()
            self._set_status("已向英文输出设备发送测试音", "info")

    def _generate_minutes(self) -> None:
        if self._state != "idle" or self._minutes_generating:
            return
        workspace_id = self.workspace_id_var.get().strip()
        if not self._meeting_turns:
            messagebox.showwarning("没有会议内容", "请先完成一段同传并产生最终字幕。")
            return
        try:
            provider_id = self._minutes_provider_id()
            api_key = self._resolved_minutes_api_key(provider_id)
            minutes_model = normalize_model_name(self.minutes_model_var.get())
            api_url = resolve_minutes_url(
                provider_id,
                workspace_id,
                self.minutes_api_url_var.get(),
            )
            extra_body = parse_extra_body(self.minutes_extra_body_var.get())
            if provider_id == LLM_QWEN_WORKSPACE:
                extra_body.setdefault("enable_thinking", False)
            settings = self._current_settings()
            save_settings(settings)
        except (OSError, ValueError) as exc:
            messagebox.showwarning("会议纪要配置无效", str(exc))
            return
        self.minutes_model_var.set(minutes_model)

        turns = tuple(self._meeting_turns)
        started_at = self._meeting_started_at or turns[0].recorded_at
        ended_at = self._meeting_ended_at or turns[-1].recorded_at
        self._minutes_generating = True
        self._set_status("正在生成 AI 会议纪要…", "busy", busy=True)
        self._update_minutes_button()
        self.clear_history_button.configure(state="disabled")
        threading.Thread(
            target=self._generate_minutes_worker,
            args=(
                api_key,
                api_url,
                minutes_model,
                LLM_BY_ID[provider_id].label,
                extra_body,
                turns,
                started_at,
                ended_at,
            ),
            name="meeting-minutes",
            daemon=True,
        ).start()

    def _generate_minutes_worker(
        self,
        api_key: str,
        api_url: str,
        minutes_model: str,
        provider_name: str,
        extra_body: dict[str, Any],
        turns: tuple[MeetingTurn, ...],
        started_at: datetime,
        ended_at: datetime,
    ) -> None:
        try:
            client = OpenAICompatibleMeetingMinutesClient(
                api_key,
                api_url,
                model=minutes_model,
                provider_name=provider_name,
                extra_body=extra_body,
            )
            result = client.generate(turns, started_at, ended_at)
            self._post("minutes_ready", result)
        except Exception as exc:
            self._post("minutes_failed", str(exc))

    def _post(self, kind: str, payload: Any = None) -> None:
        self._events.put(UiMessage(kind, payload))

    def _drain_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                self._handle_ui_message(event)
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(self.POLL_MS, self._drain_events)

    def _handle_ui_message(self, message: UiMessage) -> None:
        if message.kind == "started":
            self._state = "running"
            if self._meeting_started_at is None:
                self._meeting_started_at = datetime.now()
            self._meeting_ended_at = None
            self._set_status("同传运行中", "running", busy=False)
            self._set_audio_panel_expanded(False)
            self.stop_button.configure(state="normal")
            self.test_button.configure(state="normal")
        elif message.kind == "start_failed":
            self._state = "idle"
            self._session = None
            self._set_status("启动失败", "error", busy=False)
            self._set_controls_running(False)
            messagebox.showerror("启动失败", str(message.payload))
        elif message.kind == "stopped":
            self._state = "idle"
            self._session = None
            self._meeting_ended_at = datetime.now()
            self._set_status("已停止", "idle", busy=False)
            self._set_controls_running(False)
            self.incoming_interim_var.set("等待对方说英文…")
            self.outgoing_interim_var.set("等待你说中文…")
        elif message.kind == "incoming":
            self._record_turn("incoming", message.payload)
            self._show_translation(
                message.payload,
                self.incoming_interim_var,
                self.incoming_history,
            )
        elif message.kind == "outgoing":
            self._record_turn("outgoing", message.payload)
            self._show_translation(
                message.payload,
                self.outgoing_interim_var,
                self.outgoing_history,
            )
        elif message.kind == "error":
            self._set_status(str(message.payload), "error")
        elif message.kind == "usage":
            usage: UsageStats = message.payload
            self.usage_var.set(
                "Token："
                f"入音频 {usage.input_audio_tokens} / "
                f"出音频 {usage.output_audio_tokens} / "
                f"文本 {usage.input_text_tokens + usage.output_text_tokens}"
            )
        elif message.kind == "minutes_ready":
            result: MinutesResult = message.payload
            self._minutes_generating = False
            self._set_status(
                "会议纪要已生成："
                f"输入 {result.input_tokens} / 输出 {result.output_tokens} Token",
                "info",
                busy=False,
            )
            self.clear_history_button.configure(state="normal")
            self._update_minutes_button()
            self._show_minutes_window(result.markdown)
        elif message.kind == "minutes_failed":
            self._minutes_generating = False
            self._set_status("会议纪要生成失败", "error", busy=False)
            self.clear_history_button.configure(state="normal")
            self._update_minutes_button()
            messagebox.showerror("会议纪要生成失败", str(message.payload))

    def _record_turn(self, direction: str, event: TranslationEvent) -> None:
        if not event.is_final or not (event.source_text or event.translated_text):
            return
        recorded_at = datetime.now()
        if self._meeting_started_at is None:
            self._meeting_started_at = recorded_at
        self._meeting_turns.append(
            MeetingTurn(
                recorded_at=recorded_at,
                direction=direction,
                source_text=event.source_text,
                translated_text=event.translated_text,
            )
        )
        self._update_minutes_button()

    @staticmethod
    def _show_translation(
        event: TranslationEvent,
        interim_var: tk.StringVar,
        history: tk.Text,
    ) -> None:
        if not event.is_final:
            preview = event.translated_text or event.source_text
            if preview:
                interim_var.set(preview)
            return
        interim_var.set("正在聆听…")
        history.configure(state="normal")
        if history.tag_ranges("placeholder"):
            history.delete("1.0", "end")
        if event.source_text:
            history.insert("end", event.source_text + "\n", "source")
        if event.translated_text:
            history.insert("end", event.translated_text + "\n", "translation")
        history.see("end")
        history.configure(state="disabled")

    def _set_controls_running(self, running: bool, ready: bool = True) -> None:
        combo_state = "disabled" if running else "readonly"
        for combo in (
            self.microphone_combo,
            self.loopback_combo,
            self.output_combo,
            self.voice_combo,
        ):
            combo.configure(state=combo_state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.refresh_button.configure(state="disabled" if running else "normal")
        self.settings_button.configure(state="disabled" if running else "normal")
        self._sync_voice_choices()
        self.stop_button.configure(state="normal" if running and ready else "disabled")
        self.test_button.configure(state="normal" if running and ready else "disabled")
        self._update_minutes_button()

    def _update_minutes_button(self) -> None:
        enabled = (
            self._state == "idle"
            and bool(self._meeting_turns)
            and not self._minutes_generating
        )
        self.minutes_button.configure(
            text="正在生成会议纪要…" if self._minutes_generating else "生成 AI 会议纪要",
            state="normal" if enabled else "disabled",
        )

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.always_on_top_var.get())

    def _clear_history(self) -> None:
        if self._meeting_turns and not messagebox.askyesno(
            "清空会议记录",
            "清空后将无法基于当前字幕生成会议纪要，确定继续吗？",
        ):
            return
        for history in (self.incoming_history, self.outgoing_history):
            self._reset_history(history)
        self._meeting_turns.clear()
        self._meeting_started_at = None
        self._meeting_ended_at = None
        self._update_minutes_button()

    def _show_minutes_window(self, markdown: str) -> None:
        if self._minutes_window and self._minutes_window.winfo_exists():
            self._minutes_window.destroy()

        window = tk.Toplevel(self.root)
        self._minutes_window = window
        window.title("AI 会议纪要")
        window.geometry("900x720")
        window.minsize(680, 500)
        window.configure(bg=ui.BACKGROUND)
        window.transient(self.root)
        window.bind("<Escape>", lambda _event: window.destroy())

        outer = ttk.Frame(
            window,
            style="Background.TFrame",
            padding=ui.SPACE_4,
        )
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(
            outer,
            style="Card.TFrame",
            padding=ui.SPACE_4,
        )
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, ui.SPACE_3))
        title_group = ttk.Frame(toolbar, style="Surface.TFrame")
        title_group.pack(side="left", fill="x", expand=True)
        ttk.Label(
            title_group,
            text="AI 会议纪要",
            style="CardTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            title_group,
            text=self._meeting_time_summary(),
            style="Helper.TLabel",
        ).pack(anchor="w", pady=(ui.SPACE_1, 0))
        ttk.Button(
            toolbar,
            text="保存 Markdown",
            style="Primary.TButton",
            command=lambda: self._save_minutes(markdown),
        ).pack(side="right")
        ttk.Button(
            toolbar,
            text="复制",
            command=lambda: self._copy_minutes(markdown),
        ).pack(side="right", padx=(0, ui.SPACE_2))

        text_frame = ttk.Frame(outer, style="Card.TFrame", padding=1)
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        text = tk.Text(
            text_frame,
            wrap="word",
            width=1,
            height=1,
            relief="flat",
            borderwidth=0,
            bg=ui.TEXT_SURFACE,
            fg=ui.TEXT_PRIMARY,
            insertbackground=ui.TEXT_PRIMARY,
            selectbackground=ui.SELECTION,
            selectforeground="#FFFFFF",
            font=(self._fonts.transcript, 11),
            padx=ui.SPACE_4,
            pady=ui.SPACE_4,
        )
        scrollbar = ttk.Scrollbar(
            text_frame,
            orient="vertical",
            command=text.yview,
        )
        text.configure(yscrollcommand=scrollbar.set)
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._render_minutes_markdown(text, markdown)
        text.configure(state="disabled")

    def _meeting_time_summary(self) -> str:
        started_at = self._meeting_started_at
        ended_at = self._meeting_ended_at
        if not started_at:
            return "会议时间未记录"
        if not ended_at:
            return f"开始于 {started_at:%Y-%m-%d %H:%M}"
        seconds = max(0, int((ended_at - started_at).total_seconds()))
        minutes = max(1, (seconds + 59) // 60)
        return (
            f"{started_at:%Y-%m-%d %H:%M} 至 {ended_at:%H:%M}"
            f" · 约 {minutes} 分钟"
        )

    def _render_minutes_markdown(self, text: tk.Text, markdown: str) -> None:
        text.tag_configure(
            "h1",
            foreground=ui.TEXT_PRIMARY,
            font=(self._fonts.transcript, 18, "bold"),
            spacing1=6,
            spacing3=12,
        )
        text.tag_configure(
            "h2",
            foreground=ui.INFO,
            font=(self._fonts.transcript, 14, "bold"),
            spacing1=14,
            spacing3=7,
        )
        text.tag_configure(
            "h3",
            foreground=ui.TEXT_SECONDARY,
            font=(self._fonts.transcript, 12, "bold"),
            spacing1=10,
            spacing3=5,
        )
        text.tag_configure(
            "body",
            foreground=ui.TEXT_SECONDARY,
            spacing3=5,
        )
        text.tag_configure(
            "bullet",
            foreground=ui.TEXT_PRIMARY,
            lmargin1=18,
            lmargin2=34,
            spacing3=4,
        )
        text.tag_configure(
            "numbered",
            foreground=ui.TEXT_PRIMARY,
            lmargin1=18,
            lmargin2=40,
            spacing3=4,
        )
        for block in ui.parse_markdown_blocks(markdown):
            if block.kind == "blank":
                text.insert("end", "\n")
            elif block.kind == "bullet":
                text.insert("end", f"• {block.text}\n", "bullet")
            else:
                text.insert("end", block.text + "\n", block.kind)

    def _copy_minutes(self, markdown: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(markdown)
        self._set_status("会议纪要已复制", "info")

    def _save_minutes(self, markdown: str) -> None:
        started_at = self._meeting_started_at or datetime.now()
        path = filedialog.asksaveasfilename(
            parent=self._minutes_window or self.root,
            title="保存会议纪要",
            defaultextension=".md",
            filetypes=(("Markdown 文件", "*.md"), ("文本文件", "*.txt")),
            initialfile=f"会议纪要_{started_at:%Y%m%d_%H%M}.md",
        )
        if not path:
            return
        try:
            Path(path).write_text(markdown, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self._minutes_window)
            return
        self._set_status("会议纪要已保存", "info")

    def _on_close(self) -> None:
        if self._state in {"running", "starting"} and self._session:
            self._set_status("正在关闭…", "busy", busy=True)
            try:
                self._session.stop()
            except Exception:
                pass
        self.root.destroy()
