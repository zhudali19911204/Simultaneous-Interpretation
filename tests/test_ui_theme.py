from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.ui_theme import (  # noqa: E402
    parse_markdown_blocks,
    status_style_for,
)


class UiThemeTests(unittest.TestCase):
    def test_status_styles_have_safe_fallback(self) -> None:
        expected = {
            "idle": "Idle.Status.TLabel",
            "info": "Info.Status.TLabel",
            "busy": "Busy.Status.TLabel",
            "running": "Running.Status.TLabel",
            "warning": "Warning.Status.TLabel",
            "error": "Error.Status.TLabel",
        }
        self.assertEqual(
            {kind: status_style_for(kind) for kind in expected},
            expected,
        )
        self.assertEqual(status_style_for("unknown"), "Idle.Status.TLabel")

    def test_markdown_is_split_into_renderable_blocks(self) -> None:
        blocks = parse_markdown_blocks(
            "# 会议纪要\n\n## 决策\n- 采用方案 A\n1. 张三跟进\n普通说明"
        )
        self.assertEqual(
            [(block.kind, block.text) for block in blocks],
            [
                ("h1", "会议纪要"),
                ("blank", ""),
                ("h2", "决策"),
                ("bullet", "采用方案 A"),
                ("numbered", "1. 张三跟进"),
                ("body", "普通说明"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
