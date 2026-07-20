from __future__ import annotations

import ctypes
import os
import threading
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

import mss
from PIL import Image

from . import ui_theme as ui
from .visual_analysis import VisualAnalysis


@dataclass(frozen=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("截图区域宽度和高度必须大于 0")


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def virtual_screen_bounds() -> CaptureRegion:
    if os.name == "nt":
        user32 = ctypes.windll.user32
        return CaptureRegion(
            int(user32.GetSystemMetrics(76)),
            int(user32.GetSystemMetrics(77)),
            int(user32.GetSystemMetrics(78)),
            int(user32.GetSystemMetrics(79)),
        )
    with mss.mss() as capture:
        monitor = capture.monitors[0]
        return CaptureRegion(
            int(monitor["left"]),
            int(monitor["top"]),
            int(monitor["width"]),
            int(monitor["height"]),
        )


class ScreenGrabber:
    def capture(self, region: CaptureRegion) -> Image.Image:
        monitor = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }
        with mss.mss() as capture:
            shot = capture.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.rgb)


class RegionSelector:
    MIN_SIZE = 64

    def __init__(
        self,
        owner: tk.Misc,
        on_selected: Callable[[CaptureRegion], None],
        on_cancelled: Callable[[], None] | None = None,
    ) -> None:
        self._owner = owner
        self._on_selected = on_selected
        self._on_cancelled = on_cancelled
        self._window: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._bounds = virtual_screen_bounds()
        self._start: tuple[int, int] | None = None
        self._rectangle: int | None = None

    @property
    def visible(self) -> bool:
        return bool(self._window and self._window.winfo_exists())

    def show(self) -> None:
        if self.visible:
            assert self._window is not None
            self._window.lift()
            return
        window = tk.Toplevel(self._owner)
        self._window = window
        window.overrideredirect(True)
        window.configure(bg=ui.BACKGROUND)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.42)
        window.geometry(
            f"{self._bounds.width}x{self._bounds.height}+0+0"
        )
        window.update_idletasks()
        self._place_window(window)
        canvas = tk.Canvas(
            window,
            bg=ui.BACKGROUND,
            cursor="crosshair",
            highlightthickness=0,
        )
        self._canvas = canvas
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            self._bounds.width // 2,
            56,
            text="拖动鼠标框选 Teams 或 PPT 共享画面区域 · Esc 取消",
            fill=ui.TEXT_PRIMARY,
            font=("Microsoft YaHei UI", 16, "bold"),
        )
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        window.bind("<Escape>", lambda _event: self.cancel())
        window.focus_force()
        try:
            window.grab_set()
        except tk.TclError:
            pass

    def cancel(self) -> None:
        callback = self._on_cancelled
        self.destroy()
        if callback:
            callback()

    def destroy(self) -> None:
        window = self._window
        self._window = None
        self._canvas = None
        if window and window.winfo_exists():
            try:
                window.grab_release()
            except tk.TclError:
                pass
            window.destroy()

    def _place_window(self, window: tk.Toplevel) -> None:
        if os.name != "nt":
            window.geometry(
                f"{self._bounds.width}x{self._bounds.height}"
                f"+{self._bounds.left}+{self._bounds.top}"
            )
            return
        hwnd = window.winfo_id()
        set_window_pos = ctypes.windll.user32.SetWindowPos
        set_window_pos.argtypes = (
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        set_window_pos.restype = wintypes.BOOL
        set_window_pos(
            wintypes.HWND(hwnd),
            wintypes.HWND(-1),
            self._bounds.left,
            self._bounds.top,
            self._bounds.width,
            self._bounds.height,
            0x0010,
        )

    def _on_press(self, event: tk.Event) -> None:
        self._start = (int(event.x), int(event.y))
        if self._rectangle is not None and self._canvas:
            self._canvas.delete(self._rectangle)
        if self._canvas:
            self._rectangle = self._canvas.create_rectangle(
                event.x,
                event.y,
                event.x,
                event.y,
                outline=ui.INFO,
                width=3,
            )

    def _on_drag(self, event: tk.Event) -> None:
        if not self._start or self._rectangle is None or not self._canvas:
            return
        self._canvas.coords(
            self._rectangle,
            self._start[0],
            self._start[1],
            event.x,
            event.y,
        )

    def _on_release(self, event: tk.Event) -> None:
        if not self._start:
            return
        left = max(0, min(self._start[0], int(event.x)))
        top = max(0, min(self._start[1], int(event.y)))
        right = min(self._bounds.width, max(self._start[0], int(event.x)))
        bottom = min(self._bounds.height, max(self._start[1], int(event.y)))
        width = right - left
        height = bottom - top
        if width < self.MIN_SIZE or height < self.MIN_SIZE:
            self._start = None
            if self._rectangle is not None and self._canvas:
                self._canvas.delete(self._rectangle)
                self._rectangle = None
            return
        region = CaptureRegion(
            left=self._bounds.left + left,
            top=self._bounds.top + top,
            width=width,
            height=height,
        )
        callback = self._on_selected
        self.destroy()
        callback(region)


class GlobalPauseHotkey:
    HOTKEY_ID = 0x5149
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    VK_F9 = 0x78

    def __init__(self) -> None:
        self._triggered = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._registered = False
        self._thread_id = 0
        self._thread: threading.Thread | None = None

    @property
    def registered(self) -> bool:
        return self._registered

    def start(self) -> bool:
        if os.name != "nt":
            return False
        if self._thread and self._thread.is_alive():
            return self._registered
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._message_loop,
            name="visual-hotkey",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=1.0)
        return self._registered

    def consume(self) -> bool:
        if not self._triggered.is_set():
            return False
        self._triggered.clear()
        return True

    def stop(self) -> None:
        self._stop.set()
        if os.name == "nt" and self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    self._thread_id,
                    self.WM_QUIT,
                    0,
                    0,
                )
            except (AttributeError, OSError):
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = 0
        self._registered = False
        self._triggered.clear()

    def _message_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._registered = bool(
            user32.RegisterHotKey(
                None,
                self.HOTKEY_ID,
                self.MOD_CONTROL | self.MOD_ALT,
                self.VK_F9,
            )
        )
        self._ready.set()
        if not self._registered:
            return
        message = wintypes.MSG()
        try:
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == self.WM_HOTKEY and message.wParam == self.HOTKEY_ID:
                    self._triggered.set()
        finally:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)
            self._registered = False


class QuickSummaryOverlay:
    def __init__(self, owner: tk.Misc, fonts: ui.ThemeFonts) -> None:
        self._owner = owner
        self._fonts = fonts
        self._window: tk.Toplevel | None = None
        self._enabled = False
        self._suspended = False
        self._text = "等待共享画面分析…"
        self._drag_origin: tuple[int, int] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            self._ensure_window()
            self._render()
            if not self._suspended:
                assert self._window is not None
                self._window.deiconify()
                self._window.lift()
        else:
            self.hide()

    def update(self, analysis: VisualAnalysis) -> None:
        lines = [analysis.title]
        lines.extend(f"• {item}" for item in analysis.summary_points[:3])
        if analysis.metrics:
            lines.append("关键数字：" + "；".join(analysis.metrics[:4]))
        self._text = "\n".join(lines)
        if self._enabled:
            self._ensure_window()
            self._render()

    def suspend_for_capture(self) -> None:
        self._suspended = True
        if self._window and self._window.winfo_exists():
            self._window.withdraw()

    def resume_after_capture(self) -> None:
        self._suspended = False
        if self._enabled and self._window and self._window.winfo_exists():
            self._window.deiconify()
            self._window.lift()

    def hide(self) -> None:
        if self._window and self._window.winfo_exists():
            self._window.withdraw()

    def destroy(self) -> None:
        window = self._window
        self._window = None
        self._enabled = False
        self._suspended = False
        if window and window.winfo_exists():
            window.destroy()

    def _ensure_window(self) -> None:
        if self._window and self._window.winfo_exists():
            return
        window = tk.Toplevel(self._owner)
        self._window = window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=ui.INFO)
        frame = tk.Frame(window, bg=ui.SURFACE, padx=16, pady=12)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        self._label = tk.Label(
            frame,
            text=self._text,
            bg=ui.SURFACE,
            fg=ui.TEXT_PRIMARY,
            justify="left",
            anchor="w",
            font=(self._fonts.transcript, 12),
            wraplength=720,
        )
        self._label.pack(fill="both", expand=True)
        for widget in (window, frame, self._label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
        window.update_idletasks()
        width = min(780, max(520, window.winfo_reqwidth()))
        height = max(120, window.winfo_reqheight())
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, window.winfo_screenheight() - height - 72)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _render(self) -> None:
        if hasattr(self, "_label"):
            self._label.configure(text=self._text)

    def _start_drag(self, event: tk.Event) -> None:
        if self._window:
            self._drag_origin = (
                int(event.x_root) - self._window.winfo_x(),
                int(event.y_root) - self._window.winfo_y(),
            )

    def _drag(self, event: tk.Event) -> None:
        if not self._window or not self._drag_origin:
            return
        self._window.geometry(
            f"+{int(event.x_root) - self._drag_origin[0]}"
            f"+{int(event.y_root) - self._drag_origin[1]}"
        )
