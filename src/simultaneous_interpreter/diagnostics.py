from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable


APP_DIR_NAME = "SimultaneousInterpreter"
LOG_FILE_NAME = "interpreter.log"
MAX_LOG_BYTES = 1_048_576
LOG_BACKUP_COUNT = 3


def default_log_path() -> Path:
    base = os.getenv("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME / "logs" / LOG_FILE_NAME
    return (
        Path.home()
        / "AppData"
        / "Roaming"
        / APP_DIR_NAME
        / "logs"
        / LOG_FILE_NAME
    )


def sanitize_diagnostic(
    detail: object,
    secrets: Iterable[str] = (),
) -> str:
    safe = re.sub(r"\s+", " ", str(detail or "")).strip()
    for secret in sorted(
        {value for value in secrets if value},
        key=len,
        reverse=True,
    ):
        safe = safe.replace(secret, "[redacted]")
    safe = re.sub(
        r"(?i)authorization\s*[:=]\s*bearer\s+[^\s,;]+",
        "Authorization: Bearer [redacted]",
        safe,
    )
    safe = re.sub(
        r"(?i)bearer\s+[^\s,;]+",
        "Bearer [redacted]",
        safe,
    )
    safe = re.sub(r"(?i)\b(?:ws|wss|http|https)://[^\s]+", "[redacted-url]", safe)
    return safe[:500]


class ConnectionDiagnosticLogger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_log_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        logger_name = f"simultaneous_interpreter.connection.{self.path}"
        self._logger = logging.getLogger(logger_name)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        if not self._logger.handlers:
            handler = RotatingFileHandler(
                self.path,
                maxBytes=MAX_LOG_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            self._logger.addHandler(handler)

    def log(self, status: object) -> None:
        direction = sanitize_diagnostic(getattr(status, "direction", "unknown"))
        state = sanitize_diagnostic(getattr(status, "state", "unknown"))
        attempt = int(getattr(status, "attempt", 0) or 0)
        detail = sanitize_diagnostic(getattr(status, "detail", ""))
        self._logger.info(
            "direction=%s state=%s attempt=%d detail=%s",
            direction,
            state,
            attempt,
            detail or "-",
        )

    def close(self) -> None:
        for handler in tuple(self._logger.handlers):
            handler.close()
            self._logger.removeHandler(handler)
