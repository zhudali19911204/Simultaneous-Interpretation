from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.minutes_export import (  # noqa: E402
    export_minutes_with_assets,
    referenced_visual_page_numbers,
    select_referenced_visual_moments,
)
from simultaneous_interpreter.visual_analysis import (  # noqa: E402
    VisualAnalysis,
    VisualMoment,
    select_key_visual_moments,
    trim_visual_media,
    visual_key_score,
)


BASE_TIME = datetime(2026, 7, 20, 9, 0, 0)


def _moment(
    sequence: int,
    *,
    analysis: VisualAnalysis | None = None,
    full: bytes | None = b"full-jpeg",
    thumbnail: bytes | None = b"thumbnail-jpeg",
) -> VisualMoment:
    return VisualMoment(
        sequence=sequence,
        captured_at=BASE_TIME + timedelta(minutes=sequence),
        page_hash=sequence,
        analysis=analysis or VisualAnalysis(
            title=f"第 {sequence} 页",
            summary_points=("摘要",),
        ),
        image_jpeg=full,
        thumbnail_jpeg=thumbnail,
    )


class KeyVisualSelectionTests(unittest.TestCase):
    def test_score_uses_each_key_category_once(self) -> None:
        moment = _moment(
            1,
            analysis=VisualAnalysis(
                title="项目看板",
                summary_points=("一", "二"),
                metrics=("10%", "20%"),
                action_items=("跟进", "复核"),
                risks=("延期",),
                open_questions=("预算？",),
            ),
        )
        self.assertEqual(visual_key_score(moment), 17)

    def test_selection_limits_to_latest_twenty_when_scores_tie(self) -> None:
        selected = select_key_visual_moments(
            tuple(_moment(index) for index in range(1, 26))
        )
        self.assertEqual(len(selected), 20)
        self.assertEqual([item.sequence for item in selected], list(range(6, 26)))

    def test_selection_prefers_score_then_returns_chronological_order(self) -> None:
        action = VisualAnalysis(title="行动", action_items=("交付",))
        metric_and_risk = VisualAnalysis(
            title="指标风险",
            metrics=("增长 10%",),
            risks=("延期",),
        )
        selected = select_key_visual_moments(
            (_moment(1), _moment(2, analysis=action), _moment(3, analysis=metric_and_risk)),
            limit=2,
        )
        self.assertEqual([item.sequence for item in selected], [2, 3])

    def test_no_score_falls_back_to_latest_page_with_media(self) -> None:
        empty = VisualAnalysis(title="空页面")
        selected = select_key_visual_moments(
            (
                _moment(1, analysis=empty, full=None, thumbnail=None),
                _moment(2, analysis=empty),
                _moment(3, analysis=empty, full=None, thumbnail=None),
            )
        )
        self.assertEqual([item.sequence for item in selected], [2])

    def test_trim_keeps_early_key_page_within_existing_limits(self) -> None:
        moments = [
            _moment(
                1,
                analysis=VisualAnalysis(title="早期行动项", action_items=("跟进",)),
            )
        ] + [_moment(index) for index in range(2, 26)]
        trimmed = trim_visual_media(moments, full_limit=5, thumbnail_limit=10)
        full_sequences = [item.sequence for item in trimmed if item.image_jpeg]
        thumbnail_sequences = [
            item.sequence for item in trimmed if item.thumbnail_jpeg
        ]
        self.assertEqual(full_sequences, [1, 22, 23, 24, 25])
        self.assertEqual(thumbnail_sequences, list(range(16, 26)))


class MinutesExportTests(unittest.TestCase):
    def test_referenced_pages_only_come_from_shared_screen_section(self) -> None:
        markdown = """# 会议纪要
## 关键讨论
- 第 99 页只是讨论中的普通文字
## 共享画面要点
- 第11页（15:57:34）：关键数字
- 第 3 页（15:20:00）：风险
- 第11页：重复引用
## 风险与未决问题
- 第 88 页不是画面要点
"""
        self.assertEqual(referenced_visual_page_numbers(markdown), (11, 3))

    def test_selects_only_referenced_candidate_pages(self) -> None:
        markdown = """# 会议纪要
## 共享画面要点（重要页面）
- 第4页（09:04:00）：行动项
- 第1页（09:01:00）：关键数字
- 第9页（09:09:00）：模型误写的非候选页
"""
        selected = select_referenced_visual_moments(
            markdown,
            (_moment(1), _moment(2), _moment(4)),
        )
        self.assertEqual([item.sequence for item in selected], [1, 4])

    def test_no_explicit_page_reference_selects_no_screenshot(self) -> None:
        selected = select_referenced_visual_moments(
            "## 共享画面要点\n- 未明确",
            (_moment(1),),
        )
        self.assertEqual(selected, ())

    def test_export_writes_relative_links_full_image_and_thumbnail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "会议 资料"
            folder.mkdir()
            target = folder / "会议 纪要.md"
            moments = (
                _moment(1, full=b"full-one", thumbnail=b"thumb-one"),
                _moment(2, full=None, thumbnail=b"thumb-two"),
            )
            result = export_minutes_with_assets("# 会议纪要", moments, target)

            self.assertEqual(result.image_count, 2)
            self.assertEqual(result.skipped_count, 0)
            self.assertIsNotNone(result.assets_dir)
            assert result.assets_dir is not None
            self.assertEqual(
                (result.assets_dir / "page_001_090100.jpg").read_bytes(),
                b"full-one",
            )
            self.assertEqual(
                (result.assets_dir / "page_002_090200.jpg").read_bytes(),
                b"thumb-two",
            )
            markdown = target.read_text(encoding="utf-8")
            self.assertIn("## 共享画面关键截图", markdown)
            self.assertIn("### 第 1 页 · 09:01:00 · 第 1 页", markdown)
            self.assertIn(
                "(<会议 纪要_assets/page_001_090100.jpg>)",
                markdown,
            )

    def test_existing_assets_directory_uses_numbered_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "minutes.md"
            (Path(directory) / "minutes_assets").mkdir()
            result = export_minutes_with_assets("# Minutes", (_moment(1),), target)
            assert result.assets_dir is not None
            self.assertEqual(result.assets_dir.name, "minutes_assets_2")

    def test_missing_image_keeps_text_note_without_assets_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "minutes.md"
            result = export_minutes_with_assets(
                "# Minutes",
                (_moment(1, full=None, thumbnail=None),),
                target,
            )
            self.assertIsNone(result.assets_dir)
            self.assertEqual(result.image_count, 0)
            self.assertEqual(result.skipped_count, 1)
            self.assertIn("截图已从内存释放", target.read_text(encoding="utf-8"))

    def test_no_visuals_preserves_original_markdown_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "minutes.md"
            markdown = "# Minutes\n原始内容"
            result = export_minutes_with_assets(markdown, (), target)
            self.assertIsNone(result.assets_dir)
            self.assertEqual(target.read_text(encoding="utf-8"), markdown)

    def test_failed_markdown_write_removes_new_assets_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "minutes.md"
            with patch.object(Path, "write_text", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    export_minutes_with_assets("# Minutes", (_moment(1),), target)
            self.assertFalse(Path(directory, "minutes_assets").exists())


if __name__ == "__main__":
    unittest.main()
