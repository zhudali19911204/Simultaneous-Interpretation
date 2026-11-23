from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.app import InterpreterApp  # noqa: E402
from simultaneous_interpreter.provider_config import (  # noqa: E402
    INTERPRETER_QWEN, INTERPRETER_QWEN_COMPATIBLE,
)
from simultaneous_interpreter.qwen_backend import (  # noqa: E402
    ConnectionStatus, QWEN35_MODEL_NAME, QWEN38_MODEL_NAME,
)


class FakeButton:
    def __init__(self) -> None:
        self.state = ""
        self.text = ""

    def configure(self, **options) -> None:
        self.state = options.get("state", self.state)
        self.text = options.get("text", self.text)


class FakeVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class FakeOverlay:
    def __init__(self) -> None:
        self.calls = []

    def set_connection_notice(self, text, **options) -> None:
        self.calls.append((text, options))

    def clear_connection_notice(self) -> None:
        self.calls.append(("", {}))


def make_app_shell():
    app = InterpreterApp.__new__(InterpreterApp)
    app._state = "running"
    app._connection_states = {}
    app._had_connection_interruption = False
    app._meeting_turns = [object()]
    app.output_var = FakeVar("CABLE Input")
    app.test_button = FakeButton()
    app._subtitle_overlay = FakeOverlay()
    status_calls = []
    app._set_status = lambda *args, **kwargs: status_calls.append((args, kwargs))
    return app, status_calls


class AppConnectionStatusTests(unittest.TestCase):
    def test_fast_subtitles_only_appear_for_official_qwen38(self) -> None:
        app = InterpreterApp.__new__(InterpreterApp)
        app._settings_window = Mock()
        app.fast_subtitle_options = Mock()
        app.interpreter_model_var = FakeVar(QWEN38_MODEL_NAME)
        app._interpreter_provider_id = Mock(return_value=INTERPRETER_QWEN)
        app._sync_fast_subtitle_control()
        app.fast_subtitle_options.grid.assert_called_once_with()
        app.interpreter_model_var.value = QWEN35_MODEL_NAME
        app._sync_fast_subtitle_control()
        app.fast_subtitle_options.grid_remove.assert_called_once_with()
        app.interpreter_model_var.value = QWEN38_MODEL_NAME
        app._interpreter_provider_id.return_value = INTERPRETER_QWEN_COMPATIBLE
        app._sync_fast_subtitle_control()
        self.assertEqual(app.fast_subtitle_options.grid_remove.call_count, 2)

    def test_cancel_restores_fast_subtitle_setting(self) -> None:
        app = Mock()
        app.qwen38_fast_subtitles_var = tk.BooleanVar(master=tk.Tcl(), value=False)
        app._settings_variables.side_effect = lambda: InterpreterApp._settings_variables(app)
        app._settings_snapshot = {
            name: variable.get() for name, variable in app._settings_variables().items()
        }
        app.qwen38_fast_subtitles_var.set(True)
        InterpreterApp._cancel_settings(app)
        self.assertFalse(app.qwen38_fast_subtitles_var.get())

    def test_qwen38_disables_local_gate_and_switching_back_restores_control(self) -> None:
        app = InterpreterApp.__new__(InterpreterApp)
        app._state = "idle"
        app.voice_combo = FakeButton()
        app.voice_var = FakeVar(app.VOICE_CHOICES[0][0])
        app.silence_gate_check = FakeButton()
        app.interpreter_model_var = FakeVar("qwen3.8-livetranslate-flash-realtime")
        app._sync_voice_choices()
        self.assertEqual(app.silence_gate_check.state, "disabled")
        self.assertIn("服务端静音检测", app.silence_gate_check.text)

        app.interpreter_model_var.value = "qwen3.5-livetranslate-flash-realtime"
        app._sync_voice_choices()
        self.assertEqual(app.silence_gate_check.state, "normal")
        self.assertEqual(app.silence_gate_check.text, "省流量静音门控")

    def test_minutes_template_is_locked_only_while_generating(self) -> None:
        app = InterpreterApp.__new__(InterpreterApp)
        app._state = "idle"
        app._meeting_turns = [object()]
        app._minutes_generating = False
        app.minutes_button = FakeButton()
        app.minutes_template_combo = FakeButton()

        app._update_minutes_button()

        self.assertEqual(app.minutes_button.state, "normal")
        self.assertEqual(app.minutes_template_combo.state, "readonly")

        app._minutes_generating = True
        app._update_minutes_button()

        self.assertEqual(app.minutes_button.state, "disabled")
        self.assertEqual(app.minutes_template_combo.state, "disabled")

    def test_minutes_citation_routes_to_assistant_timeline(self) -> None:
        app = InterpreterApp.__new__(InterpreterApp)

        class FakeAssistant:
            def __init__(self) -> None:
                self.timestamps = []

            def show_timeline_at(self, timestamp: str) -> None:
                self.timestamps.append(timestamp)

        assistant = FakeAssistant()
        app._meeting_assistant = assistant
        show_calls = []
        app._show_meeting_assistant = lambda: show_calls.append(True)

        app._open_minutes_citation("09:42:18")

        self.assertEqual(show_calls, [True])
        self.assertEqual(assistant.timestamps, ["09:42:18"])

    def test_disabled_english_audio_keeps_test_output_disabled(self) -> None:
        app, status_calls = make_app_shell()
        app.output_var = FakeVar(app.OUTPUT_DISABLED_LABEL)

        app._handle_connection_status(ConnectionStatus("incoming", "connected"))
        app._handle_connection_status(ConnectionStatus("outgoing", "connected"))

        self.assertEqual(app.test_button.state, "disabled")
        self.assertEqual(
            status_calls[-1][0][0],
            "同传运行中（英文语音已关闭）",
        )

    def test_meeting_assistant_is_created_only_when_opened(self) -> None:
        app = InterpreterApp.__new__(InterpreterApp)
        app._meeting_assistant = None
        app.root = object()
        app._fonts = object()
        app._meeting_turns = []
        app._create_meeting_assistant_client = lambda: None

        class FakeAssistantWindow:
            instances = 0
            shows = 0

            def __init__(self, *_args):
                type(self).instances += 1

            def show(self):
                type(self).shows += 1

        with patch(
            "simultaneous_interpreter.app.MeetingAssistantWindow",
            FakeAssistantWindow,
        ):
            app._show_meeting_assistant()
            app._show_meeting_assistant()

        self.assertEqual(FakeAssistantWindow.instances, 1)
        self.assertEqual(FakeAssistantWindow.shows, 2)

    def test_reconnecting_direction_recovers_without_clearing_meeting(self) -> None:
        app, status_calls = make_app_shell()
        app._handle_connection_status(
            ConnectionStatus("incoming", "connected")
        )
        app._handle_connection_status(
            ConnectionStatus(
                "outgoing",
                "reconnecting",
                attempt=2,
                detail="TimeoutError",
            )
        )
        self.assertIn("中译英重连中（第 2 次）", status_calls[-1][0][0])
        self.assertEqual(app.test_button.state, "disabled")
        self.assertEqual(len(app._meeting_turns), 1)

        app._handle_connection_status(
            ConnectionStatus("outgoing", "connected", attempt=2)
        )
        self.assertEqual(status_calls[-1][0][0], "同传运行中")
        self.assertEqual(app.test_button.state, "normal")
        self.assertEqual(
            app._subtitle_overlay.calls[-1],
            ("连接已恢复", {"kind": "info", "clear_after_ms": 3_000}),
        )
        self.assertEqual(len(app._meeting_turns), 1)

    def test_non_retryable_direction_failure_is_visible(self) -> None:
        app, status_calls = make_app_shell()
        app._handle_connection_status(
            ConnectionStatus(
                "incoming",
                "failed",
                detail="服务错误 403：permission denied",
            )
        )
        self.assertIn("英译中已停止", status_calls[-1][0][0])
        self.assertEqual(status_calls[-1][0][1], "error")
        self.assertEqual(
            app._subtitle_overlay.calls[-1][0],
            "英译中连接失败，请检查设置",
        )
        self.assertEqual(len(app._meeting_turns), 1)


if __name__ == "__main__":
    unittest.main()
