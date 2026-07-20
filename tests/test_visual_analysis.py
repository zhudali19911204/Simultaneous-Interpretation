from __future__ import annotations

import io
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import URLError

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.meeting_minutes import MeetingTurn  # noqa: E402
from simultaneous_interpreter.visual_analysis import (  # noqa: E402
    OpenAICompatibleVisionClient,
    PageChangeDetector,
    VisualAnalysis,
    VisualAnalysisScheduler,
    VisualMoment,
    fit_image_size,
    format_visual_context,
    hash_distance,
    parse_visual_analysis,
    prepare_image,
    select_visual_moments,
    trim_visual_media,
)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _analysis(title: str = "经营看板") -> VisualAnalysis:
    return VisualAnalysis(
        title=title,
        summary_points=("收入增长", "成本稳定", "需要确认预测"),
        metrics=("收入 120 万",),
    )


class ImagePreparationTests(unittest.TestCase):
    def test_preview_size_fills_bounds_without_changing_aspect_ratio(self) -> None:
        self.assertEqual(fit_image_size(1600, 900, 800, 240), (427, 240))
        self.assertEqual(fit_image_size(320, 180, 640, 360), (640, 360))

    def test_preview_size_rejects_invalid_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            fit_image_size(1600, 0, 800, 240)

    def test_image_is_resized_hashed_and_bounded(self) -> None:
        image = Image.new("RGB", (2400, 1200), "#2563EB")
        prepared = prepare_image(image)
        self.assertLessEqual(max(prepared.width, prepared.height), 1600)
        self.assertLessEqual(len(prepared.jpeg_bytes), 800 * 1024)
        self.assertTrue(prepared.thumbnail_jpeg)
        self.assertTrue(prepared.is_blank)

    def test_distinct_pages_have_nonzero_hash_distance(self) -> None:
        first = Image.new("RGB", (320, 200), "white")
        second = first.copy()
        for x in range(160, 320):
            for y in range(200):
                second.putpixel((x, y), (0, 0, 0))
        self.assertGreater(
            hash_distance(prepare_image(first).page_hash, prepare_image(second).page_hash),
            0,
        )


class PageDetectionTests(unittest.TestCase):
    def test_page_requires_stability_and_change_threshold(self) -> None:
        detector = PageChangeDetector(stable_seconds=2, change_threshold=5)
        self.assertIsNone(detector.observe(0x0000, 0.0))
        self.assertIsNone(detector.observe(0x0000, 1.9))
        self.assertIsNotNone(detector.observe(0x0000, 2.0))
        self.assertIsNone(detector.observe(0x0001, 5.0))
        self.assertIsNone(detector.observe(0xFFFF, 6.0))
        self.assertIsNotNone(detector.observe(0xFFFF, 8.0))

    def test_blank_frame_never_becomes_page(self) -> None:
        detector = PageChangeDetector(stable_seconds=1)
        detector.observe(123, 0.0, is_blank=True)
        self.assertIsNone(detector.observe(123, 2.0, is_blank=True))


class SchedulingTests(unittest.TestCase):
    def test_busy_and_interval_keep_only_latest_page(self) -> None:
        scheduler = VisualAnalysisScheduler(min_interval_seconds=15)
        self.assertEqual(scheduler.offer("A", 0).action, "start")
        self.assertEqual(scheduler.offer("B", 1).action, "queued")
        self.assertEqual(scheduler.offer("C", 2).action, "queued")
        self.assertEqual(scheduler.complete(2).action, "queued")
        decision = scheduler.poll(15)
        self.assertEqual(decision.action, "start")
        self.assertEqual(decision.candidate, "C")

    def test_manual_request_has_priority_and_bypasses_hourly_limit(self) -> None:
        scheduler = VisualAnalysisScheduler(
            min_interval_seconds=0,
            max_auto_per_hour=1,
        )
        self.assertEqual(scheduler.offer("auto", 0).action, "start")
        scheduler.complete(1)
        self.assertEqual(scheduler.offer("blocked", 2).action, "rate_limited")
        self.assertEqual(
            scheduler.offer("manual", 3, manual=True).action,
            "start",
        )

    def test_manual_pending_is_not_overwritten_by_auto_page(self) -> None:
        scheduler = VisualAnalysisScheduler(min_interval_seconds=0)
        scheduler.offer("active", 0)
        scheduler.offer("manual", 1, manual=True)
        scheduler.offer("new-auto", 2)
        decision = scheduler.complete(3)
        self.assertEqual(decision.candidate, "manual")


class VisionClientTests(unittest.TestCase):
    def test_base64_request_and_structured_response(self) -> None:
        captured: dict[str, object] = {}

        def opener(request: object, timeout: int) -> _Response:
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _Response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "title": "销售看板",
                                        "summary_points": ["增长 10%"],
                                        "visible_text": ["FY26"],
                                        "metrics": ["10%"],
                                        "terms": [],
                                        "action_items": [],
                                        "risks": [],
                                        "open_questions": ["口径是否一致"],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 22, "completion_tokens": 9},
                }
            )

        client = OpenAICompatibleVisionClient(
            "secret",
            "https://vision.example.com/v1",
            "vision-model",
            opener=opener,
            retry_delays=(),
        )
        result = client.analyze(b"jpeg-data")
        body = captured["body"]
        image_url = body["messages"][1]["content"][1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/jpeg;base64,"))
        self.assertEqual(result.title, "销售看板")
        self.assertEqual(result.usage.input_tokens, 22)

    def test_transient_network_error_is_retried(self) -> None:
        attempts = 0
        sleeps: list[float] = []

        def opener(_request: object, timeout: int) -> _Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise URLError("temporary")
            return _Response(
                {
                    "choices": [
                        {"message": {"content": '{"title":"ok"}'}}
                    ]
                }
            )

        client = OpenAICompatibleVisionClient(
            "",
            "http://localhost:11434/v1",
            "vision-model",
            opener=opener,
            sleep=sleeps.append,
            retry_delays=(0.25,),
        )
        self.assertEqual(client.analyze(b"jpeg").title, "ok")
        self.assertEqual(attempts, 2)
        self.assertEqual(sleeps, [0.25])

    def test_json_code_fence_is_accepted(self) -> None:
        parsed = parse_visual_analysis('```json\n{"title":"路线图"}\n```')
        self.assertEqual(parsed.title, "路线图")


class VisualTimelineTests(unittest.TestCase):
    def test_minutes_context_selects_important_pages_across_full_meeting(self) -> None:
        now = datetime(2026, 7, 20, 9, 0, 0)
        moments = tuple(
            VisualMoment(
                index,
                now + timedelta(minutes=index),
                index,
                (
                    VisualAnalysis(
                        title="早期关键决策",
                        action_items=("确认预算",),
                    )
                    if index == 1
                    else _analysis(f"普通页面 {index}")
                ),
            )
            for index in range(1, 21)
        )
        selected = select_visual_moments(
            moments,
            limit=12,
            key_pages=True,
        )
        self.assertIn(1, [item.sequence for item in selected])
        self.assertEqual(len(selected), 12)

    def test_context_budget_does_not_drop_early_selected_page(self) -> None:
        now = datetime(2026, 7, 20, 9, 0, 0)
        moments = tuple(
            VisualMoment(
                index,
                now + timedelta(minutes=index),
                index,
                VisualAnalysis(
                    title=("早期关键页" if index == 1 else f"页面 {index}"),
                    summary_points=("很长的摘要" * 80,),
                    action_items=(("确认早期预算",) if index == 1 else ()),
                ),
            )
            for index in range(1, 14)
        )
        context = format_visual_context(
            moments,
            max_chars=1_200,
            key_pages=True,
        )
        self.assertLessEqual(len(context), 1_200)
        self.assertIn("第1页", context)
        self.assertIn("第13页", context)

    def test_old_images_are_released_but_text_is_kept(self) -> None:
        now = datetime(2026, 7, 20, 9, 0, 0)
        moments = tuple(
            VisualMoment(
                index,
                now + timedelta(seconds=index),
                index,
                _analysis(str(index)),
                b"full",
                b"thumb",
            )
            for index in range(1, 6)
        )
        trimmed = trim_visual_media(moments, full_limit=2, thumbnail_limit=4)
        self.assertIsNone(trimmed[0].image_jpeg)
        self.assertIsNone(trimmed[0].thumbnail_jpeg)
        self.assertEqual(trimmed[-1].analysis.title, "5")
        self.assertEqual(trimmed[-1].image_jpeg, b"full")

    def test_visual_context_links_turns_until_next_page(self) -> None:
        now = datetime(2026, 7, 20, 10, 0, 0)
        moments = (
            VisualMoment(1, now, 1, _analysis("第一页")),
            VisualMoment(2, now + timedelta(minutes=2), 2, _analysis("第二页")),
        )
        turns = (
            MeetingTurn(
                now + timedelta(minutes=1),
                "incoming",
                "Revenue is growing.",
                "收入正在增长。",
            ),
            MeetingTurn(
                now + timedelta(minutes=3),
                "outgoing",
                "确认风险。",
                "Confirm the risk.",
            ),
        )
        context = format_visual_context(moments, turns)
        first_block, second_block = context.split("\n\n", 1)
        self.assertIn("收入正在增长", first_block)
        self.assertNotIn("确认风险", first_block)
        self.assertIn("确认风险", second_block)


if __name__ == "__main__":
    unittest.main()
