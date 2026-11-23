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
    DEFAULT_MINUTES_TEMPLATE,
    EvidenceRef,
    FactItem,
    FactLedger,
    MINUTES_MODEL,
    MeetingTurn,
    NumericClaim,
    OpenAICompatibleMeetingMinutesClient,
    QwenMeetingMinutesClient,
    build_chat_url,
    format_fact_ledger,
    format_transcript,
    merge_fact_ledgers,
    minutes_template_label,
    normalize_chat_url,
    normalize_minutes_template,
    normalize_model_name,
    parse_fact_ledger,
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


def fact_response(
    *,
    prompt_tokens: int = 10,
    completion_tokens: int = 2,
) -> FakeResponse:
    content = json.dumps(
        {
            "facts": [
                {
                    "category": "action",
                    "topic": "原型交付",
                    "statement": "周五交付原型",
                    "owner": "",
                    "deadline": "周五",
                    "status": "",
                    "evidence": [
                        {
                            "timestamp": "09:30:00",
                            "role": "对方",
                            "excerpt": "ship the prototype on Friday",
                        }
                    ],
                }
            ],
            "numeric_claims": [],
        },
        ensure_ascii=False,
    )
    return FakeResponse(
        {
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }
    )


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

    def test_minutes_template_defaults_and_labels(self) -> None:
        self.assertEqual(DEFAULT_MINUTES_TEMPLATE, "standard")
        self.assertEqual(normalize_minutes_template(" Detailed "), "detailed")
        self.assertEqual(minutes_template_label("executive"), "领导摘要")
        with self.assertRaises(ValueError):
            normalize_minutes_template("custom")

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


class FactLedgerTests(unittest.TestCase):
    def test_parse_fact_ledger_keeps_only_real_timestamps(self) -> None:
        content = json.dumps(
            {
                "facts": [
                    {
                        "category": "action",
                        "topic": "审计费",
                        "statement": "小华在7月冲销计提",
                        "owner": "小华",
                        "deadline": "7月",
                        "status": "已确认",
                        "evidence": [
                            {
                                "timestamp": "09:30:08",
                                "role": "对方",
                                "excerpt": "7月完成冲销",
                            },
                            {
                                "timestamp": "10:00:00",
                                "role": "对方",
                                "excerpt": "不存在的时间",
                            },
                        ],
                    }
                ],
                "numeric_claims": [],
            },
            ensure_ascii=False,
        )

        ledger = parse_fact_ledger(
            content,
            allowed_timestamps={"09:30:08"},
        )

        self.assertEqual(ledger.facts[0].owner, "小华")
        self.assertEqual(
            [item.timestamp for item in ledger.facts[0].evidence],
            ["09:30:08"],
        )
        self.assertTrue(any("10:00:00" in item for item in ledger.warnings))

    def test_merge_deduplicates_facts_and_detects_numeric_conflicts(self) -> None:
        first_evidence = EvidenceRef("09:20:00", "对方")
        second_evidence = EvidenceRef("09:45:00", "我")
        first = FactLedger(
            facts=(
                FactItem(
                    "discussion",
                    "审计费",
                    "审计费需要冲销",
                    evidence=(first_evidence,),
                ),
            ),
            numeric_claims=(
                NumericClaim(
                    "审计费影响",
                    "120",
                    "万元",
                    "2026年",
                    evidence=(first_evidence,),
                ),
            ),
        )
        second = FactLedger(
            facts=(
                FactItem(
                    "discussion",
                    "审计费",
                    "审计费需要冲销",
                    evidence=(second_evidence,),
                ),
            ),
            numeric_claims=(
                NumericClaim(
                    "审计费影响",
                    "60",
                    "万元",
                    "2026年",
                    evidence=(second_evidence,),
                ),
            ),
        )

        merged = merge_fact_ledgers((first, second))

        self.assertEqual(len(merged.facts), 1)
        self.assertEqual(len(merged.facts[0].evidence), 2)
        self.assertEqual(len(merged.conflicts), 1)
        self.assertEqual(
            {item.value for item in merged.conflicts[0].claims},
            {"120", "60"},
        )
        payload = json.loads(format_fact_ledger(merged))
        self.assertEqual(len(payload["numeric_conflicts"]), 1)


class ClientTests(unittest.TestCase):
    def test_openai_compatible_client_uses_custom_url_and_body(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request)
            payload = json.loads(request.data.decode("utf-8"))
            if "只输出一个 JSON 对象" in payload["messages"][1]["content"]:
                return fact_response()
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
            payload = json.loads(request.data.decode("utf-8"))
            if "只输出一个 JSON 对象" in payload["messages"][1]["content"]:
                return fact_response(prompt_tokens=100, completion_tokens=20)
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
        self.assertEqual(result.input_tokens, 421)
        self.assertEqual(result.output_tokens, 108)
        self.assertEqual(result.quality_status, "checked")
        request, timeout = captured[-1]
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
            payload = json.loads(request.data.decode("utf-8"))
            if "只输出一个 JSON 对象" in payload["messages"][1]["content"]:
                return fact_response()
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

        payload = json.loads(captured[-1].data.decode("utf-8"))
        self.assertEqual(payload["model"], MINUTES_MODEL)

    def test_visual_context_adds_shared_screen_section_only_when_present(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request)
            payload = json.loads(request.data.decode("utf-8"))
            if "只输出一个 JSON 对象" in payload["messages"][1]["content"]:
                return fact_response()
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

        prompt = json.loads(captured[-1].data.decode("utf-8"))["messages"][1]["content"]
        self.assertIn("## 共享画面要点", prompt)
        self.assertIn("<shared_screen_context>", prompt)

    def test_detailed_template_uses_dynamic_summary_range(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request)
            payload = json.loads(request.data.decode("utf-8"))
            if "只输出一个 JSON 对象" in payload["messages"][1]["content"]:
                return fact_response()
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    "# 会议纪要\n"
                                    "## 核心摘要\n"
                                    "- 已确认周五交付 [09:30:00]"
                                )
                            }
                        }
                    ],
                    "usage": {},
                }
            )

        client = QwenMeetingMinutesClient("sk-test", "ws-test")
        started_at = sample_turns()[0].recorded_at
        with patch("simultaneous_interpreter.meeting_minutes.urlopen", fake_urlopen):
            result = client.generate(
                sample_turns(),
                started_at,
                started_at + timedelta(minutes=2),
                template="detailed",
            )

        final_prompt = json.loads(captured[-1].data.decode("utf-8"))[
            "messages"
        ][1]["content"]
        self.assertIn("详细纪要", final_prompt)
        self.assertIn("8–15 条", final_prompt)
        self.assertIn("## 议题与详细讨论", final_prompt)
        self.assertEqual(result.template, "detailed")
        self.assertFalse(result.quality_warnings)

    def test_long_meeting_aggregates_chunk_and_final_usage(self) -> None:
        responses = iter(
            [
                fact_response(prompt_tokens=10, completion_tokens=2),
                fact_response(prompt_tokens=11, completion_tokens=3),
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

    def test_invalid_fact_json_falls_back_with_visible_quality_warning(self) -> None:
        responses = iter(
            [
                FakeResponse(
                    {
                        "choices": [{"message": {"content": "不是 JSON"}}],
                        "usage": {"prompt_tokens": 8, "completion_tokens": 1},
                    }
                ),
                FakeResponse(
                    {
                        "choices": [
                            {"message": {"content": "# 会议纪要\n基础流程结果"}}
                        ],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 5},
                    }
                ),
            ]
        )

        client = QwenMeetingMinutesClient("sk-test", "ws-test")
        started_at = sample_turns()[0].recorded_at
        with patch(
            "simultaneous_interpreter.meeting_minutes.urlopen",
            lambda _request, timeout: next(responses),
        ):
            result = client.generate(
                sample_turns(),
                started_at,
                started_at + timedelta(minutes=2),
            )

        self.assertEqual(result.quality_status, "fallback")
        self.assertIn("基础流程结果", result.markdown)
        self.assertEqual(result.input_tokens, 28)
        self.assertEqual(result.output_tokens, 6)
        self.assertTrue(any("回退" in item for item in result.quality_warnings))


if __name__ == "__main__":
    unittest.main()
