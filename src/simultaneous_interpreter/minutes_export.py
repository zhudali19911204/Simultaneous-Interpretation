from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .visual_analysis import (
    MAX_KEY_SCREENSHOTS,
    VisualMoment,
    select_key_visual_moments,
)


@dataclass(frozen=True)
class MinutesExportResult:
    markdown_path: Path
    assets_dir: Path | None
    image_count: int
    skipped_count: int


_SHARED_SCREEN_HEADING = re.compile(
    r"^#{1,6}[ \t]*共享画面要点[^\r\n]*$",
    re.MULTILINE,
)
_MARKDOWN_HEADING = re.compile(r"^#{1,6}[ \t]+", re.MULTILINE)
_PAGE_REFERENCE = re.compile(r"第\s*(\d+)\s*页")


def referenced_visual_page_numbers(markdown: str) -> tuple[int, ...]:
    """Return page numbers explicitly referenced by shared-screen highlights."""

    heading = _SHARED_SCREEN_HEADING.search(markdown)
    if heading is None:
        return ()
    section_start = heading.end()
    next_heading = _MARKDOWN_HEADING.search(markdown, section_start)
    section_end = next_heading.start() if next_heading is not None else len(markdown)
    seen: set[int] = set()
    page_numbers: list[int] = []
    for match in _PAGE_REFERENCE.finditer(markdown, section_start, section_end):
        sequence = int(match.group(1))
        if sequence not in seen:
            seen.add(sequence)
            page_numbers.append(sequence)
    return tuple(page_numbers)


def select_referenced_visual_moments(
    markdown: str,
    moments: Iterable[VisualMoment],
    *,
    limit: int = MAX_KEY_SCREENSHOTS,
) -> tuple[VisualMoment, ...]:
    """Select only candidate pages named in the generated visual highlights."""

    if limit <= 0:
        return ()
    referenced = set(referenced_visual_page_numbers(markdown))
    selected = [item for item in moments if item.sequence in referenced]
    return tuple(
        sorted(selected, key=lambda item: (item.captured_at, item.sequence))[:limit]
    )


def _single_line_title(title: str) -> str:
    return " ".join(title.split()) or "未命名页面"


def _markdown_alt_text(moment: VisualMoment) -> str:
    title = _single_line_title(moment.analysis.title)
    title = (
        title.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    return f"第{moment.sequence}页：{title}"


def _next_assets_dir(markdown_path: Path) -> Path:
    base = markdown_path.with_name(f"{markdown_path.stem}_assets")
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}_{suffix}")
        suffix += 1
    return candidate


def _image_filename(moment: VisualMoment) -> str:
    return (
        f"page_{moment.sequence:03d}_"
        f"{moment.captured_at:%H%M%S}.jpg"
    )


def _gallery_markdown(
    selected: tuple[VisualMoment, ...],
    assets_dir: Path | None,
) -> tuple[str, int, int]:
    lines = ["## 共享画面关键截图", ""]
    image_count = 0
    skipped_count = 0
    for moment in selected:
        title = _single_line_title(moment.analysis.title)
        lines.append(
            f"### 第 {moment.sequence} 页 · "
            f"{moment.captured_at:%H:%M:%S} · {title}"
        )
        image_bytes = moment.image_jpeg or moment.thumbnail_jpeg
        if image_bytes and assets_dir is not None:
            filename = _image_filename(moment)
            lines.append(
                f"![{_markdown_alt_text(moment)}]"
                f"(<{assets_dir.name}/{filename}>)"
            )
            image_count += 1
        else:
            lines.append("> 截图已从内存释放，仅保留页面分析。")
            skipped_count += 1
        lines.append("")
    if skipped_count:
        lines.append(
            f"> 注：另有 {skipped_count} 个关键页面的图片已从内存释放，"
            "未写入附件目录。"
        )
    return "\n".join(lines).rstrip(), image_count, skipped_count


def export_minutes_with_assets(
    markdown: str,
    moments: Iterable[VisualMoment],
    markdown_path: Path,
    *,
    limit: int = MAX_KEY_SCREENSHOTS,
) -> MinutesExportResult:
    target = Path(markdown_path)
    selected = select_key_visual_moments(moments, limit=limit)
    exportable = tuple(
        item for item in selected if item.image_jpeg or item.thumbnail_jpeg
    )
    assets_dir = _next_assets_dir(target) if exportable else None
    try:
        if assets_dir is not None:
            assets_dir.mkdir()
            for moment in exportable:
                image_bytes = moment.image_jpeg or moment.thumbnail_jpeg
                assert image_bytes is not None
                (assets_dir / _image_filename(moment)).write_bytes(image_bytes)

        image_count = 0
        skipped_count = 0
        if selected:
            gallery, image_count, skipped_count = _gallery_markdown(
                selected,
                assets_dir,
            )
            final_markdown = f"{markdown.rstrip()}\n\n{gallery}\n".lstrip()
        else:
            final_markdown = markdown
        target.write_text(final_markdown, encoding="utf-8")
    except OSError:
        if assets_dir is not None and assets_dir.exists():
            shutil.rmtree(assets_dir, ignore_errors=True)
        raise
    return MinutesExportResult(
        markdown_path=target,
        assets_dir=assets_dir,
        image_count=image_count,
        skipped_count=skipped_count,
    )
