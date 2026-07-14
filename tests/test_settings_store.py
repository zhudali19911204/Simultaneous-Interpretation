from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.meeting_minutes import DEFAULT_MINUTES_MODEL  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
