from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.subtitle_overlay import (  # noqa: E402
    RollingSubtitleBuffer,
    SubtitleOverlay,
    compact_subtitle,
    make_overlay_text,
)


class SubtitleOverlayTests(unittest.TestCase):
    def test_outgoing_text_keeps_chinese_above_english(self) -> None:
        text = make_overlay_text("outgoing", "大家好", "Hello everyone", True)
        self.assertEqual(text.role, "我说")
        self.assertEqual(text.chinese, "大家好")
        self.assertEqual(text.english, "Hello everyone")
        self.assertTrue(text.is_final)

    def test_incoming_text_keeps_chinese_above_english(self) -> None:
        text = make_overlay_text("incoming", "Good morning", "早上好", False)
        self.assertEqual(text.role, "对方说")
        self.assertEqual(text.chinese, "早上好")
        self.assertEqual(text.english, "Good morning")

    def test_subtitle_is_compacted_and_bounded(self) -> None:
        self.assertEqual(compact_subtitle("  one\n two  "), "one two")
        self.assertEqual(compact_subtitle("abcdef", 5), "…cdef")

    def test_missing_translation_has_visible_feedback(self) -> None:
        outgoing = make_overlay_text("outgoing", "你好", "", False)
        incoming = make_overlay_text("incoming", "Hello", "", False)
        self.assertEqual(outgoing.english, "Translating…")
        self.assertEqual(incoming.chinese, "正在翻译…")

    def test_final_unmatched_text_is_marked(self) -> None:
        source_only = make_overlay_text(
            "outgoing",
            "只有原文",
            "",
            True,
            "source_only",
        )
        translation_only = make_overlay_text(
            "incoming",
            "",
            "只有译文",
            True,
            "translation_only",
        )
        self.assertEqual(source_only.english, "[译文缺失]")
        self.assertEqual(translation_only.english, "[原文缺失]")

    def test_unknown_direction_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "字幕方向"):
            make_overlay_text("sideways", "source", "translation", False)

    def test_final_subtitles_roll_and_keep_latest_three(self) -> None:
        buffer = RollingSubtitleBuffer(max_items=3)
        for index in range(4):
            buffer.update(
                make_overlay_text(
                    "outgoing",
                    f"中文 {index}",
                    f"English {index}",
                    True,
                )
            )
        self.assertEqual(
            [entry.chinese for entry in buffer.entries],
            ["中文 1", "中文 2", "中文 3"],
        )

    def test_interim_subtitle_replaces_live_row(self) -> None:
        buffer = RollingSubtitleBuffer(max_items=3)
        buffer.update(make_overlay_text("outgoing", "第一句", "First", True))
        buffer.update(make_overlay_text("outgoing", "正在说", "", False))
        buffer.update(make_overlay_text("outgoing", "正在说完整内容", "Speaking", False))
        self.assertEqual(len(buffer.entries), 2)
        self.assertEqual(buffer.entries[-1].chinese, "正在说完整内容")
        self.assertEqual(buffer.entries[-1].english, "Speaking")

    def test_source_only_update_keeps_existing_live_translation(self) -> None:
        buffer = RollingSubtitleBuffer(max_items=3)
        buffer.update(make_overlay_text("outgoing", "大家好", "Hello", False))
        buffer.update(
            make_overlay_text("outgoing", "大家好，欢迎参加会议", "", False)
        )
        self.assertEqual(buffer.entries[-1].chinese, "大家好，欢迎参加会议")
        self.assertEqual(buffer.entries[-1].english, "Hello")

    def test_expiring_interim_does_not_remove_final_history(self) -> None:
        buffer = RollingSubtitleBuffer(max_items=3)
        buffer.update(make_overlay_text("outgoing", "第一句", "First", True))
        buffer.update(make_overlay_text("outgoing", "第二句", "", False))
        buffer.clear_current()
        self.assertEqual(len(buffer.entries), 1)
        self.assertEqual(buffer.entries[0].english, "First")

    def test_connection_notice_is_separate_and_auto_clears(self) -> None:
        class FakeOwner:
            def __init__(self) -> None:
                self.callbacks = {}
                self.sequence = 0

            def after(self, _delay, callback):
                self.sequence += 1
                identifier = f"after-{self.sequence}"
                self.callbacks[identifier] = callback
                return identifier

            def after_cancel(self, identifier):
                self.callbacks.pop(identifier, None)

        owner = FakeOwner()
        overlay = SubtitleOverlay(  # type: ignore[arg-type]
            owner,
            object(),  # type: ignore[arg-type]
            lambda: None,
        )
        overlay._buffer.update(
            make_overlay_text("outgoing", "第一句", "First", True)
        )
        overlay.set_connection_notice(
            "连接已恢复",
            kind="info",
            clear_after_ms=3_000,
        )
        self.assertEqual(overlay.connection_notice, "连接已恢复")
        self.assertEqual(overlay._buffer.entries[0].english, "First")
        callback = next(iter(owner.callbacks.values()))
        callback()
        self.assertEqual(overlay.connection_notice, "")
        self.assertEqual(overlay._buffer.entries[0].english, "First")


if __name__ == "__main__":
    unittest.main()
