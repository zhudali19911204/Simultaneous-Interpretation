from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.provider_config import (  # noqa: E402
    LLM_QWEN_WORKSPACE,
    parse_extra_body,
    resolve_minutes_url,
    resolve_vision_url,
    validate_visual_key_source,
)


class ProviderConfigTests(unittest.TestCase):
    def test_workspace_and_custom_minutes_urls(self) -> None:
        self.assertEqual(
            resolve_minutes_url(LLM_QWEN_WORKSPACE, "ws-demo", ""),
            "https://ws-demo.cn-beijing.maas.aliyuncs.com"
            "/compatible-mode/v1/chat/completions",
        )
        self.assertEqual(
            resolve_minutes_url("openai_compatible", "", "https://llm.example.com/v1"),
            "https://llm.example.com/v1/chat/completions",
        )

    def test_workspace_and_custom_vision_urls(self) -> None:
        self.assertEqual(
            resolve_vision_url("qwen_workspace", "ws-demo", ""),
            "https://ws-demo.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        self.assertEqual(
            resolve_vision_url("openai", "", "https://api.openai.com/v1"),
            "https://api.openai.com/v1/chat/completions",
        )

    def test_extra_body_must_be_json_object(self) -> None:
        self.assertEqual(parse_extra_body('{"top_p": 0.8}'), {"top_p": 0.8})
        self.assertEqual(parse_extra_body(""), {})
        with self.assertRaises(ValueError):
            parse_extra_body("[]")
        with self.assertRaises(ValueError):
            parse_extra_body("not-json")

    def test_visual_key_reuse_requires_matching_provider(self) -> None:
        validate_visual_key_source(
            "qwen_workspace",
            "interpreter",
            minutes_provider_id="deepseek",
            interpreter_provider_id="qwen_dashscope",
        )
        validate_visual_key_source(
            "deepseek",
            "minutes",
            minutes_provider_id="deepseek",
            interpreter_provider_id="qwen_dashscope",
        )
        with self.assertRaisesRegex(ValueError, "供应商相同"):
            validate_visual_key_source(
                "openai",
                "minutes",
                minutes_provider_id="deepseek",
                interpreter_provider_id="qwen_dashscope",
            )


if __name__ == "__main__":
    unittest.main()
