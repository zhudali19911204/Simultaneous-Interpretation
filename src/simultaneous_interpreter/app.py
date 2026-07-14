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
        self.root.geometry("1040x760")
        self.root.minsize(860, 650)
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

        self._configure_style()
        self._build_ui()
        self._refresh_devices(show_errors=False)
        self.root.after(self.POLL_MS, self._drain_events)

    def run(self) -> None:
        self.root.mainloop()

    def _configure_style(self) -> None:
        self.root.configure(bg="#0f172a")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#0f172a")
        style.configure("Card.TFrame", background="#172033")
        style.configure(
            "TLabel", background="#0f172a", foreground="#dbeafe", font=("Segoe UI", 10)
        )
        style.configure(
            "Title.TLabel", background="#0f172a", foreground="#f8fafc", font=("Segoe UI", 20, "bold")
        )
        style.configure(
            "Subtitle.TLabel", background="#0f172a", foreground="#94a3b8", font=("Segoe UI", 10)
        )
        style.configure(
            "CardTitle.TLabel", background="#172033", foreground="#f8fafc", font=("Segoe UI", 12, "bold")
        )
        style.configure(
            "Interim.TLabel", background="#172033", foreground="#60a5fa", font=("Microsoft YaHei UI", 13)
        )
        style.configure("TCheckbutton", background="#0f172a", foreground="#dbeafe")
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))
        style.configure(
            "Gear.TButton",
            font=("Segoe UI Symbol", 14),
            padding=(6, 3),
        )

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        self.settings_button = ttk.Button(
            header,
            text="⚙",
            width=3,
            style="Gear.TButton",
            command=self._open_settings,
            cursor="hand2",
        )
        self.settings_button.pack(side="right")
        ttk.Label(header, text="Teams 中英同声翻译助手", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="对方英文 → 你看中文｜你说中文 → 对方听英文",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        config = ttk.LabelFrame(outer, text="音频路由", padding=12)
        config.pack(fill="x", pady=(0, 12))
        for column in range(4):
            config.columnconfigure(column, weight=1)
        ttk.Label(config, text="你的实体麦克风").grid(row=0, column=0, sticky="w")
        ttk.Label(config, text="Teams 扬声器回放捕获").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(config, text="英文输出（选 CABLE Input）").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Label(config, text="英文声音").grid(row=0, column=3, sticky="w", padx=(10, 0))

        self.microphone_combo = ttk.Combobox(config, textvariable=self.microphone_var, state="readonly")
        self.microphone_combo.grid(row=1, column=0, sticky="ew")
        self.loopback_combo = ttk.Combobox(config, textvariable=self.loopback_var, state="readonly")
        self.loopback_combo.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        self.output_combo = ttk.Combobox(config, textvariable=self.output_var, state="readonly")
        self.output_combo.grid(row=1, column=2, sticky="ew", padx=(10, 0))
        self.voice_combo = ttk.Combobox(
            config,
            textvariable=self.voice_var,
            state="readonly",
            values=tuple(label for label, _ in self.VOICE_CHOICES),
        )
        self.voice_combo.grid(row=1, column=3, sticky="ew", padx=(10, 0))
        self._sync_voice_choices()

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(
            controls, text="开始同传", style="Primary.TButton", command=self._start
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="停止", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.test_button = ttk.Button(
            controls, text="测试输出声道", command=self._test_output, state="disabled"
        )
        self.test_button.pack(side="left", padx=(8, 0))
        self.refresh_button = ttk.Button(controls, text="刷新音频设备", command=self._refresh_devices)
        self.refresh_button.pack(side="left", padx=(8, 0))
        self.silence_gate_check = ttk.Checkbutton(
            controls,
            text="省流量静音门控",
            variable=self.silence_gate_var,
        )
        self.silence_gate_check.pack(side="left", padx=(12, 0))
        self._sync_voice_choices()
        ttk.Checkbutton(
            controls,
            text="窗口置顶",
            variable=self.always_on_top_var,
            command=self._toggle_topmost,
        ).pack(side="right")
        ttk.Label(controls, textvariable=self.status_var).pack(side="right", padx=(0, 16))
        ttk.Label(controls, textvariable=self.usage_var).pack(side="right", padx=(0, 16))

        panes = ttk.Frame(outer)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)
        panes.rowconfigure(0, weight=1)

        incoming = ttk.Frame(panes, style="Card.TFrame", padding=14)
        incoming.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        outgoing = ttk.Frame(panes, style="Card.TFrame", padding=14)
        outgoing.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        ttk.Label(incoming, text="对方英文 → 中文字幕", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            incoming,
            textvariable=self.incoming_interim_var,
            style="Interim.TLabel",
            wraplength=450,
        ).pack(fill="x", anchor="w", pady=(10, 10))
        self.incoming_history = self._make_history(incoming)

        ttk.Label(outgoing, text="你的中文 → 英文语音", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            outgoing,
            textvariable=self.outgoing_interim_var,
            style="Interim.TLabel",
            wraplength=450,
        ).pack(fill="x", anchor="w", pady=(10, 10))
        self.outgoing_history = self._make_history(outgoing)

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Label(
            footer,
            text="隐私提示：音频和字幕会发送到你选择的供应商；API Key 保存到 Windows 凭据管理器。",
            style="Subtitle.TLabel",
        ).pack(side="left")
        self.clear_history_button = ttk.Button(
            footer,
            text="清空记录",
            command=self._clear_history,
        )
        self.clear_history_button.pack(side="right")
        self.minutes_button = ttk.Button(
            footer,
            text="生成 AI 会议纪要",
            command=self._generate_minutes,
            state="disabled",
        )
        self.minutes_button.pack(side="right", padx=(0, 8))

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
        window.geometry("840x570")
        window.minsize(720, 520)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._cancel_settings)

        content = ttk.Frame(window, padding=16)
        content.pack(fill="both", expand=True)

        interpreter = ttk.LabelFrame(content, text="同声翻译服务", padding=12)
        interpreter.pack(fill="x")
        for column in range(2):
            interpreter.columnconfigure(column, weight=1)
        ttk.Label(interpreter, text="供应商 / 协议").grid(row=0, column=0, sticky="w")
        self.interpreter_model_label = ttk.Label(interpreter, text="实时模型")
        self.interpreter_model_label.grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        self.interpreter_provider_combo = ttk.Combobox(
            interpreter,
            textvariable=self.interpreter_provider_var,
            state="readonly",
            values=tuple(item.label for item in INTERPRETER_PROVIDERS),
        )
        self.interpreter_provider_combo.grid(row=1, column=0, sticky="ew")
        self.interpreter_model_combo = ttk.Combobox(
            interpreter,
            textvariable=self.interpreter_model_var,
            values=("qwen3.5-livetranslate-flash-realtime",),
        )
        self.interpreter_model_combo.grid(
            row=1, column=1, sticky="ew", padx=(12, 0)
        )
        self.interpreter_api_key_label = ttk.Label(interpreter, text="API Key")
        self.interpreter_api_key_label.grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        self.interpreter_identity_label = ttk.Label(
            interpreter, text="WorkspaceId（百炼）"
        )
        self.interpreter_identity_label.grid(
            row=2, column=1, sticky="w", padx=(12, 0), pady=(10, 0)
        )
        self.api_key_entry = ttk.Entry(
            interpreter, textvariable=self.api_key_var, show="●"
        )
        self.api_key_entry.grid(row=3, column=0, sticky="ew")
        self.workspace_id_entry = ttk.Entry(
            interpreter, textvariable=self.workspace_id_var
        )
        self.workspace_id_entry.grid(row=3, column=1, sticky="ew", padx=(12, 0))
        self.interpreter_websocket_url_label = ttk.Label(
            interpreter,
            text="WebSocket 地址（仅自定义 LiveTranslate 兼容接口）",
        )
        self.interpreter_websocket_url_label.grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        self.interpreter_websocket_url_entry = ttk.Entry(
            interpreter, textvariable=self.interpreter_websocket_url_var
        )
        self.interpreter_websocket_url_entry.grid(
            row=5, column=0, columnspan=2, sticky="ew"
        )

        minutes = ttk.LabelFrame(content, text="AI 会议纪要", padding=12)
        minutes.pack(fill="x", pady=(12, 0))
        for column in range(2):
            minutes.columnconfigure(column, weight=1)
        ttk.Label(minutes, text="LLM 供应商").grid(row=0, column=0, sticky="w")
        ttk.Label(minutes, text="模型名称").grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        self.minutes_provider_combo = ttk.Combobox(
            minutes,
            textvariable=self.minutes_provider_var,
            state="readonly",
            values=tuple(item.label for item in LLM_PROVIDERS),
        )
        self.minutes_provider_combo.grid(row=1, column=0, sticky="ew")
        self.minutes_model_combo = ttk.Combobox(
            minutes,
            textvariable=self.minutes_model_var,
            values=self.MINUTES_MODEL_CHOICES,
        )
        self.minutes_model_combo.grid(row=1, column=1, sticky="ew", padx=(12, 0))
        ttk.Label(minutes, text="API Key（百炼可复用同传；Ollama 可空）").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Label(minutes, text="Chat Completions 地址").grid(
            row=2, column=1, sticky="w", padx=(12, 0), pady=(10, 0)
        )
        self.minutes_api_key_entry = ttk.Entry(
            minutes, textvariable=self.minutes_api_key_var, show="●"
        )
        self.minutes_api_key_entry.grid(row=3, column=0, sticky="ew")
        self.minutes_api_url_entry = ttk.Entry(
            minutes, textvariable=self.minutes_api_url_var
        )
        self.minutes_api_url_entry.grid(row=3, column=1, sticky="ew", padx=(12, 0))
        ttk.Label(minutes, text="附加请求参数 JSON").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        self.minutes_extra_body_entry = ttk.Entry(
            minutes, textvariable=self.minutes_extra_body_var
        )
        self.minutes_extra_body_entry.grid(
            row=5, column=0, columnspan=2, sticky="ew"
        )

        self.interpreter_provider_combo.bind(
            "<<ComboboxSelected>>", self._on_interpreter_provider_changed
        )
        self.minutes_provider_combo.bind(
            "<<ComboboxSelected>>", self._on_minutes_provider_changed
        )

        actions = ttk.Frame(content)
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(
            actions,
            text="清除凭据",
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

        self._apply_provider_controls()
        window.grab_set()
        window.focus_force()

    def _close_settings(self) -> None:
        window = self._settings_window
        self._settings_window = None
        self._settings_snapshot = None
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

    @staticmethod
    def _make_history(parent: ttk.Frame) -> tk.Text:
        history = tk.Text(
            parent,
            wrap="word",
            width=1,
            height=1,
            relief="flat",
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            selectbackground="#1d4ed8",
            font=("Microsoft YaHei UI", 11),
            padx=12,
            pady=10,
            state="disabled",
        )
        history.pack(fill="both", expand=True)
        history.tag_configure("source", foreground="#94a3b8", spacing1=8)
        history.tag_configure("translation", foreground="#f8fafc", spacing3=12, font=("Microsoft YaHei UI", 12, "bold"))
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
                self.status_var.set("音频设备已刷新")
        except Exception as exc:
            self.status_var.set("无法读取音频设备")
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
            self.status_var.set("请填写该供应商实际开放的会议纪要模型名")

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
            messagebox.showerror("保存失败", str(exc))
            return False
        self.interpreter_model_var.set(settings.interpreter_model)
        self.minutes_model_var.set(settings.meeting_minutes_model)
        self.minutes_extra_body_var.set(settings.meeting_minutes_extra_body)
        self.status_var.set("配置已保存")
        messagebox.showinfo(
            "保存成功",
            "API Key 已保存到 Windows 凭据管理器；供应商、地址和模型已保存到本机配置。",
        )
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
        self.status_var.set("凭据已清除")

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
        self.status_var.set(f"正在连接{provider_label}…")
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
        self.status_var.set("正在停止…")
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
            self.status_var.set("已向英文输出设备发送测试音")

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
        self.status_var.set("正在生成 AI 会议纪要…")
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
            self.status_var.set("同传运行中")
            self.stop_button.configure(state="normal")
            self.test_button.configure(state="normal")
        elif message.kind == "start_failed":
            self._state = "idle"
            self._session = None
            self.status_var.set("启动失败")
            self._set_controls_running(False)
            messagebox.showerror("启动失败", str(message.payload))
        elif message.kind == "stopped":
            self._state = "idle"
            self._session = None
            self._meeting_ended_at = datetime.now()
            self.status_var.set("已停止")
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
            self.status_var.set(str(message.payload))
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
            self.status_var.set(
                "会议纪要已生成："
                f"输入 {result.input_tokens} / 输出 {result.output_tokens} Token"
            )
            self.clear_history_button.configure(state="normal")
            self._update_minutes_button()
            self._show_minutes_window(result.markdown)
        elif message.kind == "minutes_failed":
            self._minutes_generating = False
            self.status_var.set("会议纪要生成失败")
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
        self.minutes_button.configure(state="normal" if enabled else "disabled")

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.always_on_top_var.get())

    def _clear_history(self) -> None:
        if self._meeting_turns and not messagebox.askyesno(
            "清空会议记录",
            "清空后将无法基于当前字幕生成会议纪要，确定继续吗？",
        ):
            return
        for history in (self.incoming_history, self.outgoing_history):
            history.configure(state="normal")
            history.delete("1.0", "end")
            history.configure(state="disabled")
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
        window.geometry("860x700")
        window.minsize(640, 480)

        toolbar = ttk.Frame(window, padding=(12, 10))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="AI 会议纪要", style="CardTitle.TLabel").pack(side="left")
        ttk.Button(
            toolbar,
            text="保存 Markdown",
            command=lambda: self._save_minutes(markdown),
        ).pack(side="right")
        ttk.Button(
            toolbar,
            text="复制",
            command=lambda: self._copy_minutes(markdown),
        ).pack(side="right", padx=(0, 8))

        text = tk.Text(
            window,
            wrap="word",
            width=1,
            height=1,
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            selectbackground="#1d4ed8",
            font=("Microsoft YaHei UI", 11),
            padx=16,
            pady=14,
        )
        text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        text.insert("1.0", markdown)
        text.configure(state="disabled")

    def _copy_minutes(self, markdown: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(markdown)
        self.status_var.set("会议纪要已复制")

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
        self.status_var.set("会议纪要已保存")

    def _on_close(self) -> None:
        if self._state in {"running", "starting"} and self._session:
            self.status_var.set("正在关闭…")
            try:
                self._session.stop()
            except Exception:
                pass
        self.root.destroy()
