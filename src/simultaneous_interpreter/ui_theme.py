from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk


BACKGROUND = "#08111F"
SURFACE = "#0F1B2D"
SURFACE_ELEVATED = "#16243A"
TEXT_SURFACE = "#0B1525"
BORDER = "#2B3A52"
PRIMARY = "#2563EB"
PRIMARY_HOVER = "#1D4ED8"
PRIMARY_PRESSED = "#1E40AF"
INFO = "#22D3EE"
FOCUS = "#60A5FA"
TEXT_PRIMARY = "#F8FAFC"
TEXT_SECONDARY = "#CBD5E1"
TEXT_MUTED = "#94A3B8"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#F87171"
DANGER_SURFACE = "#7F1D1D"
DANGER_HOVER = "#991B1B"
DISABLED = "#64748B"
SELECTION = "#1D4ED8"

SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_6 = 24


@dataclass(frozen=True)
class ThemeFonts:
    ui: str
    transcript: str


@dataclass(frozen=True)
class MarkdownBlock:
    kind: str
    text: str


STATUS_STYLES = {
    "idle": "Idle.Status.TLabel",
    "info": "Info.Status.TLabel",
    "busy": "Busy.Status.TLabel",
    "running": "Running.Status.TLabel",
    "warning": "Warning.Status.TLabel",
    "error": "Error.Status.TLabel",
}


def status_style_for(kind: str) -> str:
    return STATUS_STYLES.get(kind, STATUS_STYLES["idle"])


def parse_markdown_blocks(markdown: str) -> tuple[MarkdownBlock, ...]:
    blocks: list[MarkdownBlock] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            blocks.append(MarkdownBlock("blank", ""))
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            blocks.append(
                MarkdownBlock(f"h{len(heading.group(1))}", heading.group(2).strip())
            )
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            blocks.append(MarkdownBlock("bullet", bullet.group(1).strip()))
            continue
        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if numbered:
            blocks.append(
                MarkdownBlock(
                    "numbered",
                    f"{numbered.group(1)}. {numbered.group(2).strip()}",
                )
            )
            continue
        blocks.append(MarkdownBlock("body", stripped))
    return tuple(blocks)


def _pick_font(root: tk.Misc, *candidates: str) -> str:
    installed = {name.casefold(): name for name in tkfont.families(root)}
    for candidate in candidates:
        match = installed.get(candidate.casefold())
        if match:
            return match
    return candidates[-1]


def configure_theme(root: tk.Misc) -> ThemeFonts:
    ui_font = _pick_font(root, "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI")
    transcript_font = _pick_font(root, "Microsoft YaHei UI", "Microsoft YaHei", ui_font)
    fonts = ThemeFonts(ui=ui_font, transcript=transcript_font)

    if isinstance(root, (tk.Tk, tk.Toplevel)):
        root.configure(bg=BACKGROUND)

    root.option_add("*TCombobox*Listbox.background", SURFACE_ELEVATED)
    root.option_add("*TCombobox*Listbox.foreground", TEXT_PRIMARY)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECTION)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=(fonts.ui, 10))
    style.configure("TFrame", background=BACKGROUND)
    style.configure("Background.TFrame", background=BACKGROUND)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("Elevated.TFrame", background=SURFACE_ELEVATED)
    style.configure(
        "Card.TFrame",
        background=SURFACE,
        bordercolor=BORDER,
        relief="solid",
        borderwidth=1,
    )

    style.configure(
        "TLabel",
        background=BACKGROUND,
        foreground=TEXT_SECONDARY,
        font=(fonts.ui, 10),
    )
    style.configure(
        "Title.TLabel",
        background=BACKGROUND,
        foreground=TEXT_PRIMARY,
        font=(fonts.ui, 20, "bold"),
    )
    style.configure(
        "Subtitle.TLabel",
        background=BACKGROUND,
        foreground=TEXT_MUTED,
        font=(fonts.ui, 10),
    )
    style.configure(
        "Surface.TLabel",
        background=SURFACE,
        foreground=TEXT_SECONDARY,
        font=(fonts.ui, 10),
    )
    style.configure(
        "SurfaceTitle.TLabel",
        background=SURFACE,
        foreground=TEXT_PRIMARY,
        font=(fonts.ui, 11, "bold"),
    )
    style.configure(
        "Helper.TLabel",
        background=SURFACE,
        foreground=TEXT_MUTED,
        font=(fonts.ui, 9),
    )
    style.configure(
        "CardTitle.TLabel",
        background=SURFACE,
        foreground=TEXT_PRIMARY,
        font=(fonts.ui, 12, "bold"),
    )
    style.configure(
        "Interim.TLabel",
        background=SURFACE,
        foreground=INFO,
        font=(fonts.transcript, 15, "bold"),
    )
    style.configure(
        "Incoming.Role.TLabel",
        background="#0C4A6E",
        foreground="#E0F2FE",
        font=(fonts.ui, 9, "bold"),
        padding=(8, 3),
    )
    style.configure(
        "Outgoing.Role.TLabel",
        background="#3730A3",
        foreground="#EEF2FF",
        font=(fonts.ui, 9, "bold"),
        padding=(8, 3),
    )
    style.configure(
        "ErrorBanner.TLabel",
        background="#3F1D2B",
        foreground="#FECACA",
        bordercolor=DANGER,
        relief="solid",
        borderwidth=1,
        padding=(12, 8),
        font=(fonts.ui, 10),
    )

    for name, color in (
        ("Idle", TEXT_MUTED),
        ("Info", INFO),
        ("Busy", WARNING),
        ("Running", SUCCESS),
        ("Warning", WARNING),
        ("Error", DANGER),
    ):
        style.configure(
            f"{name}.Status.TLabel",
            background=SURFACE_ELEVATED,
            foreground=color,
            bordercolor=BORDER,
            relief="solid",
            borderwidth=1,
            padding=(10, 5),
            font=(fonts.ui, 9, "bold"),
        )

    style.configure(
        "TButton",
        background=SURFACE_ELEVATED,
        foreground=TEXT_PRIMARY,
        bordercolor=BORDER,
        lightcolor=SURFACE_ELEVATED,
        darkcolor=SURFACE_ELEVATED,
        focuscolor=FOCUS,
        relief="flat",
        borderwidth=1,
        padding=(12, 8),
        font=(fonts.ui, 10),
    )
    style.map(
        "TButton",
        background=[("pressed", "#22324D"), ("active", "#1E2D46"), ("disabled", SURFACE)],
        foreground=[("disabled", DISABLED)],
        bordercolor=[("focus", FOCUS), ("active", FOCUS), ("disabled", BORDER)],
    )
    style.configure(
        "Primary.TButton",
        background=PRIMARY,
        foreground="#FFFFFF",
        bordercolor=PRIMARY,
        lightcolor=PRIMARY,
        darkcolor=PRIMARY,
        font=(fonts.ui, 10, "bold"),
        padding=(18, 9),
    )
    style.map(
        "Primary.TButton",
        background=[("pressed", PRIMARY_PRESSED), ("active", PRIMARY_HOVER), ("disabled", "#1E3A5F")],
        foreground=[("disabled", TEXT_MUTED)],
        bordercolor=[("focus", FOCUS), ("active", PRIMARY_HOVER)],
    )
    style.configure(
        "Danger.TButton",
        background=DANGER_SURFACE,
        foreground="#FFFFFF",
        bordercolor=DANGER,
        lightcolor=DANGER_SURFACE,
        darkcolor=DANGER_SURFACE,
        font=(fonts.ui, 10, "bold"),
    )
    style.map(
        "Danger.TButton",
        background=[("pressed", "#6B1720"), ("active", DANGER_HOVER), ("disabled", SURFACE)],
        foreground=[("disabled", DISABLED)],
        bordercolor=[("focus", DANGER), ("disabled", BORDER)],
    )
    style.configure(
        "Quiet.TButton",
        background=BACKGROUND,
        foreground=TEXT_SECONDARY,
        bordercolor=BORDER,
        lightcolor=BACKGROUND,
        darkcolor=BACKGROUND,
    )

    style.configure(
        "TEntry",
        fieldbackground=SURFACE_ELEVATED,
        foreground=TEXT_PRIMARY,
        insertcolor=TEXT_PRIMARY,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=(9, 8),
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", SURFACE), ("readonly", SURFACE)],
        foreground=[("disabled", DISABLED), ("readonly", TEXT_MUTED)],
        bordercolor=[("focus", FOCUS), ("invalid", DANGER)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=SURFACE_ELEVATED,
        background=SURFACE_ELEVATED,
        foreground=TEXT_PRIMARY,
        arrowcolor=TEXT_SECONDARY,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=(8, 7),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE_ELEVATED), ("disabled", SURFACE)],
        foreground=[("readonly", TEXT_PRIMARY), ("disabled", DISABLED)],
        bordercolor=[("focus", FOCUS), ("invalid", DANGER)],
        arrowcolor=[("disabled", DISABLED)],
    )
    style.configure(
        "TCheckbutton",
        background=BACKGROUND,
        foreground=TEXT_SECONDARY,
        focuscolor=FOCUS,
        padding=(2, 4),
    )
    style.map(
        "TCheckbutton",
        background=[("active", BACKGROUND), ("disabled", BACKGROUND)],
        foreground=[("active", TEXT_PRIMARY), ("disabled", DISABLED)],
        indicatorcolor=[("selected", PRIMARY), ("!selected", SURFACE_ELEVATED)],
    )
    style.configure(
        "Surface.TCheckbutton",
        background=SURFACE,
        foreground=TEXT_SECONDARY,
        focuscolor=FOCUS,
    )
    style.map(
        "Surface.TCheckbutton",
        background=[("active", SURFACE), ("disabled", SURFACE)],
        foreground=[("active", TEXT_PRIMARY), ("disabled", DISABLED)],
        indicatorcolor=[("selected", PRIMARY), ("!selected", SURFACE_ELEVATED)],
    )

    style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=SURFACE,
        foreground=TEXT_MUTED,
        bordercolor=BORDER,
        padding=(18, 9),
        font=(fonts.ui, 10, "bold"),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE_ELEVATED), ("active", "#13223A")],
        foreground=[("selected", TEXT_PRIMARY), ("active", TEXT_SECONDARY)],
        bordercolor=[("selected", FOCUS)],
    )
    style.configure(
        "App.Horizontal.TProgressbar",
        troughcolor=SURFACE,
        background=INFO,
        bordercolor=SURFACE,
        lightcolor=INFO,
        darkcolor=INFO,
        thickness=3,
    )
    style.configure("TSeparator", background=BORDER)
    return fonts
