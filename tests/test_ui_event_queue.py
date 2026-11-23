from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.app import (  # noqa: E402
    InterpreterApp,
    UiEventQueue,
    UiMessage,
)
from simultaneous_interpreter.qwen_backend import TranslationEvent  # noqa: E402


def preview(text: str, segment: str = "a", direction: str = "incoming") -> UiMessage:
    return UiMessage(direction, TranslationEvent("Source", text, False, segment_id=segment))


class UiEventQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = InterpreterApp.__new__(InterpreterApp)
        self.app.root = Mock()
        self.app.root.winfo_exists.return_value = True
        self.app._events = UiEventQueue()
        self.app._handle_ui_message = Mock()

    def test_one_frame_keeps_latest_snapshot_for_each_direction_and_segment(self) -> None:
        messages = [
            preview("一"), preview("二", "b"), preview("一二"),
            preview("Hello", "a", "outgoing"), preview("一二三"),
        ]
        for message in messages:
            self.app._post(message.kind, message.payload)
        result = self.app._events.take(10)
        self.assertEqual(
            [(item.kind, item.payload.segment_id, item.payload.translated_text)
             for item in result],
            [("incoming", "a", "一二三"), ("incoming", "b", "二"),
             ("outgoing", "a", "Hello")],
        )

    def test_finals_and_control_messages_keep_their_order(self) -> None:
        final = UiMessage("incoming", TranslationEvent("Source", "完成", True, segment_id="a"))
        stop = UiMessage("stopped")
        messages = [preview("一"), preview("一二"), final, stop, preview("新句")]
        for message in messages:
            self.app._post(message.kind, message.payload)
        self.assertEqual(
            self.app._events.take(10),
            [messages[1], final, stop, messages[-1]],
        )

    def test_first_frame_after_stall_draws_current_preview(self) -> None:
        for index in range(1, 1025):
            self.app._post("incoming", preview(str(index)).payload)
        self.app._drain_events()
        self.app._handle_ui_message.assert_called_once_with(preview("1024"))
        self.assertEqual(self.app._events.take(1), [])
        self.app.root.after.assert_called_once_with(self.app.POLL_MS, self.app._drain_events)

    def test_queue_yields_to_tk_after_bounded_batch_of_distinct_events(self) -> None:
        messages = [
            preview(str(index), segment=str(index))
            for index in range(self.app.MAX_EVENTS_PER_POLL + 1)
        ]
        for message in messages:
            self.app._post(message.kind, message.payload)
        self.app._drain_events()
        self.assertEqual(self.app._events.take(1), messages[-1:])
        self.assertEqual(self.app._handle_ui_message.call_args_list, [
            call(message) for message in messages[:-1]
        ])
        self.app.root.after.assert_called_once_with(1, self.app._drain_events)

    def test_first_preview_is_delivered_without_waiting_for_final(self) -> None:
        first = preview("第一片段")
        self.app._post(first.kind, first.payload)
        self.app._drain_events()
        self.app._handle_ui_message.assert_called_once_with(first)
        self.app.root.after.assert_called_once_with(self.app.POLL_MS, self.app._drain_events)


if __name__ == "__main__":
    unittest.main()
