from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .meeting_minutes import DEFAULT_MINUTES_MODEL, normalize_model_name


APP_DIR_NAME = "SimultaneousInterpreter"
SETTINGS_FILE_NAME = "settings.json"


@dataclass(frozen=True)
class AppSettings:
    meeting_minutes_model: str = DEFAULT_MINUTES_MODEL


def default_settings_path() -> Path:
    base = os.getenv("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME / SETTINGS_FILE_NAME
    return Path.home() / "AppData" / "Roaming" / APP_DIR_NAME / SETTINGS_FILE_NAME


def load_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or default_settings_path()
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppSettings()
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    if not isinstance(raw, dict):
        return AppSettings()

    model = raw.get("meeting_minutes_model", DEFAULT_MINUTES_MODEL)
    if not isinstance(model, str):
        return AppSettings()
    try:
        model = normalize_model_name(model)
    except ValueError:
        model = DEFAULT_MINUTES_MODEL
    return AppSettings(meeting_minutes_model=model)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    settings_path = path or default_settings_path()
    model = normalize_model_name(settings.meeting_minutes_model)
    payload: dict[str, Any] = {"meeting_minutes_model": model}
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
