from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import ttk
from typing import Callable

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


@dataclass(frozen=True)
class _AssistantMessage:
    window_generation: int
    data_generation: int
    kind: str
    payload: object = None
    question: str = ""


class MeetingAssistantWindow:
    POLL_MS = 200

    def __init__(
        self,
        owner: tk.Tk,
        fonts: ui.ThemeFonts,
        turn_supplier: Callable[[], tuple[MeetingTurn, ...]],
        client_factory: Callable[[], MeetingAssistantClient],
    ) -> None:
        self._owner = owner
        self._fonts = fonts
        self._turn_supplier = turn_supplier
        self._client_factory = client_factory
        self._window: tk.Toplevel | None = None
        self._after_id: str | None = None
        self._window_generation = 0
        self._data_generation = 0
        self._messages: queue.Queue[_AssistantMessage] = queue.Queue()
        self._task_lock = threading.Lock()
        self._next_task_id = 0
        self._active_task_id: int | None = None
        self._snapshot: tuple[MeetingTurn, ...] = ()
        self._insight_markdown = ""
        self._processed_turn_count = 0
        self._last_attempt_at: datetime | None = None
        self._usage = AssistantUsage()
        self._questions: list[tuple[str, str]] = []
        self._auto_enabled = False
        self._pending_question: str | None = None

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

    def _create_window(self) -> None:
        window = tk.Toplevel(self._owner)
        self._window = window
        window.title("AI 会议理解助手")
        window.geometry("920x700")
        window.minsize(760, 560)
        window.configure(bg=ui.BACKGROUND)
        window.protocol("WM_DELETE_WINDOW", self._close_window)

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
        notebook.add(insight_tab, text="实时重点")
        notebook.add(question_tab, text="会中问答")
        notebook.add(timeline_tab, text="完整时间线")
        self._build_insight_tab(insight_tab)
        self._build_question_tab(question_tab)
        self._build_timeline_tab(timeline_tab)

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

    def _reset_analysis_state(self) -> None:
        self._data_generation += 1
        self._insight_markdown = ""
        self._processed_turn_count = 0
        self._last_attempt_at = None
        self._usage = AssistantUsage()
        self._questions.clear()
        self._pending_question = None
        self._invalidate_active_task()
        if self.visible:
            self._render_all()

    def _start_insight(self, *, manual: bool) -> None:
        if self._is_busy():
            if manual:
                self._set_status("会议助手正在处理其他请求，请稍候")
            return
        if not self._snapshot:
            self._set_status("当前没有可整理的会议记录", error=True)
            return
        if self._processed_turn_count >= len(self._snapshot):
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

        def worker() -> None:
            try:
                result = client.update_insight(turns, previous, processed)
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
        if not self._snapshot:
            self._set_status("当前没有可用于回答的会议记录", error=True)
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

        def worker() -> None:
            try:
                result = client.answer(question, turns, insight)
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

    def _update_controls(self) -> None:
        if not self.visible:
            return
        busy = self._is_busy()
        state = "disabled" if busy else "normal"
        self._refresh_button.configure(state=state)
        self._ask_button.configure(state=state)
        self._question_entry.configure(state=state)
        self._usage_var.set(
            "助手 Token："
            f"输入 {self._usage.input_tokens} / 输出 {self._usage.output_tokens}"
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
        if self._after_id is not None:
            try:
                self._owner.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        window = self._window
        self._window = None
        if window and window.winfo_exists():
            window.destroy()
