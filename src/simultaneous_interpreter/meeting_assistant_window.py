from __future__ import annotations

import io
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timedelta
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageTk

from . import ui_theme as ui
from .meeting_assistant import (
    AssistantResult,
    AssistantUsage,
    InsightUpdate,
    MeetingAssistantClient,
    filter_timeline_turns,
    format_assistant_turn,
    result_generation_is_current,
    should_auto_refresh,
    snapshot_requires_reset,
)
from .meeting_minutes import MeetingTurn
from .screen_capture import (
    CaptureRegion,
    GlobalPauseHotkey,
    QuickSummaryOverlay,
    RegionSelector,
    ScreenGrabber,
)
from .visual_analysis import (
    OpenAICompatibleVisionClient,
    PageChangeDetector,
    PreparedImage,
    ScheduleDecision,
    VisualAnalysis,
    VisualAnalysisScheduler,
    VisualMoment,
    VisualUsage,
    fit_image_size,
    format_visual_context,
    prepare_image,
    visual_analysis_text,
)


@dataclass(frozen=True)
class _AssistantMessage:
    window_generation: int
    data_generation: int
    kind: str
    payload: object = None
    question: str = ""
    visual_generation: int = 0


@dataclass(frozen=True)
class _VisualCandidate:
    captured_at: datetime
    prepared: PreparedImage


@dataclass(frozen=True)
class _CaptureResult:
    candidate: _VisualCandidate
    sampled_at: float
    manual: bool = False


@dataclass(frozen=True)
class _VisionResult:
    candidate: _VisualCandidate
    analysis: VisualAnalysis
    elapsed_ms: int


@dataclass(frozen=True)
class _VisualFailure:
    message: str
    error_type: str


class MeetingAssistantWindow:
    POLL_MS = 200
    VISUAL_PREVIEW_HEIGHT = 240

    def __init__(
        self,
        owner: tk.Tk,
        fonts: ui.ThemeFonts,
        turn_supplier: Callable[[], tuple[MeetingTurn, ...]],
        client_factory: Callable[[], MeetingAssistantClient],
        visual_supplier: Callable[[], tuple[VisualMoment, ...]] | None = None,
        visual_appender: Callable[[VisualMoment], None] | None = None,
        vision_client_factory: Callable[[], OpenAICompatibleVisionClient] | None = None,
        capture_allowed: Callable[[], bool] | None = None,
        visual_diagnostic: Callable[[str, int, int, str], None] | None = None,
    ) -> None:
        self._owner = owner
        self._fonts = fonts
        self._turn_supplier = turn_supplier
        self._client_factory = client_factory
        self._visual_supplier = visual_supplier or (lambda: ())
        self._visual_appender = visual_appender or (lambda _moment: None)
        self._vision_client_factory = vision_client_factory
        self._capture_allowed = capture_allowed or (lambda: True)
        self._visual_diagnostic = visual_diagnostic
        self._window: tk.Toplevel | None = None
        self._after_id: str | None = None
        self._window_generation = 0
        self._data_generation = 0
        self._messages: queue.Queue[_AssistantMessage] = queue.Queue()
        self._task_lock = threading.Lock()
        self._next_task_id = 0
        self._active_task_id: int | None = None
        self._snapshot: tuple[MeetingTurn, ...] = ()
        self._visual_snapshot: tuple[VisualMoment, ...] = ()
        self._insight_markdown = ""
        self._processed_turn_count = 0
        self._last_attempt_at: datetime | None = None
        self._usage = AssistantUsage()
        self._questions: list[tuple[str, str]] = []
        self._auto_enabled = False
        self._pending_question: str | None = None
        self._visual_questions: list[tuple[int, str, str]] = []
        self._visual_usage = VisualUsage()
        self._region: CaptureRegion | None = None
        self._selector: RegionSelector | None = None
        self._grabber = ScreenGrabber()
        self._detector = PageChangeDetector()
        self._visual_scheduler = VisualAnalysisScheduler()
        self._capture_state = "stopped"
        self._capture_inflight = False
        self._next_capture_at = 0.0
        self._latest_candidate: _VisualCandidate | None = None
        self._capture_generation = 0
        self._hotkey = GlobalPauseHotkey()
        self._quick_overlay = QuickSummaryOverlay(owner, fonts)
        self._thumbnail_photo: ImageTk.PhotoImage | None = None
        self._visual_preview_image: Image.Image | None = None
        self._visual_preview_message = "尚无共享画面分析"
        self._visual_preview_after_id: str | None = None
        self._visual_progress_active = False

    @property
    def visible(self) -> bool:
        return bool(self._window and self._window.winfo_exists())

    def show(self) -> None:
        if self.visible:
            assert self._window is not None
            self._window.deiconify()
            self._window.lift()
            self._window.focus_force()
            return
        self._window_generation += 1
        self._create_window()
        self._sync_snapshot()
        self._render_all()
        self._schedule_tick()

    def destroy(self) -> None:
        self._close_window()

    def stop_visual_capture(self) -> None:
        self._stop_visual_capture("同传已停止，共享画面分析已停止")

    def records_cleared(self) -> None:
        self._data_generation += 1
        self._stop_visual_capture("会议记录已清空")
        self._snapshot = ()
        self._visual_snapshot = ()
        self._reset_analysis_state()

    def _create_window(self) -> None:
        window = tk.Toplevel(self._owner)
        self._window = window
        window.title("AI 会议理解助手")
        window.geometry("1080x760")
        window.minsize(820, 600)
        window.configure(bg=ui.BACKGROUND)
        window.protocol("WM_DELETE_WINDOW", self._close_window)
        window.bind("<Escape>", lambda _event: self._close_window())

        outer = ttk.Frame(window, style="Background.TFrame", padding=ui.SPACE_4)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        header = ttk.Frame(outer, style="Background.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, ui.SPACE_3))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="AI 会议理解助手",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self._usage_var = tk.StringVar(value="助手 Token：0")
        ttk.Label(
            header,
            textvariable=self._usage_var,
            style="Subtitle.TLabel",
        ).grid(row=0, column=1, sticky="e")

        self._status_var = tk.StringVar(value="仅读取当前会议的最终字幕")
        self._status_label = tk.Label(
            outer,
            textvariable=self._status_var,
            bg=ui.SURFACE_ELEVATED,
            fg=ui.TEXT_MUTED,
            anchor="w",
            padx=10,
            pady=6,
            font=(self._fonts.ui, 9),
        )
        self._status_label.grid(row=1, column=0, sticky="ew", pady=(0, ui.SPACE_2))

        notebook = ttk.Notebook(outer)
        notebook.grid(row=2, column=0, sticky="nsew")
        insight_tab = ttk.Frame(notebook, style="Surface.TFrame", padding=ui.SPACE_3)
        question_tab = ttk.Frame(notebook, style="Surface.TFrame", padding=ui.SPACE_3)
        timeline_tab = ttk.Frame(notebook, style="Surface.TFrame", padding=ui.SPACE_3)
        visual_tab = ttk.Frame(notebook, style="Surface.TFrame", padding=ui.SPACE_3)
        notebook.add(insight_tab, text="实时重点")
        notebook.add(question_tab, text="会中问答")
        notebook.add(timeline_tab, text="完整时间线")
        notebook.add(visual_tab, text="共享画面")
        self._build_insight_tab(insight_tab)
        self._build_question_tab(question_tab)
        self._build_timeline_tab(timeline_tab)
        self._build_visual_tab(visual_tab)

    def _build_insight_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(parent, style="Surface.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, ui.SPACE_2))
        self._refresh_button = ttk.Button(
            toolbar,
            text="刷新重点",
            command=lambda: self._start_insight(manual=True),
        )
        self._refresh_button.pack(side="left")
        self._auto_var = tk.BooleanVar(value=self._auto_enabled)
        ttk.Checkbutton(
            toolbar,
            text="每 5 分钟自动更新",
            variable=self._auto_var,
            command=self._on_auto_changed,
        ).pack(side="left", padx=(ui.SPACE_3, 0))
        self._insight_time_var = tk.StringVar(value="尚未生成")
        ttk.Label(
            toolbar,
            textvariable=self._insight_time_var,
            style="Helper.TLabel",
        ).pack(side="right")
        self._insight_text = self._make_readonly_text(parent)
        self._insight_text.grid(row=1, column=0, sticky="nsew")
        self._configure_markdown_tags(self._insight_text)

    def _build_question_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self._question_history = self._make_readonly_text(parent)
        self._question_history.grid(row=0, column=0, sticky="nsew")
        self._question_history.tag_configure(
            "question",
            foreground=ui.INFO,
            font=(self._fonts.transcript, 11, "bold"),
            spacing1=10,
        )
        self._question_history.tag_configure(
            "answer",
            foreground=ui.TEXT_PRIMARY,
            font=(self._fonts.transcript, 11),
            spacing3=14,
        )
        controls = ttk.Frame(parent, style="Surface.TFrame")
        controls.grid(row=1, column=0, sticky="ew", pady=(ui.SPACE_2, 0))
        controls.columnconfigure(0, weight=1)
        self._question_var = tk.StringVar()
        self._question_entry = ttk.Entry(
            controls,
            textvariable=self._question_var,
        )
        self._question_entry.grid(row=0, column=0, sticky="ew")
        self._question_entry.bind("<Return>", lambda _event: self._start_question())
        self._ask_button = ttk.Button(
            controls,
            text="提问",
            style="Primary.TButton",
            command=self._start_question,
        )
        self._ask_button.grid(row=0, column=1, padx=(ui.SPACE_2, 0))
        ttk.Button(
            controls,
            text="复制选中",
            command=lambda: self._copy_selection(self._question_history),
        ).grid(row=0, column=2, padx=(ui.SPACE_2, 0))

    def _build_timeline_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(parent, style="Surface.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, ui.SPACE_2))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="搜索", style="Surface.TLabel").grid(
            row=0,
            column=0,
            padx=(0, ui.SPACE_2),
        )
        self._search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self._search_var)
        search.grid(row=0, column=1, sticky="ew")
        self._search_var.trace_add("write", lambda *_args: self._render_timeline())
        self._direction_var = tk.StringVar(value="全部")
        direction = ttk.Combobox(
            toolbar,
            textvariable=self._direction_var,
            values=("全部", "对方", "我"),
            state="readonly",
            width=8,
        )
        direction.grid(row=0, column=2, padx=(ui.SPACE_2, 0))
        direction.bind("<<ComboboxSelected>>", lambda _event: self._render_timeline())
        ttk.Button(
            toolbar,
            text="复制选中",
            command=lambda: self._copy_selection(self._timeline_text),
        ).grid(row=0, column=3, padx=(ui.SPACE_2, 0))
        self._timeline_text = self._make_readonly_text(parent)
        self._timeline_text.grid(row=1, column=0, sticky="nsew")
        self._timeline_text.tag_configure(
            "incoming",
            foreground=ui.INFO,
            font=(self._fonts.transcript, 11),
            spacing1=8,
            spacing3=5,
        )
        self._timeline_text.tag_configure(
            "outgoing",
            foreground=ui.FOCUS,
            font=(self._fonts.transcript, 11),
            spacing1=8,
            spacing3=5,
        )
        self._timeline_text.tag_configure(
            "match",
            foreground=ui.TEXT_PRIMARY,
            background=ui.PRIMARY,
        )

    def _build_visual_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(parent, style="Surface.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, ui.SPACE_2))
        self._visual_start_button = ttk.Button(
            toolbar,
            text="选择区域并开始",
            style="Primary.TButton",
            command=self._select_visual_region,
        )
        self._visual_start_button.pack(side="left")
        self._visual_pause_button = ttk.Button(
            toolbar,
            text="暂停",
            command=self._toggle_visual_pause,
            state="disabled",
        )
        self._visual_pause_button.pack(side="left", padx=(ui.SPACE_2, 0))
        self._visual_reselect_button = ttk.Button(
            toolbar,
            text="重新选择",
            command=self._select_visual_region,
            state="disabled",
        )
        self._visual_reselect_button.pack(side="left", padx=(ui.SPACE_2, 0))
        self._visual_stop_button = ttk.Button(
            toolbar,
            text="停止分析",
            style="Danger.TButton",
            command=lambda: self._stop_visual_capture("共享画面分析已停止"),
            state="disabled",
        )
        self._visual_stop_button.pack(side="left", padx=(ui.SPACE_2, 0))
        self._visual_analyze_button = ttk.Button(
            toolbar,
            text="分析当前页",
            command=lambda: self._capture_once(manual=True),
            state="disabled",
        )
        self._visual_analyze_button.pack(side="left", padx=(ui.SPACE_2, 0))
        self._quick_overlay_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            toolbar,
            text="显示中文速读浮窗",
            variable=self._quick_overlay_var,
            command=self._toggle_quick_overlay,
            style="Surface.TCheckbutton",
        ).pack(side="right")
        self._visual_progress = ttk.Progressbar(
            toolbar,
            mode="indeterminate",
            length=88,
        )

        self._visual_helper_var = tk.StringVar(
            value="选择 Teams/PPT 共享区域后，每秒本地采样；稳定换页约 2 秒后分析。"
        )
        ttk.Label(
            parent,
            textvariable=self._visual_helper_var,
            style="Helper.TLabel",
            wraplength=960,
        ).grid(row=1, column=0, sticky="ew", pady=(0, ui.SPACE_2))

        panes = ttk.Panedwindow(parent, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")
        timeline = ttk.Frame(panes, style="Card.TFrame", padding=ui.SPACE_2)
        details = ttk.Frame(panes, style="Card.TFrame", padding=ui.SPACE_2)
        panes.add(timeline, weight=1)
        panes.add(details, weight=3)

        timeline.columnconfigure(0, weight=1)
        timeline.rowconfigure(1, weight=1)
        ttk.Label(
            timeline,
            text="视觉时间线",
            style="CardTitle.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, ui.SPACE_2))
        list_frame = ttk.Frame(timeline, style="Card.TFrame")
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self._visual_list = tk.Listbox(
            list_frame,
            bg=ui.TEXT_SURFACE,
            fg=ui.TEXT_PRIMARY,
            selectbackground=ui.PRIMARY,
            selectforeground="#FFFFFF",
            highlightthickness=1,
            highlightbackground=ui.BORDER,
            highlightcolor=ui.FOCUS,
            relief="flat",
            borderwidth=0,
            activestyle="none",
            font=(self._fonts.ui, 10),
            exportselection=False,
        )
        visual_scroll = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self._visual_list.yview,
        )
        self._visual_list.configure(yscrollcommand=visual_scroll.set)
        self._visual_list.grid(row=0, column=0, sticky="nsew")
        visual_scroll.grid(row=0, column=1, sticky="ns")
        self._visual_list.bind("<<ListboxSelect>>", self._on_visual_selected)

        details.columnconfigure(0, weight=1)
        details.rowconfigure(0, minsize=self.VISUAL_PREVIEW_HEIGHT)
        details.rowconfigure(1, weight=2)
        details.rowconfigure(3, weight=1)
        self._visual_image_canvas = tk.Canvas(
            details,
            bg=ui.TEXT_SURFACE,
            height=self.VISUAL_PREVIEW_HEIGHT,
            highlightthickness=1,
            highlightbackground=ui.BORDER,
            highlightcolor=ui.FOCUS,
            relief="flat",
            borderwidth=0,
            takefocus=False,
        )
        self._visual_image_canvas.grid(row=0, column=0, sticky="nsew")
        self._visual_image_canvas.bind(
            "<Configure>",
            self._on_visual_preview_resize,
        )
        self._visual_detail_text = self._make_readonly_text(details)
        self._visual_detail_text.grid(
            row=1,
            column=0,
            sticky="nsew",
            pady=(ui.SPACE_2, 0),
        )
        self._visual_detail_text.tag_configure(
            "visual_title",
            foreground=ui.INFO,
            font=(self._fonts.transcript, 14, "bold"),
            spacing3=8,
        )
        self._visual_detail_text.tag_configure(
            "visual_body",
            foreground=ui.TEXT_PRIMARY,
            font=(self._fonts.transcript, 10),
            spacing3=5,
        )

        question_controls = ttk.Frame(details, style="Card.TFrame")
        question_controls.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(ui.SPACE_2, 0),
        )
        question_controls.columnconfigure(0, weight=1)
        self._visual_question_var = tk.StringVar()
        self._visual_question_entry = ttk.Entry(
            question_controls,
            textvariable=self._visual_question_var,
        )
        self._visual_question_entry.grid(row=0, column=0, sticky="ew")
        self._visual_question_entry.bind(
            "<Return>",
            lambda _event: self._start_visual_question(),
        )
        self._visual_ask_button = ttk.Button(
            question_controls,
            text="针对本页提问",
            command=self._start_visual_question,
            state="disabled",
        )
        self._visual_ask_button.grid(row=0, column=1, padx=(ui.SPACE_2, 0))
        self._visual_answer_text = self._make_readonly_text(details)
        self._visual_answer_text.grid(
            row=3,
            column=0,
            sticky="nsew",
            pady=(ui.SPACE_2, 0),
        )

    def _make_readonly_text(self, parent: ttk.Frame) -> tk.Text:
        text = tk.Text(
            parent,
            wrap="word",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=ui.BORDER,
            highlightcolor=ui.FOCUS,
            bg=ui.TEXT_SURFACE,
            fg=ui.TEXT_PRIMARY,
            insertbackground=ui.TEXT_PRIMARY,
            selectbackground=ui.SELECTION,
            selectforeground=ui.TEXT_PRIMARY,
            padx=12,
            pady=10,
            font=(self._fonts.transcript, 11),
            state="disabled",
        )
        return text

    def _configure_markdown_tags(self, text: tk.Text) -> None:
        text.tag_configure(
            "h1",
            foreground=ui.TEXT_PRIMARY,
            font=(self._fonts.transcript, 18, "bold"),
            spacing1=14,
            spacing3=8,
        )
        text.tag_configure(
            "h2",
            foreground=ui.INFO,
            font=(self._fonts.transcript, 14, "bold"),
            spacing1=12,
            spacing3=6,
        )
        text.tag_configure(
            "h3",
            foreground=ui.TEXT_PRIMARY,
            font=(self._fonts.transcript, 12, "bold"),
            spacing1=10,
        )
        text.tag_configure("body", foreground=ui.TEXT_PRIMARY, spacing3=5)
        text.tag_configure("bullet", foreground=ui.TEXT_SECONDARY, lmargin1=14, lmargin2=28)
        text.tag_configure("numbered", foreground=ui.TEXT_SECONDARY, lmargin1=14, lmargin2=28)

    def _schedule_tick(self) -> None:
        if self.visible:
            self._after_id = self._owner.after(self.POLL_MS, self._tick)

    def _tick(self) -> None:
        self._after_id = None
        if not self.visible:
            return
        self._sync_snapshot()
        self._drain_messages()
        if self._hotkey.consume():
            self._toggle_visual_pause()
        now = time.monotonic()
        if (
            self._capture_state == "running"
            and not self._capture_inflight
            and now >= self._next_capture_at
        ):
            self._capture_once(manual=False)
        self._handle_schedule_decision(self._visual_scheduler.poll(now))
        self._update_controls()
        if should_auto_refresh(
            enabled=self._auto_enabled,
            window_visible=True,
            busy=self._is_busy(),
            now=datetime.now(),
            last_attempt_at=self._last_attempt_at,
            turns=self._snapshot,
            processed_turn_count=self._processed_turn_count,
        ):
            self._start_insight(manual=False)
        self._schedule_tick()

    def _sync_snapshot(self) -> None:
        try:
            turns = tuple(self._turn_supplier())
        except Exception as exc:
            self._set_status(f"读取会议记录失败：{exc}", error=True)
            return
        if snapshot_requires_reset(self._snapshot, turns):
            self._reset_analysis_state()
            self._set_status("会议记录已更新，助手状态已重置")
        if turns != self._snapshot:
            self._snapshot = turns
            self._render_timeline()
        try:
            visuals = tuple(self._visual_supplier())
        except Exception as exc:
            self._set_status(f"读取共享画面记录失败：{exc}", error=True)
            return
        if (
            self._visual_snapshot
            and (
                len(visuals) < len(self._visual_snapshot)
                or tuple(
                    item.sequence
                    for item in visuals[: len(self._visual_snapshot)]
                )
                != tuple(item.sequence for item in self._visual_snapshot)
            )
        ):
            self._visual_questions.clear()
            self._capture_generation += 1
        if visuals != self._visual_snapshot:
            self._visual_snapshot = visuals
            self._render_visual_timeline(select_latest=True)

    def _reset_analysis_state(self) -> None:
        self._data_generation += 1
        self._insight_markdown = ""
        self._processed_turn_count = 0
        self._last_attempt_at = None
        self._usage = AssistantUsage()
        self._questions.clear()
        self._visual_questions.clear()
        self._visual_usage = VisualUsage()
        self._pending_question = None
        self._invalidate_active_task()
        if self.visible:
            self._render_all()

    def _start_insight(self, *, manual: bool) -> None:
        if self._is_busy():
            if manual:
                self._set_status("会议助手正在处理其他请求，请稍候")
            return
        if not self._snapshot and not self._visual_snapshot:
            self._set_status("当前没有可整理的会议记录或共享画面", error=True)
            return
        if (
            self._processed_turn_count >= len(self._snapshot)
            and not self._visual_snapshot
        ):
            self._set_status("没有新的会议内容可整理")
            return
        try:
            client = self._client_factory()
        except Exception as exc:
            self._set_status(f"会议助手配置无效：{exc}", error=True)
            return
        self._last_attempt_at = datetime.now()
        task_id = self._begin_task()
        if task_id is None:
            return
        self._set_status("正在更新实时重点…")
        generation = self._window_generation
        data_generation = self._data_generation
        turns = self._snapshot
        previous = self._insight_markdown
        processed = self._processed_turn_count
        visual_context = format_visual_context(
            self._visual_snapshot,
            turns,
        )

        def worker() -> None:
            try:
                result = client.update_insight(
                    turns,
                    previous,
                    processed,
                    visual_context,
                )
                message = _AssistantMessage(
                    generation,
                    data_generation,
                    "insight_ready",
                    result,
                )
            except Exception as exc:
                message = _AssistantMessage(
                    generation,
                    data_generation,
                    "failed",
                    str(exc),
                )
            finally:
                self._finish_task(task_id)
            self._messages.put(message)

        threading.Thread(target=worker, name="meeting-insight", daemon=True).start()

    def _start_question(self) -> None:
        question = self._question_var.get().strip()
        if not question:
            self._set_status("请输入会议问题", error=True)
            self._question_entry.focus_set()
            return
        if self._is_busy():
            self._pending_question = question
            self._set_status("提问已优先等待，当前整理完成后立即回答")
            return
        if not self._snapshot and not self._visual_snapshot:
            self._set_status("当前没有可用于回答的会议记录或共享画面", error=True)
            return
        try:
            client = self._client_factory()
        except Exception as exc:
            self._set_status(f"会议助手配置无效：{exc}", error=True)
            return
        task_id = self._begin_task()
        if task_id is None:
            return
        self._set_status("正在查找会议依据并回答…")
        generation = self._window_generation
        data_generation = self._data_generation
        turns = self._snapshot
        insight = self._insight_markdown
        visual_context = format_visual_context(
            self._visual_snapshot,
            turns,
            query=question,
        )

        def worker() -> None:
            try:
                result = client.answer(
                    question,
                    turns,
                    insight,
                    visual_context,
                )
                message = _AssistantMessage(
                    generation,
                    data_generation,
                    "answer_ready",
                    result,
                    question,
                )
            except Exception as exc:
                message = _AssistantMessage(
                    generation,
                    data_generation,
                    "failed",
                    str(exc),
                    question,
                )
            finally:
                self._finish_task(task_id)
            self._messages.put(message)

        threading.Thread(target=worker, name="meeting-question", daemon=True).start()

    def _drain_messages(self) -> None:
        try:
            while True:
                message = self._messages.get_nowait()
                if not result_generation_is_current(
                    message.window_generation,
                    message.data_generation,
                    self._window_generation,
                    self._data_generation,
                ):
                    continue
                if (
                    message.kind.startswith("visual_")
                    or message.kind.startswith("capture_")
                ) and message.visual_generation != self._capture_generation:
                    continue
                if message.kind == "insight_ready":
                    result = message.payload
                    assert isinstance(result, InsightUpdate)
                    self._insight_markdown = result.markdown
                    self._processed_turn_count = result.processed_turn_count
                    self._usage = self._usage + result.usage
                    self._insight_time_var.set(f"更新于 {datetime.now():%H:%M:%S}")
                    self._render_insight()
                    self._set_status("实时重点已更新")
                elif message.kind == "answer_ready":
                    result = message.payload
                    assert isinstance(result, AssistantResult)
                    self._questions.append((message.question, result.content))
                    self._usage = self._usage + result.usage
                    self._question_var.set("")
                    self._render_questions()
                    self._set_status("回答已生成")
                elif message.kind == "visual_answer_ready":
                    result = message.payload
                    assert isinstance(result, AssistantResult)
                    sequence_text, _, question = message.question.partition("|")
                    sequence = int(sequence_text)
                    self._visual_questions.append(
                        (sequence, question, result.content)
                    )
                    self._usage = self._usage + result.usage
                    self._visual_question_var.set("")
                    self._render_selected_visual()
                    self._set_status("本页回答已生成")
                elif message.kind == "capture_ready":
                    self._capture_inflight = False
                    self._quick_overlay.resume_after_capture()
                    result = message.payload
                    assert isinstance(result, _CaptureResult)
                    self._handle_capture_result(result)
                elif message.kind == "capture_sampled":
                    self._quick_overlay.resume_after_capture()
                elif message.kind == "capture_failed":
                    self._capture_inflight = False
                    self._quick_overlay.resume_after_capture()
                    failure = message.payload
                    assert isinstance(failure, _VisualFailure)
                    self._set_status(f"共享画面捕获失败：{failure.message}", error=True)
                    self._log_visual("capture_failed", 0, 0, failure.error_type)
                elif message.kind == "visual_ready":
                    result = message.payload
                    assert isinstance(result, _VisionResult)
                    self._handle_visual_ready(result)
                elif message.kind == "visual_failed":
                    failure = message.payload
                    assert isinstance(failure, _VisualFailure)
                    self._set_status(f"共享画面分析失败：{failure.message}", error=True)
                    self._log_visual("analysis_failed", 0, 0, failure.error_type)
                    self._handle_schedule_decision(
                        self._visual_scheduler.complete(time.monotonic())
                    )
                elif message.kind == "failed":
                    self._set_status(str(message.payload), error=True)
        except queue.Empty:
            pass
        if self._pending_question and not self._is_busy():
            question = self._pending_question
            self._pending_question = None
            self._question_var.set(question)
            self._start_question()

    def _render_all(self) -> None:
        if not self.visible:
            return
        self._render_insight()
        self._render_questions()
        self._render_timeline()
        self._render_visual_timeline()
        self._update_controls()

    def _render_insight(self) -> None:
        self._replace_text(self._insight_text, "")
        self._insight_text.configure(state="normal")
        markdown = self._insight_markdown or "尚未生成实时重点。点击“刷新重点”开始。"
        for block in ui.parse_markdown_blocks(markdown):
            if block.kind == "blank":
                self._insight_text.insert("end", "\n")
            elif block.kind == "bullet":
                self._insight_text.insert("end", f"• {block.text}\n", "bullet")
            else:
                self._insight_text.insert("end", block.text + "\n", block.kind)
        self._insight_text.configure(state="disabled")

    def _render_questions(self) -> None:
        self._question_history.configure(state="normal")
        self._question_history.delete("1.0", "end")
        if not self._questions:
            self._question_history.insert(
                "end",
                "输入问题，例如：对方刚才确认了什么？\n",
                "answer",
            )
        for question, answer in self._questions:
            self._question_history.insert("end", f"问：{question}\n", "question")
            self._question_history.insert("end", f"答：{answer}\n", "answer")
        self._question_history.see("end")
        self._question_history.configure(state="disabled")

    def _render_timeline(self) -> None:
        if not self.visible or not hasattr(self, "_timeline_text"):
            return
        directions = {"全部": "all", "对方": "incoming", "我": "outgoing"}
        turns = filter_timeline_turns(
            self._snapshot,
            direction=directions.get(self._direction_var.get(), "all"),
            query=self._search_var.get(),
        )
        text = self._timeline_text
        text.configure(state="normal")
        text.delete("1.0", "end")
        if not turns:
            message = "当前没有匹配的会议记录。" if self._snapshot else "会议字幕将在这里按时间显示。"
            text.insert("end", message + "\n", "incoming")
        for turn in turns:
            text.insert(
                "end",
                format_assistant_turn(turn) + "\n",
                turn.direction,
            )
        query = self._search_var.get().strip()
        if query:
            start = "1.0"
            while True:
                match = text.search(query, start, stopindex="end", nocase=True)
                if not match:
                    break
                end = f"{match}+{len(query)}c"
                text.tag_add("match", match, end)
                start = end
        text.configure(state="disabled")

    def _render_visual_timeline(self, *, select_latest: bool = False) -> None:
        if not self.visible or not hasattr(self, "_visual_list"):
            return
        selected_sequence = None
        current = self._selected_visual()
        if current is not None:
            selected_sequence = current.sequence
        self._visual_display = tuple(
            sorted(self._visual_snapshot, key=lambda item: item.captured_at)
        )
        self._visual_list.delete(0, "end")
        for moment in self._visual_display:
            title = moment.analysis.title
            if len(title) > 28:
                title = title[:27] + "…"
            self._visual_list.insert(
                "end",
                f"{moment.captured_at:%H:%M:%S} · 第{moment.sequence}页 · {title}",
            )
        if self._visual_display:
            index = len(self._visual_display) - 1
            if not select_latest and selected_sequence is not None:
                for candidate_index, moment in enumerate(self._visual_display):
                    if moment.sequence == selected_sequence:
                        index = candidate_index
                        break
            self._visual_list.selection_set(index)
            self._visual_list.see(index)
        self._render_selected_visual()

    def _on_visual_selected(self, _event: object = None) -> None:
        self._render_selected_visual()

    def _selected_visual(self) -> VisualMoment | None:
        if not hasattr(self, "_visual_list"):
            return None
        selection = self._visual_list.curselection()
        display = getattr(self, "_visual_display", ())
        if not selection or selection[0] >= len(display):
            return None
        return display[selection[0]]

    def _render_selected_visual(self) -> None:
        if not self.visible or not hasattr(self, "_visual_detail_text"):
            return
        moment = self._selected_visual()
        if moment is None:
            self._set_visual_preview(None, "尚无共享画面分析")
            self._replace_text(
                self._visual_detail_text,
                "选择 Teams/PPT 区域并开始后，稳定页面会显示在这里。",
            )
            self._replace_text(self._visual_answer_text, "")
            self._visual_ask_button.configure(state="disabled")
            return
        preview_bytes = moment.image_jpeg or moment.thumbnail_jpeg
        if preview_bytes:
            try:
                with Image.open(io.BytesIO(preview_bytes)) as image:
                    self._set_visual_preview(image.convert("RGB"), "")
            except (OSError, ValueError):
                self._set_visual_preview(None, "该页面预览图无法读取")
        else:
            self._set_visual_preview(None, "该页面缩略图已从内存释放")
        detail = (
            f"第 {moment.sequence} 页 · {moment.captured_at:%H:%M:%S}\n"
            f"{visual_analysis_text(moment.analysis)}"
        )
        self._replace_text(self._visual_detail_text, detail)
        answers = [
            (question, answer)
            for sequence, question, answer in self._visual_questions
            if sequence == moment.sequence
        ]
        answer_text = "\n\n".join(
            f"问：{question}\n答：{answer}" for question, answer in answers
        )
        self._replace_text(
            self._visual_answer_text,
            answer_text or "可在上方输入问题，结合本页画面和对应时段字幕回答。",
        )
        self._visual_ask_button.configure(
            state="disabled" if self._is_busy() else "normal"
        )

    def _set_visual_preview(
        self,
        image: Image.Image | None,
        message: str,
    ) -> None:
        self._visual_preview_image = image
        self._visual_preview_message = message
        self._draw_visual_preview()

    def _on_visual_preview_resize(self, _event: tk.Event) -> None:
        if self._visual_preview_after_id is not None:
            try:
                self._owner.after_cancel(self._visual_preview_after_id)
            except tk.TclError:
                pass
        self._visual_preview_after_id = self._owner.after(
            60,
            self._draw_visual_preview,
        )

    def _draw_visual_preview(self) -> None:
        self._visual_preview_after_id = None
        if not self.visible or not hasattr(self, "_visual_image_canvas"):
            return
        canvas = self._visual_image_canvas
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 2 or height <= 2:
            return
        canvas.delete("all")
        self._thumbnail_photo = None
        image = self._visual_preview_image
        if image is None:
            canvas.create_text(
                width // 2,
                height // 2,
                text=self._visual_preview_message,
                fill=ui.TEXT_MUTED,
                font=(self._fonts.ui, 10),
                width=max(1, width - ui.SPACE_4 * 2),
                justify="center",
            )
            return
        available_width = max(1, width - ui.SPACE_2 * 2)
        available_height = max(1, height - ui.SPACE_2 * 2)
        display_size = fit_image_size(
            image.width,
            image.height,
            available_width,
            available_height,
        )
        display_image = image.resize(display_size, Image.Resampling.LANCZOS)
        self._thumbnail_photo = ImageTk.PhotoImage(display_image)
        canvas.create_image(
            width // 2,
            height // 2,
            image=self._thumbnail_photo,
            anchor="center",
        )

    def _select_visual_region(self) -> None:
        if not self._capture_allowed():
            self._set_status("请先开始同传，再开启共享画面分析", error=True)
            return
        if self._selector and self._selector.visible:
            self._selector.show()
            return
        self._capture_generation += 1
        self._capture_state = "selecting"
        self._capture_inflight = False
        self._visual_scheduler.cancel()
        self._detector.reset()
        self._quick_overlay.hide()

        def selected(region: CaptureRegion) -> None:
            self._region = region
            self._selector = None
            self._capture_state = "running"
            self._next_capture_at = time.monotonic()
            registered = self._hotkey.start()
            suffix = "" if registered else "；全局快捷键注册失败，请使用界面按钮"
            self._visual_helper_var.set(
                f"正在分析 {region.width}×{region.height} 区域"
                f" · Ctrl+Alt+F9 暂停/恢复{suffix}"
            )
            self._set_status("共享画面分析已开始")
            if self._quick_overlay_var.get():
                self._quick_overlay.set_enabled(True)
            self._update_controls()

        def cancelled() -> None:
            self._selector = None
            self._capture_state = "stopped" if self._region is None else "paused"
            self._set_status("已取消选择共享画面区域")
            self._update_controls()

        self._selector = RegionSelector(self._owner, selected, cancelled)
        self._selector.show()

    def _toggle_visual_pause(self) -> None:
        if self._capture_state == "running":
            self._capture_state = "paused"
            self._quick_overlay.hide()
            self._set_status("共享画面分析已暂停")
        elif self._capture_state in {"paused", "paused_limit"} and self._region:
            if not self._capture_allowed():
                self._set_status("同传未运行，无法恢复共享画面分析", error=True)
                return
            self._capture_state = "running"
            self._next_capture_at = time.monotonic()
            if self._quick_overlay_var.get():
                self._quick_overlay.set_enabled(True)
            self._set_status("共享画面分析已恢复")
        self._update_controls()

    def _toggle_quick_overlay(self) -> None:
        enabled = bool(self._quick_overlay_var.get())
        if enabled and self._visual_snapshot:
            self._quick_overlay.update(self._visual_snapshot[-1].analysis)
        self._quick_overlay.set_enabled(enabled)

    def _capture_once(self, *, manual: bool) -> None:
        if self._region is None:
            self._set_status("请先选择共享画面区域", error=True)
            return
        if not self._capture_allowed():
            self._set_status("同传未运行，无法捕获共享画面", error=True)
            return
        if self._capture_inflight:
            if manual:
                self._set_status("正在采样当前画面，请稍候")
            return
        self._capture_inflight = True
        self._next_capture_at = time.monotonic() + 1.0
        self._quick_overlay.suspend_for_capture()
        generation = self._window_generation
        data_generation = self._data_generation
        visual_generation = self._capture_generation
        region = self._region

        def worker() -> None:
            try:
                image = self._grabber.capture(region)
                captured_at = datetime.now()
                self._messages.put(
                    _AssistantMessage(
                        generation,
                        data_generation,
                        "capture_sampled",
                        visual_generation=visual_generation,
                    )
                )
                prepared = prepare_image(image)
                payload: object = _CaptureResult(
                    _VisualCandidate(captured_at, prepared),
                    time.monotonic(),
                    manual,
                )
                kind = "capture_ready"
            except Exception as exc:
                payload = _VisualFailure(str(exc), type(exc).__name__)
                kind = "capture_failed"
            self._messages.put(
                _AssistantMessage(
                    generation,
                    data_generation,
                    kind,
                    payload,
                    visual_generation=visual_generation,
                )
            )

        threading.Thread(
            target=worker,
            name="shared-screen-capture",
            daemon=True,
        ).start()

    def _handle_capture_result(self, result: _CaptureResult) -> None:
        candidate = result.candidate
        if candidate.prepared.is_blank:
            if result.manual:
                self._set_status("当前画面接近空白，已跳过分析")
            return
        self._latest_candidate = candidate
        if result.manual:
            decision = self._visual_scheduler.offer(
                candidate,
                result.sampled_at,
                manual=True,
            )
            self._handle_schedule_decision(decision)
            return
        change = self._detector.observe(
            candidate.prepared.page_hash,
            result.sampled_at,
            is_blank=False,
        )
        if change is None:
            return
        first_seen = candidate.captured_at - timedelta(
            seconds=max(0.0, result.sampled_at - change.first_seen_at)
        )
        stable_candidate = _VisualCandidate(first_seen, candidate.prepared)
        decision = self._visual_scheduler.offer(stable_candidate, result.sampled_at)
        self._handle_schedule_decision(decision)

    def _handle_schedule_decision(self, decision: ScheduleDecision) -> None:
        if decision.action == "start":
            assert isinstance(decision.candidate, _VisualCandidate)
            self._start_visual_analysis(decision.candidate)
        elif decision.action == "rate_limited":
            self._capture_state = "paused_limit"
            self._quick_overlay.hide()
            self._visual_helper_var.set(
                "自动分析已达到每小时 60 次上限，已暂停；手动分析当前页仍可使用。"
            )
            self._set_status("共享画面自动分析已达到每小时上限", error=True)

    def _start_visual_analysis(self, candidate: _VisualCandidate) -> None:
        if self._vision_client_factory is None:
            self._set_status("共享画面 AI 尚未配置", error=True)
            self._handle_schedule_decision(
                self._visual_scheduler.complete(time.monotonic())
            )
            return
        try:
            client = self._vision_client_factory()
        except Exception as exc:
            self._set_status(f"共享画面 AI 配置无效：{exc}", error=True)
            self._handle_schedule_decision(
                self._visual_scheduler.complete(time.monotonic())
            )
            return
        generation = self._window_generation
        data_generation = self._data_generation
        visual_generation = self._capture_generation
        page_number = max(
            (item.sequence for item in self._visual_snapshot),
            default=0,
        ) + 1
        self._set_status(f"正在分析共享画面第 {page_number} 页…")
        self._log_visual("analysis_started", page_number, 0, "")

        def worker() -> None:
            started = time.monotonic()
            try:
                analysis = client.analyze(candidate.prepared.jpeg_bytes)
                payload: object = _VisionResult(
                    candidate,
                    analysis,
                    int((time.monotonic() - started) * 1_000),
                )
                kind = "visual_ready"
            except Exception as exc:
                payload = _VisualFailure(str(exc), type(exc).__name__)
                kind = "visual_failed"
            self._messages.put(
                _AssistantMessage(
                    generation,
                    data_generation,
                    kind,
                    payload,
                    visual_generation=visual_generation,
                )
            )

        threading.Thread(
            target=worker,
            name="shared-screen-analysis",
            daemon=True,
        ).start()

    def _handle_visual_ready(self, result: _VisionResult) -> None:
        sequence = max(
            (item.sequence for item in self._visual_snapshot),
            default=0,
        ) + 1
        moment = VisualMoment(
            sequence=sequence,
            captured_at=result.candidate.captured_at,
            page_hash=result.candidate.prepared.page_hash,
            analysis=result.analysis,
            image_jpeg=result.candidate.prepared.jpeg_bytes,
            thumbnail_jpeg=result.candidate.prepared.thumbnail_jpeg,
        )
        self._visual_appender(moment)
        self._visual_snapshot = tuple(self._visual_supplier())
        self._visual_usage = self._visual_usage + result.analysis.usage
        self._quick_overlay.update(result.analysis)
        self._render_visual_timeline(select_latest=True)
        self._set_status(
            f"共享画面第 {sequence} 页已分析 · {result.elapsed_ms / 1000:.1f} 秒"
        )
        self._log_visual("analysis_ready", sequence, result.elapsed_ms, "")
        self._handle_schedule_decision(
            self._visual_scheduler.complete(time.monotonic())
        )

    def _start_visual_question(self) -> None:
        moment = self._selected_visual()
        question = self._visual_question_var.get().strip()
        if moment is None:
            self._set_status("请先选择一个已分析页面", error=True)
            return
        if not question:
            self._set_status("请输入针对本页的问题", error=True)
            self._visual_question_entry.focus_set()
            return
        if self._is_busy():
            self._set_status("会议助手正在处理其他请求，请稍候")
            return
        try:
            client = self._client_factory()
        except Exception as exc:
            self._set_status(f"会议助手配置无效：{exc}", error=True)
            return
        task_id = self._begin_task()
        if task_id is None:
            return
        self._set_status(f"正在结合第 {moment.sequence} 页和对应字幕回答…")
        generation = self._window_generation
        data_generation = self._data_generation
        visual_generation = self._capture_generation
        turns = self._snapshot
        visual_context = format_visual_context((moment,), turns, query=question)

        def worker() -> None:
            try:
                result = client.answer(
                    question,
                    turns,
                    self._insight_markdown,
                    visual_context,
                )
                message = _AssistantMessage(
                    generation,
                    data_generation,
                    "visual_answer_ready",
                    result,
                    f"{moment.sequence}|{question}",
                    visual_generation,
                )
            except Exception as exc:
                message = _AssistantMessage(
                    generation,
                    data_generation,
                    "failed",
                    str(exc),
                    question,
                )
            finally:
                self._finish_task(task_id)
            self._messages.put(message)

        threading.Thread(
            target=worker,
            name="shared-screen-question",
            daemon=True,
        ).start()

    def _stop_visual_capture(self, message: str) -> None:
        self._capture_generation += 1
        self._capture_state = "stopped"
        self._capture_inflight = False
        self._region = None
        self._latest_candidate = None
        self._visual_scheduler.cancel()
        self._detector.reset()
        if self._selector:
            self._selector.destroy()
            self._selector = None
        self._hotkey.stop()
        self._quick_overlay.set_enabled(False)
        if hasattr(self, "_quick_overlay_var"):
            self._quick_overlay_var.set(False)
        if self.visible:
            self._visual_helper_var.set(
                "选择 Teams/PPT 共享区域后，每秒本地采样；稳定换页约 2 秒后分析。"
            )
            self._set_status(message)
            self._update_controls()

    def _log_visual(
        self,
        state: str,
        page: int,
        elapsed_ms: int,
        error_type: str,
    ) -> None:
        if self._visual_diagnostic:
            self._visual_diagnostic(state, page, elapsed_ms, error_type)

    def _update_controls(self) -> None:
        if not self.visible:
            return
        busy = self._is_busy()
        state = "disabled" if busy else "normal"
        self._refresh_button.configure(state=state)
        self._ask_button.configure(state=state)
        self._question_entry.configure(state=state)
        if hasattr(self, "_visual_start_button"):
            self._visual_start_button.configure(
                state=(
                    "normal"
                    if self._capture_state == "stopped"
                    and self._capture_allowed()
                    else "disabled"
                )
            )
            can_control = self._capture_state in {
                "running",
                "paused",
                "paused_limit",
            }
            self._visual_pause_button.configure(
                text=(
                    "恢复"
                    if self._capture_state in {"paused", "paused_limit"}
                    else "暂停"
                ),
                state="normal" if can_control else "disabled",
            )
            self._visual_reselect_button.configure(
                state="normal" if can_control else "disabled"
            )
            self._visual_stop_button.configure(
                state="normal" if can_control else "disabled"
            )
            self._visual_analyze_button.configure(
                state=(
                    "normal"
                    if self._region is not None
                    and not self._capture_inflight
                    and self._capture_allowed()
                    else "disabled"
                )
            )
            self._visual_ask_button.configure(
                state=(
                    "normal"
                    if not busy and self._selected_visual() is not None
                    else "disabled"
                )
            )
            visual_busy = self._visual_scheduler.busy
            if visual_busy and not self._visual_progress_active:
                self._visual_progress.pack(side="right", padx=(0, ui.SPACE_2))
                self._visual_progress.start(12)
                self._visual_progress_active = True
            elif not visual_busy and self._visual_progress_active:
                self._visual_progress.stop()
                self._visual_progress.pack_forget()
                self._visual_progress_active = False
        self._usage_var.set(
            "助手 Token："
            f"文本输入 {self._usage.input_tokens} / 文本输出 {self._usage.output_tokens}"
            " · 视觉输入 "
            f"{self._visual_usage.input_tokens} / 视觉输出 {self._visual_usage.output_tokens}"
        )

    def _on_auto_changed(self) -> None:
        self._auto_enabled = bool(self._auto_var.get())
        if self._auto_enabled:
            self._last_attempt_at = datetime.now()
            self._set_status("已开启每 5 分钟自动更新；仅有足够新内容时调用")
        else:
            self._set_status("已关闭自动更新")

    def _copy_selection(self, text: tk.Text) -> None:
        try:
            selected = text.get("sel.first", "sel.last")
        except tk.TclError:
            self._set_status("请先选择要复制的文字")
            return
        self._owner.clipboard_clear()
        self._owner.clipboard_append(selected)
        self._set_status("已复制选中文字")

    def _set_status(self, message: str, *, error: bool = False) -> None:
        if not self.visible:
            return
        self._status_var.set(message)
        self._status_label.configure(fg=ui.DANGER if error else ui.TEXT_MUTED)

    @staticmethod
    def _replace_text(widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if content:
            widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _is_busy(self) -> bool:
        with self._task_lock:
            return self._active_task_id is not None

    def _begin_task(self) -> int | None:
        with self._task_lock:
            if self._active_task_id is not None:
                return None
            self._next_task_id += 1
            self._active_task_id = self._next_task_id
            return self._active_task_id

    def _finish_task(self, task_id: int) -> None:
        with self._task_lock:
            if self._active_task_id == task_id:
                self._active_task_id = None

    def _invalidate_active_task(self) -> None:
        with self._task_lock:
            self._active_task_id = None

    def _close_window(self) -> None:
        self._window_generation += 1
        self._auto_enabled = False
        self._pending_question = None
        self._invalidate_active_task()
        self._stop_visual_capture("共享画面分析已停止")
        self._quick_overlay.destroy()
        if self._after_id is not None:
            try:
                self._owner.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._visual_preview_after_id is not None:
            try:
                self._owner.after_cancel(self._visual_preview_after_id)
            except tk.TclError:
                pass
            self._visual_preview_after_id = None
        self._visual_preview_image = None
        self._thumbnail_photo = None
        window = self._window
        self._window = None
        if window and window.winfo_exists():
            window.destroy()
