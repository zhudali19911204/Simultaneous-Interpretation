from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import URLError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.meeting_assistant import (  # noqa: E402
    MeetingAssistantClient,
    bounded_transcript,
    ensure_grounded_answer,
    filter_timeline_turns,
    format_assistant_turn,
    result_generation_is_current,
    select_question_turns,
    should_auto_refresh,
    snapshot_requires_reset,
    split_transcript_by_chars,
)
from simultaneous_interpreter.meeting_minutes import MeetingTurn  # noqa: E402


class FakeResponse:
    def __init__(self, content: str, input_tokens: int = 10, output_tokens: int = 5):
        self._payload = json.dumps(
            {
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def make_turn(
    seconds: int,
    direction: str,
    source: str,
    translated: str,
    alignment_status: str = "matched",
) -> MeetingTurn:
    return MeetingTurn(
        datetime(2026, 7, 20, 9, 0, 0) + timedelta(seconds=seconds),
        direction,
        source,
        translated,
        alignment_status,
    )


class TimelineTests(unittest.TestCase):
    def test_timeline_is_sorted_filtered_and_searchable(self) -> None:
        turns = (
            make_turn(20, "outgoing", "第二句", "Second sentence"),
            make_turn(10, "incoming", "First sentence", "第一句"),
        )
        result = filter_timeline_turns(turns, direction="incoming", query="第一")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].recorded_at.second, 10)

    def test_missing_side_is_marked(self) -> None:
        source_only = make_turn(
            0,
            "incoming",
            "Only source",
            "",
            "source_only",
        )
        self.assertIn("[译文缺失]", format_assistant_turn(source_only))

    def test_question_context_keeps_recent_and_relevant_old_turns(self) -> None:
        turns = (
            make_turn(0, "incoming", "Project Falcon budget", "猎鹰项目预算"),
            make_turn(700, "outgoing", "普通讨论", "General discussion"),
            make_turn(1_600, "incoming", "Final update", "最终更新"),
        )
        selected = select_question_turns(turns, "猎鹰预算是什么")
        self.assertEqual(selected[0], turns[0])
        self.assertIn(turns[-1], selected)

    def test_bounded_transcript_keeps_latest_content(self) -> None:
        turns = tuple(
            make_turn(index, "incoming", f"source-{index}", f"译文-{index}")
            for index in range(20)
        )
        context = bounded_transcript(turns, max_chars=180)
        self.assertIn("source-19", context)
        self.assertNotIn("source-0 ", context)

    def test_clear_or_replacement_invalidates_derived_state(self) -> None:
        original = (
            make_turn(0, "incoming", "A", "甲"),
            make_turn(1, "outgoing", "B", "乙"),
        )
        self.assertTrue(snapshot_requires_reset(original, ()))
        self.assertTrue(snapshot_requires_reset(original, original[:1]))
        self.assertFalse(
            snapshot_requires_reset(
                original,
                (*original, make_turn(2, "incoming", "C", "丙")),
            )
        )

    def test_stale_window_or_data_result_is_rejected(self) -> None:
        self.assertTrue(result_generation_is_current(2, 3, 2, 3))
        self.assertFalse(result_generation_is_current(1, 3, 2, 3))
        self.assertFalse(result_generation_is_current(2, 2, 2, 3))

    def test_transcript_chunks_never_exceed_limit(self) -> None:
        turns = (make_turn(0, "incoming", "A" * 25_000, "超长内容"),)
        chunks = split_transcript_by_chars(turns, max_chars=12_000)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 12_000 for chunk in chunks))


class AutoRefreshTests(unittest.TestCase):
    def test_auto_refresh_requires_visibility_interval_and_new_content(self) -> None:
        now = datetime(2026, 7, 20, 10, 0, 0)
        turns = (make_turn(0, "incoming", "x" * 350, "译文"),)
        common = {
            "enabled": True,
            "window_visible": True,
            "busy": False,
            "now": now,
            "turns": turns,
            "processed_turn_count": 0,
        }
        self.assertTrue(should_auto_refresh(last_attempt_at=None, **common))
        self.assertFalse(
            should_auto_refresh(
                last_attempt_at=now - timedelta(seconds=100),
                **common,
            )
        )
        self.assertFalse(
            should_auto_refresh(
                last_attempt_at=None,
                **{**common, "window_visible": False},
            )
        )
        self.assertFalse(
            should_auto_refresh(
                last_attempt_at=None,
                **{**common, "busy": True},
            )
        )


class ClientTests(unittest.TestCase):
    def test_answer_without_time_role_evidence_is_rejected(self) -> None:
        self.assertIn("未找到依据", ensure_grounded_answer("预算已经确认。"))

    def test_answer_uses_question_context_and_reports_usage(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse("依据 [09:00:00 对方]，预算已确认。", 12, 6)

        client = MeetingAssistantClient(
            "test-key",
            "https://api.example.com/v1/chat/completions",
            "test-model",
            opener=opener,
        )
        result = client.answer(
            "预算确认了吗？",
            (make_turn(0, "incoming", "Budget approved", "预算已确认"),),
        )
        self.assertIn("预算已确认", requests[0]["messages"][1]["content"])
        self.assertIn("当前记录中未找到依据", requests[0]["messages"][1]["content"])
        self.assertEqual(result.usage.input_tokens, 12)
        self.assertEqual(result.usage.output_tokens, 6)

    def test_incremental_insight_processes_all_chunks(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            body = json.loads(request.data.decode("utf-8"))
            requests.append(body)
            return FakeResponse(f"## 当前议题\n更新 {len(requests)}", 8, 4)

        client = MeetingAssistantClient(
            "",
            "http://localhost:11434/v1",
            "local-model",
            opener=opener,
        )
        turns = (
            make_turn(0, "incoming", "A" * 7_000, "第一段"),
            make_turn(1, "outgoing", "B" * 7_000, "第二段"),
        )
        update = client.update_insight(turns, "旧重点", 0)
        self.assertEqual(len(requests), 2)
        self.assertIn("旧重点", requests[0]["messages"][1]["content"])
        self.assertIn("更新 1", requests[1]["messages"][1]["content"])
        self.assertEqual(update.processed_turn_count, 2)
        self.assertEqual(update.usage.input_tokens, 16)
        self.assertEqual(update.usage.output_tokens, 8)

    def test_network_error_is_scoped_to_assistant(self) -> None:
        def opener(_request, **_kwargs):
            raise URLError("offline")

        client = MeetingAssistantClient(
            "key",
            "https://api.example.com/v1",
            "model",
            provider_name="测试服务",
            opener=opener,
        )
        with self.assertRaisesRegex(RuntimeError, "会议助手服务"):
            client.answer(
                "刚才说了什么？",
                (make_turn(0, "incoming", "Hello", "你好"),),
            )

    def test_empty_meeting_is_rejected_without_network(self) -> None:
        client = MeetingAssistantClient(
            "key",
            "https://api.example.com/v1",
            "model",
            opener=lambda *_args, **_kwargs: self.fail("network called"),
        )
        with self.assertRaisesRegex(ValueError, "会议记录"):
            client.answer("问题", ())


if __name__ == "__main__":
    unittest.main()
