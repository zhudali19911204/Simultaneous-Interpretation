from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.diagnostics import (  # noqa: E402
    ConnectionDiagnosticLogger,
    sanitize_diagnostic,
)
from simultaneous_interpreter.qwen_backend import ConnectionStatus  # noqa: E402


class DiagnosticTests(unittest.TestCase):
    def test_sanitize_removes_credentials_and_urls(self) -> None:
        secret = "sk-sensitive-value"
        workspace = "ws-private123"
        detail = (
            "Authorization: Bearer sk-sensitive-value "
            "wss://ws-private123.example.com/realtime"
        )
        safe = sanitize_diagnostic(detail, (secret, workspace))
        self.assertNotIn(secret, safe)
        self.assertNotIn(workspace, safe)
        self.assertNotIn("wss://", safe)
        self.assertIn("[redacted]", safe)

    def test_connection_log_contains_only_diagnostic_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs" / "interpreter.log"
            logger = ConnectionDiagnosticLogger(path)
            logger.log(
                ConnectionStatus(
                    direction="outgoing",
                    state="reconnecting",
                    attempt=2,
                    detail="TimeoutError: ping timeout",
                )
            )
            logger.close()
            text = path.read_text(encoding="utf-8")
            self.assertIn("direction=outgoing", text)
            self.assertIn("state=reconnecting", text)
            self.assertIn("attempt=2", text)
            self.assertIn("TimeoutError", text)

    def test_visual_log_contains_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs" / "interpreter.log"
            logger = ConnectionDiagnosticLogger(path)
            logger.log_visual(
                state="analysis_ready",
                page=3,
                elapsed_ms=1250,
                error_type="",
            )
            logger.close()
            text = path.read_text(encoding="utf-8")
            self.assertIn("feature=visual", text)
            self.assertIn("page=3", text)
            self.assertIn("elapsed_ms=1250", text)


if __name__ == "__main__":
    unittest.main()
