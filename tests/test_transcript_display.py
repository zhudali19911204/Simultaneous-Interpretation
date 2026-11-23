from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.app import InterpreterApp  # noqa: E402
from simultaneous_interpreter.qwen_backend import TranslationEvent  # noqa: E402


class TranscriptDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}") from exc
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def setUp(self) -> None:
        self.history = tk.Text(self.root, state="disabled")
        self.status = tk.StringVar(master=self.root)
        InterpreterApp._reset_history(self.history)
        self.addCleanup(self.history.destroy)

    def show(
        self,
        source: str,
        translated: str,
        *,
        final: bool = False,
        segment_id: str = "segment-a",
        alignment_status: str = "matched",
    ) -> None:
        InterpreterApp._show_translation(
            TranslationEvent(
                source,
                translated,
                final,
                alignment_status=alignment_status,
                segment_id=segment_id,
            ),
            self.status,
            self.history,
        )

    def content(self) -> str:
        return self.history.get("1.0", "end-1c")

    def test_both_languages_are_visible_before_final_result(self) -> None:
        self.show("Please review the proposal.", "请审阅这份提案。")

        self.assertEqual(
            self.content(),
            "原文：Please review the proposal.\n译文：请审阅这份提案。\n\n",
        )
        self.assertEqual(str(self.history.cget("state")), "disabled")
        self.assertEqual(self.status.get(), "中英文实时更新中…")
        self.assertFalse(self.history.tag_ranges("placeholder"))

    def test_streaming_replaces_same_pair_and_final_does_not_duplicate(self) -> None:
        self.show("我们", "We")
        self.show("我们开始开会。", "Let's start the meeting.")
        self.show("我们开始开会。", "Let's start the meeting.", final=True)
        self.show("下一项", "Next item", segment_id="segment-b")

        self.assertEqual(self.content().count("原文："), 2)
        self.assertEqual(self.content().count("Let's start the meeting."), 1)
        self.assertIn("原文：我们开始开会。\n译文：Let's start the meeting.", self.content())
        self.assertNotIn("译文：We\n", self.content())

    def test_late_final_does_not_erase_newer_live_pair(self) -> None:
        self.show("First sentence", "第一句")
        self.show("Second sentence", "第二句", segment_id="segment-b")
        self.show("First sentence.", "第一句。", final=True)

        self.assertEqual(
            self.content(),
            "原文：First sentence.\n译文：第一句。\n\n"
            "原文：Second sentence\n译文：第二句\n\n",
        )
        self.assertEqual(self.status.get(), "中英文实时更新中…")
        self.show(
            "Second sentence.", "第二句。", final=True, segment_id="segment-b",
        )
        self.assertEqual(self.content().count("原文："), 2)
        self.assertEqual(self.status.get(), "正在聆听…")

    def test_missing_side_shows_pending_then_real_text(self) -> None:
        self.show("", "你好")
        self.assertIn("原文：正在识别…", self.content())
        self.show("Hello", "你好")
        self.assertNotIn("正在识别", self.content())
        self.assertEqual(self.content().count("译文：你好"), 1)

        self.show("Thank you", "", segment_id="segment-b")
        self.assertIn("译文：正在翻译…", self.content())
        self.show("Thank you", "谢谢", segment_id="segment-b")
        self.assertNotIn("正在翻译", self.content())
        self.assertIn("原文：Hello\n译文：你好", self.content())

    def test_final_missing_side_retains_warning(self) -> None:
        self.show("Hello", "", final=True, alignment_status="source_only")
        self.show(
            "", "谢谢", final=True, segment_id="segment-b",
            alignment_status="translation_only",
        )

        self.assertIn("译文：[译文缺失]", self.content())
        self.assertIn("原文：[原文缺失]", self.content())
        self.assertTrue(self.history.tag_ranges("alignment_warning"))

    def test_clear_removes_live_pair_and_new_input_starts_cleanly(self) -> None:
        self.show("Old sentence", "旧句子")
        InterpreterApp._reset_history(self.history)
        self.assertFalse(any(
            tag.startswith("live_segment:") for tag in self.history.tag_names()
        ))
        self.show("New sentence", "新句子")

        self.assertEqual(self.content(), "原文：New sentence\n译文：新句子\n\n")

    def test_events_without_segment_id_still_replace_the_live_pair(self) -> None:
        self.show("Hello", "你好", segment_id="")
        self.show("Hello!", "你好！", final=True, segment_id="")
        self.show("Thanks", "谢谢", segment_id="")

        self.assertEqual(
            self.content(),
            "原文：Hello!\n译文：你好！\n\n原文：Thanks\n译文：谢谢\n\n",
        )

    def test_appending_translation_keeps_existing_text_and_selection(self) -> None:
        self.show("Hello everyone", "大家")
        source_start = self.history.search("Hello", "1.0")
        source_end = self.history.index(f"{source_start}+5c")
        self.history.tag_add("sel", source_start, source_end)
        with patch.object(self.history, "delete", wraps=self.history.delete) as delete:
            self.show("Hello everyone", "大家好")
        delete.assert_not_called()
        selected = self.history.tag_ranges("sel")
        self.assertTrue(selected)
        self.assertEqual(self.history.get(*selected), "Hello")
        self.assertEqual(self.content(), "原文：Hello everyone\n译文：大家好\n\n")

    def test_partial_correction_only_replaces_changed_tail(self) -> None:
        self.show("Meet on Monday", "周一见")
        with patch.object(self.history, "delete", wraps=self.history.delete) as delete:
            self.show("Meet on Friday", "周五见")
        self.assertEqual(delete.call_count, 2)
        self.assertEqual(self.content(), "原文：Meet on Friday\n译文：周五见\n\n")
        self.assertTrue(all(
            self.history.compare(call.args[0], ">", "1.0")
            for call in delete.call_args_list
        ))

    def test_unchanged_final_does_not_rewrite_streamed_text(self) -> None:
        self.show("Hello", "你好")
        with patch.object(self.history, "delete", wraps=self.history.delete) as delete:
            with patch.object(self.history, "insert", wraps=self.history.insert) as insert:
                self.show("Hello", "你好", final=True)
        delete.assert_not_called()
        insert.assert_not_called()
        self.assertEqual(self.status.get(), "正在聆听…")

    def test_unicode_and_multiline_updates_keep_both_languages_intact(self) -> None:
        self.show("Hi 👋\nEveryone", "大家好👋\n欢迎")
        self.show("Hi 👋\nEveryone!", "大家好👋\n欢迎参加")
        self.show("Hi 👋\nAll!", "大家好👋\n欢迎参会", final=True)
        self.assertEqual(
            self.content(),
            "原文：Hi 👋\nAll!\n译文：大家好👋\n欢迎参会\n\n",
        )


if __name__ == "__main__":
    unittest.main()
