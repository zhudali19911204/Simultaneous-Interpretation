from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.meeting_minutes import (  # noqa: E402
    MINUTES_MODEL,
    MeetingTurn,
    OpenAICompatibleMeetingMinutesClient,
    QwenMeetingMinutesClient,
    build_chat_url,
    format_transcript,
    normalize_chat_url,
    normalize_model_name,
    split_transcript,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def sample_turns() -> list[MeetingTurn]:
    now = datetime(2026, 7, 14, 9, 30, 0)
    return [
        MeetingTurn(
            now,
            "incoming",
            "We will ship the prototype on Friday.",
            "我们将在周五交付原型。",
        ),
        MeetingTurn(
            now + timedelta(seconds=8),
            "outgoing",
            "我负责在周四完成测试。",
            "I will finish testing on Thursday.",
        ),
    ]


class TranscriptTests(unittest.TestCase):
    def test_chat_url_uses_workspace_domain(self) -> None:
        self.assertEqual(
            build_chat_url("ws-test_123"),
            "https://ws-test_123.cn-beijing.maas.aliyuncs.com"
            "/compatible-mode/v1/chat/completions",
        )
        with self.assertRaises(ValueError):
            build_chat_url("unsafe/path")

    def test_transcript_keeps_both_languages_and_roles(self) -> None:
        transcript = format_transcript(sample_turns())
        self.assertIn("对方（英语）", transcript)
        self.assertIn("我们将在周五交付原型。", transcript)
        self.assertIn("我（中文）", transcript)
        self.assertIn("I will finish testing on Thursday.", transcript)

    def test_transcript_marks_unmatched_side(self) -> None:
        turn = MeetingTurn(
            datetime(2026, 7, 20, 9, 0, 0),
            "incoming",
            "Only source text",
            "",
            alignment_status="source_only",
        )
        transcript = format_transcript([turn])
        self.assertIn("Only source text", transcript)
        self.assertIn("[译文缺失]", transcript)

    def test_split_transcript_preserves_all_lines(self) -> None:
        transcript = "\n".join(f"line-{index}" for index in range(20))
        chunks = split_transcript(transcript, max_chars=35)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n".join(chunks), transcript)

    def test_normalize_model_name(self) -> None:
        self.assertEqual(normalize_model_name(" qwen-plus "), "qwen-plus")
        self.assertEqual(normalize_model_name("Qwen/Qwen3-8B"), "Qwen/Qwen3-8B")
        with self.assertRaises(ValueError):
            normalize_model_name("")
        with self.assertRaises(ValueError):
            normalize_model_name("qwen plus")

    def test_normalize_chat_url_accepts_base_or_full_url(self) -> None:
        self.assertEqual(
            normalize_chat_url("https://api.example.com/v1"),
            "https://api.example.com/v1/chat/completions",
        )
        self.assertEqual(
            normalize_chat_url("http://localhost:11434/v1/chat/completions"),
            "http://localhost:11434/v1/chat/completions",
        )
        with self.assertRaises(ValueError):
            normalize_chat_url("ftp://api.example.com/v1")


class ClientTests(unittest.TestCase):
    def test_openai_compatible_client_uses_custom_url_and_body(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request)
            return FakeResponse(
                {
                    "choices": [{"message": {"content": "# 会议纪要\n自定义服务"}}],
                    "usage": {},
                }
            )

        client = OpenAICompatibleMeetingMinutesClient(
            "",
            "http://localhost:11434/v1",
            model="qwen3:8b",
            provider_name="本地模型",
            extra_body={"stream": False},
        )
        started_at = sample_turns()[0].recorded_at
        with patch("simultaneous_interpreter.meeting_minutes.urlopen", fake_urlopen):
            client.generate(sample_turns(), started_at, started_at + timedelta(minutes=2))

        request = captured[0]
        self.assertEqual(
            request.full_url,
            "http://localhost:11434/v1/chat/completions",
        )
        self.assertIsNone(request.get_header("Authorization"))
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "qwen3:8b")
        self.assertFalse(payload["stream"])
        self.assertNotIn("enable_thinking", payload)

    def test_generate_calls_configured_model_and_returns_usage(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return FakeResponse(
                {
                    "choices": [
                        {"message": {"content": "# 会议纪要\n\n## 核心摘要\n- 已确认交付时间"}}
                    ],
                    "usage": {"prompt_tokens": 321, "completion_tokens": 88},
                }
            )

        client = QwenMeetingMinutesClient("sk-test", "ws-test", model="qwen-max")
        started_at = sample_turns()[0].recorded_at
        with patch("simultaneous_interpreter.meeting_minutes.urlopen", fake_urlopen):
            result = client.generate(
                sample_turns(),
                started_at,
                started_at + timedelta(minutes=42),
            )

        self.assertIn("# 会议纪要", result.markdown)
        self.assertEqual(result.input_tokens, 321)
        self.assertEqual(result.output_tokens, 88)
        request, timeout = captured[0]
        self.assertIn("ws-test.cn-beijing.maas.aliyuncs.com", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test")
        self.assertGreater(timeout, 0)
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "qwen-max")
        self.assertFalse(payload["enable_thinking"])
        self.assertIn("行动项", payload["messages"][1]["content"])
        self.assertNotIn("shared_screen_context", payload["messages"][1]["content"])

    def test_generate_uses_default_minutes_model(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request)
            return FakeResponse(
                {
                    "choices": [{"message": {"content": "# 会议纪要\n默认模型"}}],
                    "usage": {},
                }
            )

        client = QwenMeetingMinutesClient("sk-test", "ws-test")
        started_at = sample_turns()[0].recorded_at
        with patch("simultaneous_interpreter.meeting_minutes.urlopen", fake_urlopen):
            client.generate(sample_turns(), started_at, started_at + timedelta(minutes=1))

        payload = json.loads(captured[0].data.decode("utf-8"))
        self.assertEqual(payload["model"], MINUTES_MODEL)

    def test_visual_context_adds_shared_screen_section_only_when_present(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request)
            return FakeResponse(
                {
                    "choices": [{"message": {"content": "# 会议纪要\n视觉内容"}}],
                    "usage": {},
                }
            )

        client = QwenMeetingMinutesClient("sk-test", "ws-test")
        started_at = sample_turns()[0].recorded_at
        with patch("simultaneous_interpreter.meeting_minutes.urlopen", fake_urlopen):
            client.generate(
                sample_turns(),
                started_at,
                started_at + timedelta(minutes=1),
                "[画面 09:30:00 第1页]\n标题：项目计划",
            )

        prompt = json.loads(captured[0].data.decode("utf-8"))["messages"][1]["content"]
        self.assertIn("## 共享画面要点", prompt)
        self.assertIn("<shared_screen_context>", prompt)

    def test_long_meeting_aggregates_chunk_and_final_usage(self) -> None:
        responses = iter(
            [
                FakeResponse(
                    {
                        "choices": [{"message": {"content": "分段笔记一"}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                    }
                ),
                FakeResponse(
                    {
                        "choices": [{"message": {"content": "分段笔记二"}}],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                    }
                ),
                FakeResponse(
                    {
                        "choices": [{"message": {"content": "# 会议纪要\n最终结果"}}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
                    }
                ),
            ]
        )

        def fake_urlopen(_request, timeout):
            self.assertGreater(timeout, 0)
            return next(responses)

        turns = sample_turns()
        started_at = turns[0].recorded_at
        client = QwenMeetingMinutesClient("sk-test", "ws-test")
        with (
            patch(
                "simultaneous_interpreter.meeting_minutes.split_transcript",
                return_value=["第一段", "第二段"],
            ),
            patch("simultaneous_interpreter.meeting_minutes.urlopen", fake_urlopen),
        ):
            result = client.generate(turns, started_at, started_at + timedelta(hours=2))

        self.assertEqual(result.input_tokens, 33)
        self.assertEqual(result.output_tokens, 9)
        self.assertIn("最终结果", result.markdown)


if __name__ == "__main__":
    unittest.main()
