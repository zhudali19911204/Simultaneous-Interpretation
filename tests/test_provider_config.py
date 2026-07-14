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

    def test_extra_body_must_be_json_object(self) -> None:
        self.assertEqual(parse_extra_body('{"top_p": 0.8}'), {"top_p": 0.8})
        self.assertEqual(parse_extra_body(""), {})
        with self.assertRaises(ValueError):
            parse_extra_body("[]")
        with self.assertRaises(ValueError):
            parse_extra_body("not-json")


if __name__ == "__main__":
    unittest.main()
