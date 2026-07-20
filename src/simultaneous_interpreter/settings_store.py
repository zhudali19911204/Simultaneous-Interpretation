from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .meeting_minutes import DEFAULT_MINUTES_MODEL, normalize_chat_url, normalize_model_name
from .provider_config import (
    INTERPRETER_BY_ID,
    INTERPRETER_QWEN,
    LLM_BY_ID,
    LLM_QWEN_WORKSPACE,
    VISION_BY_ID,
    VISION_QWEN_WORKSPACE,
    normalize_provider_id,
    parse_extra_body,
)
from .qwen_backend import MODEL_NAME, normalize_realtime_model_name
from .visual_analysis import DEFAULT_VISION_MODEL, normalize_vision_model


APP_DIR_NAME = "SimultaneousInterpreter"
SETTINGS_FILE_NAME = "settings.json"


@dataclass(frozen=True)
class AppSettings:
    interpreter_provider: str = INTERPRETER_QWEN
    interpreter_model: str = MODEL_NAME
    interpreter_websocket_url: str = ""
    meeting_minutes_provider: str = LLM_QWEN_WORKSPACE
    meeting_minutes_model: str = DEFAULT_MINUTES_MODEL
    meeting_minutes_api_url: str = ""
    meeting_minutes_extra_body: str = "{}"
    visual_provider: str = VISION_QWEN_WORKSPACE
    visual_model: str = DEFAULT_VISION_MODEL
    visual_api_url: str = ""
    visual_extra_body: str = "{}"
    visual_key_source: str = "interpreter"


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

    defaults = AppSettings()

    def string_value(name: str, default: str) -> str:
        value = raw.get(name, default)
        return value if isinstance(value, str) else default

    interpreter_provider = normalize_provider_id(
        string_value("interpreter_provider", defaults.interpreter_provider),
        INTERPRETER_BY_ID,
        defaults.interpreter_provider,
    )
    minutes_provider = normalize_provider_id(
        string_value("meeting_minutes_provider", defaults.meeting_minutes_provider),
        LLM_BY_ID,
        defaults.meeting_minutes_provider,
    )
    visual_provider = normalize_provider_id(
        string_value("visual_provider", defaults.visual_provider),
        VISION_BY_ID,
        defaults.visual_provider,
    )
    try:
        interpreter_model = normalize_realtime_model_name(
            string_value("interpreter_model", defaults.interpreter_model)
        )
    except ValueError:
        interpreter_model = defaults.interpreter_model
    try:
        minutes_model = normalize_model_name(
            string_value("meeting_minutes_model", defaults.meeting_minutes_model)
        )
    except ValueError:
        minutes_model = defaults.meeting_minutes_model
    try:
        visual_model = normalize_vision_model(
            string_value("visual_model", defaults.visual_model)
        )
    except ValueError:
        visual_model = defaults.visual_model
    extra_body = string_value(
        "meeting_minutes_extra_body", defaults.meeting_minutes_extra_body
    )
    try:
        parse_extra_body(extra_body)
    except ValueError:
        extra_body = defaults.meeting_minutes_extra_body
    visual_extra_body = string_value(
        "visual_extra_body", defaults.visual_extra_body
    )
    try:
        parse_extra_body(visual_extra_body)
    except ValueError:
        visual_extra_body = defaults.visual_extra_body
    visual_key_source = string_value(
        "visual_key_source", defaults.visual_key_source
    )
    if visual_key_source not in {"independent", "minutes", "interpreter"}:
        visual_key_source = defaults.visual_key_source
    return AppSettings(
        interpreter_provider=interpreter_provider,
        interpreter_model=interpreter_model,
        interpreter_websocket_url=string_value("interpreter_websocket_url", ""),
        meeting_minutes_provider=minutes_provider,
        meeting_minutes_model=minutes_model,
        meeting_minutes_api_url=string_value("meeting_minutes_api_url", ""),
        meeting_minutes_extra_body=extra_body,
        visual_provider=visual_provider,
        visual_model=visual_model,
        visual_api_url=string_value("visual_api_url", ""),
        visual_extra_body=visual_extra_body,
        visual_key_source=visual_key_source,
    )


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    settings_path = path or default_settings_path()
    interpreter_provider = normalize_provider_id(
        settings.interpreter_provider,
        INTERPRETER_BY_ID,
        INTERPRETER_QWEN,
    )
    minutes_provider = normalize_provider_id(
        settings.meeting_minutes_provider,
        LLM_BY_ID,
        LLM_QWEN_WORKSPACE,
    )
    visual_provider = normalize_provider_id(
        settings.visual_provider,
        VISION_BY_ID,
        VISION_QWEN_WORKSPACE,
    )
    interpreter_model = normalize_realtime_model_name(settings.interpreter_model)
    minutes_model = normalize_model_name(settings.meeting_minutes_model)
    visual_model = normalize_vision_model(settings.visual_model)
    api_url = settings.meeting_minutes_api_url.strip()
    if api_url:
        api_url = normalize_chat_url(api_url)
    extra_body = settings.meeting_minutes_extra_body.strip() or "{}"
    parse_extra_body(extra_body)
    visual_api_url = settings.visual_api_url.strip()
    if visual_api_url:
        visual_api_url = normalize_chat_url(visual_api_url)
    visual_extra_body = settings.visual_extra_body.strip() or "{}"
    parse_extra_body(visual_extra_body)
    if settings.visual_key_source not in {"independent", "minutes", "interpreter"}:
        raise ValueError("共享画面 API Key 来源无效")
    payload: dict[str, Any] = {
        "interpreter_provider": interpreter_provider,
        "interpreter_model": interpreter_model,
        "interpreter_websocket_url": settings.interpreter_websocket_url.strip(),
        "meeting_minutes_provider": minutes_provider,
        "meeting_minutes_model": minutes_model,
        "meeting_minutes_api_url": api_url,
        "meeting_minutes_extra_body": extra_body,
        "visual_provider": visual_provider,
        "visual_model": visual_model,
        "visual_api_url": visual_api_url,
        "visual_extra_body": visual_extra_body,
        "visual_key_source": settings.visual_key_source,
    }
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
