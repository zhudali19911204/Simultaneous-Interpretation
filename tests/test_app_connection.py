from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.app import InterpreterApp  # noqa: E402
from simultaneous_interpreter.qwen_backend import ConnectionStatus  # noqa: E402


class FakeButton:
    def __init__(self) -> None:
        self.state = ""

    def configure(self, **options) -> None:
        self.state = options.get("state", self.state)


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
    app.test_button = FakeButton()
    app._subtitle_overlay = FakeOverlay()
    status_calls = []
    app._set_status = lambda *args, **kwargs: status_calls.append((args, kwargs))
    return app, status_calls


class AppConnectionStatusTests(unittest.TestCase):
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
