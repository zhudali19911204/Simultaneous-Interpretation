from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .meeting_minutes import build_chat_url, normalize_chat_url


INTERPRETER_QWEN = "qwen_dashscope"
INTERPRETER_QWEN_COMPATIBLE = "qwen_livetranslate_compatible"
LLM_QWEN_WORKSPACE = "qwen_workspace"
LLM_CUSTOM = "openai_compatible"
VISION_QWEN_WORKSPACE = LLM_QWEN_WORKSPACE


@dataclass(frozen=True)
class ProviderPreset:
    provider_id: str
    label: str
    api_url: str = ""


INTERPRETER_PROVIDERS = (
    ProviderPreset(INTERPRETER_QWEN, "阿里云百炼 LiveTranslate"),
    ProviderPreset(
        INTERPRETER_QWEN_COMPATIBLE,
        "自定义 LiveTranslate 兼容接口",
    ),
)

LLM_PROVIDERS = (
    ProviderPreset(LLM_QWEN_WORKSPACE, "阿里云百炼（当前 Workspace）"),
    ProviderPreset(
        "deepseek",
        "DeepSeek（OpenAI 兼容）",
        "https://api.deepseek.com/chat/completions",
    ),
    ProviderPreset(
        "moonshot",
        "Moonshot / Kimi（OpenAI 兼容）",
        "https://api.moonshot.cn/v1/chat/completions",
    ),
    ProviderPreset(
        "zhipu",
        "智谱 GLM（OpenAI 兼容）",
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    ),
    ProviderPreset(
        "siliconflow",
        "硅基流动（OpenAI 兼容）",
        "https://api.siliconflow.cn/v1/chat/completions",
    ),
    ProviderPreset(
        "openai",
        "OpenAI（Chat Completions）",
        "https://api.openai.com/v1/chat/completions",
    ),
    ProviderPreset(
        "ollama",
        "本地 Ollama（OpenAI 兼容）",
        "http://localhost:11434/v1/chat/completions",
    ),
    ProviderPreset(LLM_CUSTOM, "自定义 OpenAI 兼容接口"),
)

INTERPRETER_BY_ID = {item.provider_id: item for item in INTERPRETER_PROVIDERS}
INTERPRETER_BY_LABEL = {item.label: item for item in INTERPRETER_PROVIDERS}
LLM_BY_ID = {item.provider_id: item for item in LLM_PROVIDERS}
LLM_BY_LABEL = {item.label: item for item in LLM_PROVIDERS}
VISION_PROVIDERS = LLM_PROVIDERS
VISION_BY_ID = LLM_BY_ID
VISION_BY_LABEL = LLM_BY_LABEL


def normalize_provider_id(
    provider_id: str,
    providers: dict[str, ProviderPreset],
    default: str,
) -> str:
    normalized = provider_id.strip()
    return normalized if normalized in providers else default


def resolve_minutes_url(provider_id: str, workspace_id: str, api_url: str) -> str:
    if provider_id == LLM_QWEN_WORKSPACE:
        return build_chat_url(workspace_id)
    preset = LLM_BY_ID.get(provider_id)
    configured = api_url.strip() or (preset.api_url if preset else "")
    if not configured:
        raise ValueError("请填写会议纪要 Chat Completions 地址")
    return normalize_chat_url(configured)


def resolve_vision_url(provider_id: str, workspace_id: str, api_url: str) -> str:
    if provider_id == VISION_QWEN_WORKSPACE:
        return build_chat_url(workspace_id)
    preset = VISION_BY_ID.get(provider_id)
    configured = api_url.strip() or (preset.api_url if preset else "")
    if not configured:
        raise ValueError("请填写共享画面 AI 的 Chat Completions 地址")
    return normalize_chat_url(configured)


def validate_visual_key_source(
    visual_provider_id: str,
    key_source: str,
    *,
    minutes_provider_id: str,
    interpreter_provider_id: str,
) -> None:
    if key_source == "independent":
        return
    if key_source == "minutes":
        if visual_provider_id != minutes_provider_id:
            raise ValueError("只有视觉与会议纪要供应商相同时才能复用会议纪要 Key")
        return
    if key_source == "interpreter":
        if (
            visual_provider_id != VISION_QWEN_WORKSPACE
            or interpreter_provider_id != INTERPRETER_QWEN
        ):
            raise ValueError("只有百炼视觉模型才能复用百炼同传 Key")
        return
    raise ValueError("共享画面 API Key 来源无效")


def parse_extra_body(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"附加请求参数不是有效 JSON：{exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("附加请求参数必须是 JSON 对象")
    return value
