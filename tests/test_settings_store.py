from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.meeting_minutes import DEFAULT_MINUTES_MODEL  # noqa: E402
from simultaneous_interpreter.provider_config import INTERPRETER_QWEN_COMPATIBLE  # noqa: E402
from simultaneous_interpreter.settings_store import AppSettings, load_settings, save_settings  # noqa: E402


class SettingsStoreTests(unittest.TestCase):
    def test_load_missing_settings_returns_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertEqual(load_settings(path).meeting_minutes_model, DEFAULT_MINUTES_MODEL)

    def test_save_and_load_minutes_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "settings.json"
            save_settings(AppSettings(meeting_minutes_model="qwen-max"), path)
            self.assertEqual(load_settings(path).meeting_minutes_model, "qwen-max")

    def test_invalid_settings_fall_back_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"meeting_minutes_model": "bad model"}', encoding="utf-8")
            self.assertEqual(load_settings(path).meeting_minutes_model, DEFAULT_MINUTES_MODEL)

    def test_round_trip_provider_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            expected = AppSettings(
                interpreter_provider=INTERPRETER_QWEN_COMPATIBLE,
                interpreter_model="vendor/live-translate",
                interpreter_websocket_url="wss://gateway.example.com/realtime",
                meeting_minutes_provider="deepseek",
                meeting_minutes_model="deepseek-chat",
                meeting_minutes_api_url="https://api.deepseek.com",
                meeting_minutes_extra_body='{"stream": false}',
                visual_provider="openai",
                visual_model="gpt-4.1-mini",
                visual_api_url="https://api.openai.com/v1",
                visual_extra_body='{"top_p": 0.8}',
                visual_key_source="independent",
            )
            save_settings(expected, path)
            actual = load_settings(path)
            self.assertEqual(actual.interpreter_provider, expected.interpreter_provider)
            self.assertEqual(actual.interpreter_model, expected.interpreter_model)
            self.assertEqual(
                actual.interpreter_websocket_url,
                expected.interpreter_websocket_url,
            )
            self.assertEqual(actual.meeting_minutes_provider, "deepseek")
            self.assertEqual(
                actual.meeting_minutes_api_url,
                "https://api.deepseek.com/chat/completions",
            )
            self.assertEqual(actual.meeting_minutes_extra_body, '{"stream": false}')
            self.assertEqual(actual.visual_provider, "openai")
            self.assertEqual(actual.visual_model, "gpt-4.1-mini")
            self.assertEqual(
                actual.visual_api_url,
                "https://api.openai.com/v1/chat/completions",
            )
            self.assertEqual(actual.visual_extra_body, '{"top_p": 0.8}')
            self.assertEqual(actual.visual_key_source, "independent")

    def test_old_settings_gain_optional_visual_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                '{"meeting_minutes_model":"qwen-max"}',
                encoding="utf-8",
            )
            settings = load_settings(path)
            self.assertEqual(settings.visual_model, "qwen3-vl-plus")
            self.assertEqual(settings.visual_key_source, "interpreter")

if __name__ == "__main__":
    unittest.main()
