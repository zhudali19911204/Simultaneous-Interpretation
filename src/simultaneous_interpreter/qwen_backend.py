from __future__ import annotations

import base64
import json
import queue
import re
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import numpy as np
import websocket

from .diagnostics import sanitize_diagnostic


INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
BLOCK_FRAMES = 320  # 20 ms of 16-bit mono PCM for lower capture latency
CAPTURE_QUEUE_BLOCKS = 50  # absorb up to one second of stalled sends
MAX_APPEND_BLOCKS = 8
PLAYBACK_BLOCK_FRAMES = 480  # 20 ms at 24 kHz
QWEN35_MODEL_NAME = "qwen3.5-livetranslate-flash-realtime"
QWEN38_MODEL_NAME = "qwen3.8-livetranslate-flash-realtime"
MODEL_NAME = QWEN38_MODEL_NAME
GATE_THRESHOLD_RMS = 0.004
GATE_PRE_ROLL_BLOCKS = 8  # 160 ms
# Keep sending silence slightly beyond the typical server VAD boundary. Stopping
# exactly at the boundary can delay finalization until the next speech segment.
GATE_HANGOVER_BLOCKS = 50  # 1000 ms
PING_INTERVAL_SECONDS = 30
PING_TIMEOUT_SECONDS = 20
INITIAL_CONNECTION_TIMEOUT_SECONDS = 15.0
RECONNECT_DELAYS_SECONDS = (2.0, 5.0, 10.0, 20.0, 30.0)
MAX_CONNECTION_ATTEMPTS_PER_WINDOW = 8
CONNECTION_ATTEMPT_WINDOW_SECONDS = 60.0
ALIGNMENT_TIMEOUT_SECONDS = 5.0

_DNS_FAILURE_MARKERS = (
    "getaddrinfo",
    "websocketaddressexception",
    "gaierror",
    "name or service not known",
    "nodename nor servname",
    "errno 11001",
    "errno 11002",
)
_TRANSIENT_STARTUP_MARKERS = (
    *_DNS_FAILURE_MARKERS,
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "remote host was lost",
    "status code 429",
    "status code 500",
    "status code 502",
    "status code 503",
    "status code 504",
    "rate_limit",
    "internal_error",
)


@dataclass(frozen=True)
class TranslationEvent:
    source_text: str
    translated_text: str
    is_final: bool
    alignment_status: str = "matched"
    segment_id: str = ""


@dataclass(frozen=True)
class AlignmentOutput:
    event: TranslationEvent
    segment_index: int
    reason: str
    first_target: str = "none"
    first_target_ms: int | None = None


@dataclass
class _AlignmentSlot:
    index: int
    speech_started_at: float | None = None
    source_item_id: str = ""
    response_id: str = ""
    output_item_id: str = ""
    source_preview: str = ""
    translated_preview: str = ""
    source_final: str | None = None
    translated_final: str | None = None
    idle_since: float | None = None
    first_target: str = "none"
    first_target_ms: int | None = None


class TranslationAligner:
    """Pairs source transcripts and translations without assuming completion order."""

    def __init__(
        self,
        *,
        timeout_seconds: float = ALIGNMENT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("alignment timeout must be greater than zero")
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._next_index = 1
        self._slots: deque[_AlignmentSlot] = deque()
        self._source_slots: dict[str, _AlignmentSlot] = {}
        self._response_slots: dict[str, _AlignmentSlot] = {}
        self._ignored_source_ids: set[str] = set()
        self._ignored_response_ids: set[str] = set()
        self._active_speech: _AlignmentSlot | None = None

    def reset(self) -> None:
        self._next_index = 1
        self._slots.clear()
        self._source_slots.clear()
        self._response_slots.clear()
        self._ignored_source_ids.clear()
        self._ignored_response_ids.clear()
        self._active_speech = None

    def speech_started(self) -> tuple[AlignmentOutput, ...]:
        now = self._clock()
        if self._active_speech is not None:
            self._start_waiting(self._active_speech, now)
        self._active_speech = self._new_slot(now)
        self._active_speech.speech_started_at = now
        return self._drain(now)

    def speech_stopped(self) -> tuple[AlignmentOutput, ...]:
        now = self._clock()
        if self._active_speech is not None:
            self._start_waiting(self._active_speech, now)
            self._active_speech = None
        return self._drain(now)

    def source_preview(
        self,
        item_id: str,
        text: str,
    ) -> tuple[AlignmentOutput, ...]:
        normalized_id = item_id.strip()
        if normalized_id and normalized_id in self._ignored_source_ids:
            return ()
        slot = self._source_slot(normalized_id)
        preview = text.strip()
        if slot.source_final is not None or slot.source_preview == preview:
            return ()
        slot.source_preview = preview
        self._note_progress(slot)
        return self._preview(slot)

    def source_delta(
        self,
        item_id: str,
        delta: str,
    ) -> tuple[AlignmentOutput, ...]:
        normalized_id = item_id.strip()
        if not delta or normalized_id in self._ignored_source_ids:
            return ()
        slot = self._source_slot(normalized_id)
        if slot.source_final is not None:
            return ()
        # Deltas can contain only whitespace; retain it between words.
        slot.source_preview += delta
        self._note_progress(slot)
        return self._preview(slot)

    def source_completed(
        self,
        item_id: str,
        transcript: str,
    ) -> tuple[AlignmentOutput, ...]:
        normalized_id = item_id.strip()
        if normalized_id and normalized_id in self._ignored_source_ids:
            return ()
        now = self._clock()
        slot = self._source_slot(normalized_id)
        if slot.source_final is None:
            slot.source_final = transcript.strip()
            slot.source_preview = slot.source_final
            slot.idle_since = now
        return self._after_completion(slot, now)

    def response_started(
        self,
        response_id: str,
        output_item_id: str = "",
    ) -> tuple[AlignmentOutput, ...]:
        normalized_id = response_id.strip()
        if normalized_id and normalized_id in self._ignored_response_ids:
            return ()
        slot = self._response_slot(normalized_id)
        if output_item_id:
            slot.output_item_id = output_item_id.strip()
        return self._drain(self._clock())

    def translation_preview(
        self,
        response_id: str,
        output_item_id: str,
        text: str,
    ) -> tuple[AlignmentOutput, ...]:
        normalized_id = response_id.strip()
        if normalized_id and normalized_id in self._ignored_response_ids:
            return ()
        slot = self._response_slot(normalized_id)
        if output_item_id:
            slot.output_item_id = output_item_id.strip()
        preview = text.strip()
        if slot.translated_final is not None or slot.translated_preview == preview:
            return ()
        slot.translated_preview = preview
        self._note_first_target(slot, preview, "preview")
        self._note_progress(slot)
        return self._preview(slot)

    def translation_delta(
        self,
        response_id: str,
        output_item_id: str,
        delta: str,
    ) -> tuple[AlignmentOutput, ...]:
        normalized_id = response_id.strip()
        if not delta or normalized_id in self._ignored_response_ids:
            return ()
        slot = self._response_slot(normalized_id)
        if slot.translated_final is not None:
            return ()
        if output_item_id:
            slot.output_item_id = output_item_id.strip()
        slot.translated_preview += delta
        self._note_first_target(slot, delta, "preview")
        self._note_progress(slot)
        return self._preview(slot)

    def translation_completed(
        self,
        response_id: str,
        output_item_id: str,
        translated: str,
    ) -> tuple[AlignmentOutput, ...]:
        normalized_id = response_id.strip()
        if normalized_id and normalized_id in self._ignored_response_ids:
            return ()
        now = self._clock()
        slot = self._response_slot(normalized_id)
        if output_item_id:
            slot.output_item_id = output_item_id.strip()
        if slot.translated_final is None:
            slot.translated_final = translated.strip()
            slot.translated_preview = slot.translated_final
            slot.idle_since = now
            self._note_first_target(slot, slot.translated_final, "final_only")
        return self._after_completion(slot, now)

    def expire(self) -> tuple[AlignmentOutput, ...]:
        return self._drain(self._clock())

    def flush(self, reason: str) -> tuple[AlignmentOutput, ...]:
        outputs: list[AlignmentOutput] = []
        while self._slots:
            slot = self._slots[0]
            output = self._finish_slot(slot, reason)
            if output is not None:
                outputs.append(output)
        self._active_speech = None
        return tuple(outputs)

    def expiry_deadline(self) -> float | None:
        if not self._slots:
            return None
        idle_since = self._slots[0].idle_since
        if idle_since is None:
            return None
        return idle_since + self._timeout_seconds

    def seconds_until_expiry(self) -> float | None:
        deadline = self.expiry_deadline()
        if deadline is None:
            return None
        return max(0.0, deadline - self._clock())

    def _new_slot(self, now: float) -> _AlignmentSlot:
        if self._slots:
            self._start_waiting(self._slots[-1], now)
        slot = _AlignmentSlot(index=self._next_index)
        self._next_index += 1
        self._slots.append(slot)
        return slot

    def _source_slot(self, item_id: str) -> _AlignmentSlot:
        if item_id and item_id in self._source_slots:
            return self._source_slots[item_id]
        slot = next(
            (
                candidate
                for candidate in self._slots
                if not candidate.source_item_id
                and candidate.source_final is None
            ),
            None,
        )
        if slot is None:
            slot = self._new_slot(self._clock())
        if item_id:
            slot.source_item_id = item_id
            self._source_slots[item_id] = slot
        return slot

    def _response_slot(self, response_id: str) -> _AlignmentSlot:
        if response_id and response_id in self._response_slots:
            return self._response_slots[response_id]
        slot = next(
            (
                candidate
                for candidate in self._slots
                if not candidate.response_id
                and candidate.translated_final is None
            ),
            None,
        )
        if slot is None:
            slot = self._new_slot(self._clock())
        if response_id:
            slot.response_id = response_id
            self._response_slots[response_id] = slot
        return slot

    @staticmethod
    def _start_waiting(slot: _AlignmentSlot, now: float) -> None:
        if slot.idle_since is None:
            slot.idle_since = now

    def _note_progress(self, slot: _AlignmentSlot) -> None:
        if slot.idle_since is not None:
            slot.idle_since = self._clock()

    def _note_first_target(self, slot: _AlignmentSlot, text: str, mode: str) -> None:
        if slot.first_target != "none" or not text.strip():
            return
        slot.first_target = mode
        if slot.speech_started_at is not None:
            elapsed = self._clock() - slot.speech_started_at
            slot.first_target_ms = round(elapsed * 1000)

    def _after_completion(
        self,
        slot: _AlignmentSlot,
        now: float,
    ) -> tuple[AlignmentOutput, ...]:
        outputs = self._drain(now)
        # Draining older final events can leave this sentence pending.
        if self._slots and self._slots[0].index <= slot.index:
            return outputs + self._preview(slot)
        return outputs

    def _preview(self, slot: _AlignmentSlot) -> tuple[AlignmentOutput, ...]:
        source = (
            slot.source_final
            if slot.source_final is not None
            else slot.source_preview.strip()
        )
        translated = (
            slot.translated_final
            if slot.translated_final is not None
            else slot.translated_preview.strip()
        )
        if not (source or translated):
            return ()
        return (
            AlignmentOutput(
                event=TranslationEvent(source, translated, False),
                segment_index=slot.index,
                reason="preview",
            ),
        )

    def _drain(self, now: float) -> tuple[AlignmentOutput, ...]:
        outputs: list[AlignmentOutput] = []
        while self._slots:
            slot = self._slots[0]
            both_completed = (
                slot.source_final is not None
                and slot.translated_final is not None
            )
            expired = (
                slot.idle_since is not None
                and now - slot.idle_since >= self._timeout_seconds
            )
            if not both_completed and not expired:
                break
            output = self._finish_slot(
                slot,
                "completed" if both_completed else "timeout",
            )
            if output is not None:
                outputs.append(output)
        return tuple(outputs)

    def _finish_slot(
        self,
        slot: _AlignmentSlot,
        reason: str,
    ) -> AlignmentOutput | None:
        if not self._slots or self._slots[0] is not slot:
            return None
        self._slots.popleft()
        if self._active_speech is slot:
            self._active_speech = None
        if slot.source_item_id:
            self._source_slots.pop(slot.source_item_id, None)
            self._ignored_source_ids.add(slot.source_item_id)
        if slot.response_id:
            self._response_slots.pop(slot.response_id, None)
            self._ignored_response_ids.add(slot.response_id)

        source = slot.source_final or ""
        translated = slot.translated_final or ""
        if not (source or translated):
            return None
        if source and translated:
            status = "matched"
        elif source:
            status = "source_only"
        else:
            status = "translation_only"
        return AlignmentOutput(
            event=TranslationEvent(
                source,
                translated,
                True,
                alignment_status=status,
            ),
            segment_index=slot.index,
            reason=reason,
            first_target=slot.first_target,
            first_target_ms=slot.first_target_ms,
        )


@dataclass(frozen=True)
class ConnectionStatus:
    direction: str
    state: str
    attempt: int = 0
    detail: str = ""


@dataclass(frozen=True)
class UsageStats:
    input_text_tokens: int = 0
    input_audio_tokens: int = 0
    output_text_tokens: int = 0
    output_audio_tokens: int = 0

    def __add__(self, other: "UsageStats") -> "UsageStats":
        return UsageStats(
            self.input_text_tokens + other.input_text_tokens,
            self.input_audio_tokens + other.input_audio_tokens,
            self.output_text_tokens + other.output_text_tokens,
            self.output_audio_tokens + other.output_audio_tokens,
        )

    @property
    def total_tokens(self) -> int:
        return (
            self.input_text_tokens
            + self.input_audio_tokens
            + self.output_text_tokens
            + self.output_audio_tokens
        )

    @classmethod
    def from_response(cls, response: dict[str, Any]) -> "UsageStats":
        usage = response.get("usage") or {}
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        return cls(
            input_text_tokens=int(input_details.get("text_tokens", 0) or 0),
            input_audio_tokens=int(input_details.get("audio_tokens", 0) or 0),
            output_text_tokens=int(output_details.get("text_tokens", 0) or 0),
            output_audio_tokens=int(output_details.get("audio_tokens", 0) or 0),
        )


def _translation_from_response(response: dict[str, Any]) -> tuple[str, str]:
    output_item_id = ""
    parts: list[str] = []
    for item in response.get("output") or ():
        if not isinstance(item, dict):
            continue
        output_item_id = output_item_id or str(item.get("id", ""))
        for content in item.get("content") or ():
            if not isinstance(content, dict):
                continue
            text = str(
                content.get("transcript") or content.get("text") or ""
            ).strip()
            if text:
                parts.append(text)
    return output_item_id, " ".join(parts)


TranslationCallback = Callable[[TranslationEvent], None]
UsageCallback = Callable[[UsageStats], None]
MessageCallback = Callable[[str], None]
ConnectionStatusCallback = Callable[[ConnectionStatus], None]


def reconnect_delay(attempt: int) -> float:
    if attempt < 1:
        return 0.0
    index = min(attempt, len(RECONNECT_DELAYS_SECONDS)) - 1
    return RECONNECT_DELAYS_SECONDS[index]


def is_retryable_startup_failure(
    detail: str,
    close_status_code: int | None,
    transport_retryable: bool,
) -> bool:
    if not transport_retryable:
        return False
    normalized = detail.casefold()
    if any(marker in normalized for marker in _TRANSIENT_STARTUP_MARKERS):
        return True
    return close_status_code in {None, 1006}


def format_startup_failure(detail: str, retry_count: int) -> str:
    normalized = detail.casefold()
    retry_text = f"，已自动重试 {retry_count} 次" if retry_count else ""
    if any(marker in normalized for marker in _DNS_FAILURE_MARKERS):
        return (
            f"无法解析实时翻译服务域名（DNS）{retry_text}。"
            "请确认 WorkspaceId 属于华北2（北京），并检查当前网络、VPN、代理或公司 DNS；"
            f"也可切换手机热点验证。\n\n技术信息：{detail}"
        )
    return detail


class ConnectionAttemptLimiter:
    def __init__(
        self,
        max_attempts: int = MAX_CONNECTION_ATTEMPTS_PER_WINDOW,
        window_seconds: float = CONNECTION_ATTEMPT_WINDOW_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_attempts < 1 or window_seconds <= 0:
            raise ValueError("连接限速参数必须大于 0")
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._clock = clock
        self._attempts: deque[float] = deque()
        self._lock = threading.Lock()

    def reserve_delay(self) -> float:
        with self._lock:
            now = self._clock()
            cutoff = now - self._window_seconds
            while self._attempts and self._attempts[0] <= cutoff:
                self._attempts.popleft()
            if len(self._attempts) < self._max_attempts:
                self._attempts.append(now)
                return 0.0
            return max(0.0, self._attempts[0] + self._window_seconds - now)

    def wait_for_slot(self, stop_event: threading.Event) -> bool:
        while not stop_event.is_set():
            delay = self.reserve_delay()
            if delay <= 0:
                return True
            if stop_event.wait(delay):
                return False
        return False


def normalize_realtime_model_name(model: str) -> str:
    normalized = model.strip()
    if not normalized:
        raise ValueError("同传模型不能为空")
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+", normalized):
        raise ValueError("同传模型名称包含不支持的字符")
    return normalized


def uses_qwen38_protocol(model: str) -> bool:
    normalized = model.strip().rsplit("/", 1)[-1].casefold()
    return (
        normalized == QWEN38_MODEL_NAME
        or normalized.startswith(QWEN38_MODEL_NAME + "-")
    )


def supports_qwen38_fast_subtitles(model: str, websocket_url: str) -> bool:
    return not websocket_url.strip() and uses_qwen38_protocol(model)


def build_api_url(
    workspace_id: str,
    model: str = MODEL_NAME,
    websocket_url: str = "",
) -> str:
    normalized_model = normalize_realtime_model_name(model)
    custom_url = websocket_url.strip()
    if custom_url:
        if "{model}" in custom_url:
            custom_url = custom_url.replace("{model}", quote(normalized_model, safe=""))
        parts = urlsplit(custom_url)
        if parts.scheme not in {"ws", "wss"} or not parts.netloc:
            raise ValueError("同传 WebSocket 地址必须是有效的 ws:// 或 wss:// 地址")
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["model"] = normalized_model
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    normalized = workspace_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
        raise ValueError("WorkspaceId 只能包含字母、数字、下划线和连字符")
    return (
        f"wss://{normalized}.cn-beijing.maas.aliyuncs.com"
        f"/api-ws/v1/realtime?model={quote(normalized_model, safe='')}"
    )


def _pcm_from_float(block: Any) -> bytes:
    samples = np.asarray(block, dtype=np.float32)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    return (
        (np.clip(samples, -1.0, 1.0) * 32767.0)
        .astype("<i2", copy=False)
        .tobytes()
    )


class EnergySpeechGate:
    """Drops long silence while retaining speech edges for server-side VAD."""

    def __init__(self) -> None:
        self._pre_roll: deque[bytes] = deque(maxlen=GATE_PRE_ROLL_BLOCKS)
        self._active = False
        self._silent_blocks = 0

    def process(self, block: Any) -> list[bytes]:
        samples = np.asarray(block, dtype=np.float32)
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        pcm = _pcm_from_float(samples)

        if self._active:
            if rms >= GATE_THRESHOLD_RMS:
                self._silent_blocks = 0
            else:
                self._silent_blocks += 1
                if self._silent_blocks >= GATE_HANGOVER_BLOCKS:
                    self._active = False
                    self._silent_blocks = 0
                    self._pre_roll.clear()
            return [pcm]

        self._pre_roll.append(pcm)
        if rms < GATE_THRESHOLD_RMS:
            return []

        self._active = True
        self._silent_blocks = 0
        buffered = list(self._pre_roll)
        self._pre_roll.clear()
        return buffered


class _CaptureBuffer:
    """Bounded ordered handoff between a WASAPI recorder and one connection."""

    def __init__(self) -> None:
        self._blocks: deque[bytes] = deque()
        self._condition = threading.Condition()
        self.closed = False

    def put(self, blocks: list[bytes]) -> None:
        with self._condition:
            # Apply backpressure only when the full jitter allowance is used.
            self._condition.wait_for(
                lambda: self.closed
                or len(self._blocks) + len(blocks) <= CAPTURE_QUEUE_BLOCKS
            )
            if not self.closed:
                self._blocks.extend(blocks)
                self._condition.notify()

    def take(self) -> bytes:
        with self._condition:
            self._condition.wait_for(lambda: self.closed or self._blocks)
            count = min(len(self._blocks), MAX_APPEND_BLOCKS)
            data = b"".join(self._blocks.popleft() for _ in range(count))
            self._condition.notify()
            return data

    def close(self, *, discard: bool = False) -> None:
        with self._condition:
            self.closed = True
            if discard:
                self._blocks.clear()
            self._condition.notify_all()


class PcmCaptureWorker:
    def __init__(
        self,
        *,
        input_device: Any,
        on_error: MessageCallback,
        use_silence_gate: bool,
    ) -> None:
        self._input_device = input_device
        self._on_error = on_error
        self._use_silence_gate = use_silence_gate
        self._buffer = _CaptureBuffer()
        self._thread: threading.Thread | None = None
        self._sender: threading.Thread | None = None

    def start(self, on_audio: Callable[[bytes], None]) -> None:
        buffer = self._buffer = _CaptureBuffer()
        self._sender = threading.Thread(
            target=self._send,
            args=(buffer, on_audio),
            name="qwen-pcm-sender",
            daemon=True,
        )
        self._thread = threading.Thread(
            target=self._run,
            args=(buffer,),
            name="qwen-pcm-capture",
            daemon=True,
        )
        self._sender.start()
        self._thread.start()

    def request_stop(self) -> None:
        self._buffer.close(discard=True)

    def stop(self) -> bool:
        self._buffer.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._sender and self._sender.is_alive():
            self._sender.join(timeout=2.0)
            if self._sender.is_alive():
                self.request_stop()
                return False
        return True

    def _send(
        self, buffer: _CaptureBuffer, on_audio: Callable[[bytes], None]
    ) -> None:
        try:
            while pcm := buffer.take():
                on_audio(pcm)
        except Exception as exc:
            if not buffer.closed:
                self._on_error(f"发送音频失败：{exc}")
        finally:
            buffer.close(discard=True)

    def _run(self, buffer: _CaptureBuffer) -> None:
        try:
            gate = EnergySpeechGate()
            with self._input_device.recorder(
                samplerate=INPUT_SAMPLE_RATE,
                channels=1,
                blocksize=BLOCK_FRAMES,
            ) as recorder:
                while not buffer.closed:
                    block = recorder.record(numframes=BLOCK_FRAMES)
                    chunks = (
                        gate.process(block)
                        if self._use_silence_gate
                        else [_pcm_from_float(block)]
                    )
                    if chunks:
                        buffer.put(chunks)
        except Exception as exc:
            if not buffer.closed:
                self._on_error(f"音频捕获失败：{exc}")
        finally:
            buffer.close()


class PcmPlaybackWorker:
    def __init__(self, output_device: Any, on_error: MessageCallback) -> None:
        self._output_device = output_device
        self._on_error = on_error
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error = ""

    def start(self) -> None:
        self._ready.clear()
        self._startup_error = ""
        self._thread = threading.Thread(
            target=self._run,
            name="qwen-pcm-playback",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("打开英文输出设备超时")
        if self._startup_error:
            raise RuntimeError(self._startup_error)

    def enqueue(self, pcm: bytes) -> None:
        if pcm:
            self._queue.put(pcm)

    def play_test_tone(self) -> None:
        frame_count = int(OUTPUT_SAMPLE_RATE * 0.35)
        timeline = np.arange(frame_count, dtype=np.float32) / OUTPUT_SAMPLE_RATE
        signal = 0.12 * np.sin(2.0 * np.pi * 760.0 * timeline)
        fade_frames = min(480, frame_count // 2)
        fade = np.linspace(0.0, 1.0, fade_frames, dtype=np.float32)
        signal[:fade_frames] *= fade
        signal[-fade_frames:] *= fade[::-1]
        self.enqueue((signal * 32767.0).astype("<i2").tobytes())

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        try:
            with self._output_device.player(
                samplerate=OUTPUT_SAMPLE_RATE,
                channels=1,
                blocksize=PLAYBACK_BLOCK_FRAMES,
            ) as player:
                self._ready.set()
                while True:
                    pcm = self._queue.get()
                    if pcm is None:
                        return
                    samples = np.frombuffer(pcm, dtype="<i2")
                    if samples.size:
                        audio = (
                            samples.astype(np.float32) / 32768.0
                        ).reshape(-1, 1)
                        player.play(audio)
        except Exception as exc:
            self._startup_error = f"英文输出设备打开失败：{exc}"
            self._ready.set()
            self._on_error(self._startup_error)


class QwenLiveTranslateClient:
    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str,
        model: str = MODEL_NAME,
        websocket_url: str = "",
        source_language: str,
        target_language: str,
        audio_output: bool,
        input_device: Any,
        voice_name: str = "Tina",
        playback: PcmPlaybackWorker | None = None,
        on_translation: TranslationCallback,
        on_usage: UsageCallback,
        on_error: MessageCallback,
        on_connection_status: ConnectionStatusCallback | None = None,
        on_diagnostic: ConnectionStatusCallback | None = None,
        attempt_limiter: ConnectionAttemptLimiter | None = None,
        direction: str,
        name: str,
        use_silence_gate: bool,
        qwen38_fast_subtitles: bool = False,
    ) -> None:
        if audio_output and playback is None:
            raise ValueError("启用音频输出时必须提供播放设备")
        self._api_key = api_key.strip()
        if not self._api_key:
            raise ValueError("同传 API Key 不能为空")
        self._workspace_id = workspace_id.strip()
        self._api_url = build_api_url(workspace_id, model, websocket_url)
        self._uses_qwen38_protocol = uses_qwen38_protocol(model)
        self._qwen38_fast_subtitles = (
            qwen38_fast_subtitles
            and supports_qwen38_fast_subtitles(model, websocket_url)
        )
        self._source_language = source_language
        self._target_language = target_language
        self._audio_output = audio_output
        self._voice_name = voice_name
        self._playback = playback
        self._on_translation = on_translation
        self._on_usage = on_usage
        self._on_error = on_error
        self._on_connection_status = on_connection_status or (lambda _status: None)
        self._on_diagnostic = on_diagnostic or (lambda _status: None)
        self._attempt_limiter = attempt_limiter or ConnectionAttemptLimiter()
        self._direction = direction
        self._name = name
        self._stream_id = uuid4().hex
        self._capture = PcmCaptureWorker(
            input_device=input_device,
            on_error=on_error,
            # 3.8 detects speech/speakers from a continuous audio timeline.
            # Cutting off silence locally can leave its current turn pending.
            use_silence_gate=use_silence_gate and not self._uses_qwen38_protocol,
        )

        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._connection_ready = threading.Event()
        self._session_finished = threading.Event()
        self._stop_event = threading.Event()
        self._send_lock = threading.Lock()
        self._socket_open = False
        self._configured = False
        self._capture_started = False
        self._stopping = False
        self._ever_connected = False
        self._startup_error = ""
        self._event_sequence = 0
        self._generation = 0
        self._current_attempt = 0
        self._last_transport_error = ""
        self._close_status_code: int | None = None
        self._close_message = ""
        self._retryable_close = True
        self._aligner = TranslationAligner()
        self._alignment_lock = threading.Lock()
        self._alignment_timer: threading.Timer | None = None
        self._alignment_timer_deadline: float | None = None

    def start(self) -> None:
        self._connection_ready.clear()
        self._stop_event.clear()
        self._session_finished.clear()
        self._reset_alignment()
        self._event_sequence = 0
        self._stopping = False
        self._socket_open = False
        self._configured = False
        self._capture_started = False
        self._ever_connected = False
        self._startup_error = ""
        self._thread = threading.Thread(
            target=self._connection_loop,
            name=f"qwen-{self._name}-supervisor",
            daemon=True,
        )
        self._thread.start()
        if not self._connection_ready.wait(
            timeout=INITIAL_CONNECTION_TIMEOUT_SECONDS
        ):
            detail = self._startup_error or f"连接千问{self._name}会话超时"
            safe_detail = sanitize_diagnostic(
                detail,
                (self._api_key, self._workspace_id, self._api_url),
            )
            error = format_startup_failure(safe_detail, self._current_attempt)
            self._emit_status(
                "failed",
                attempt=self._current_attempt,
                detail=error,
            )
            self.stop()
            raise RuntimeError(error)
        if not self._ever_connected:
            detail = self._startup_error or f"千问{self._name}会话配置失败"
            safe_detail = sanitize_diagnostic(
                detail,
                (self._api_key, self._workspace_id, self._api_url),
            )
            error = format_startup_failure(safe_detail, self._current_attempt)
            self.stop()
            raise RuntimeError(error)

    def stop(self) -> None:
        self._stopping = True
        self._stop_event.set()
        drained = self._capture.stop()
        ws = self._ws
        if not drained and ws:
            # A blocked send can hold the WebSocket's write lock. Interrupt
            # the underlying socket before asking WebSocketApp to close it.
            try:
                transport = ws.sock
                if transport is not None:
                    transport.abort()
            except (OSError, websocket.WebSocketException):
                pass
        if drained and self._configured and self._socket_open:
            try:
                self._send_json(
                    {
                        "event_id": self._next_event_id(),
                        "type": "session.finish",
                    }
                )
                self._session_finished.wait(timeout=8.0)
            except Exception:
                pass
        ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        if (
            self._thread
            and self._thread.is_alive()
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(timeout=2.0)
        self._flush_alignment("stop")
        self._socket_open = False
        self._configured = False

    def _connection_loop(self) -> None:
        attempt = 0
        failure_detail = ""
        while not self._stop_event.is_set():
            if attempt:
                self._emit_status(
                    "reconnecting",
                    attempt=attempt,
                    detail=failure_detail,
                )
                if self._stop_event.wait(reconnect_delay(attempt)):
                    break
            if not self._attempt_limiter.wait_for_slot(self._stop_event):
                break

            self._prepare_connection(attempt)
            if attempt == 0:
                self._emit_status("connecting")
            try:
                self._run_socket()
            except Exception as exc:
                self._last_transport_error = (
                    f"{type(exc).__name__}: {exc}"
                )
            finally:
                self._capture.stop()
                self._flush_alignment(
                    "stop" if self._stopping else "disconnect"
                )

            if self._stop_event.is_set() or self._stopping:
                break
            failure_detail = self._failure_detail()
            if not self._ever_connected:
                self._startup_error = failure_detail or f"千问{self._name}连接失败"
                if is_retryable_startup_failure(
                    failure_detail,
                    self._close_status_code,
                    self._retryable_close,
                ):
                    attempt += 1
                    continue
                self._emit_status("failed", detail=self._startup_error)
                self._connection_ready.set()
                break
            if not self._retryable_close:
                self._emit_status("failed", detail=failure_detail)
                break
            attempt += 1

    def _prepare_connection(self, attempt: int) -> None:
        self._session_finished.clear()
        self._socket_open = False
        self._configured = False
        self._capture_started = False
        self._last_transport_error = ""
        self._close_status_code = None
        self._close_message = ""
        self._retryable_close = True
        self._current_attempt = attempt
        self._generation += 1
        self._reset_alignment()
        generation = self._generation
        self._ws = websocket.WebSocketApp(
            self._api_url,
            header=[f"Authorization: Bearer {self._api_key}"],
            on_open=lambda ws: self._on_open(generation, ws),
            on_message=lambda ws, message: self._on_message(
                generation, ws, message
            ),
            on_error=lambda ws, error: self._on_socket_error(
                generation, ws, error
            ),
            on_close=lambda ws, code, message: self._on_close(
                generation, ws, code, message
            ),
        )

    def build_session_update(self) -> dict[str, Any]:
        modalities = ["text", "audio"] if self._audio_output else ["text"]
        session: dict[str, Any]
        if self._uses_qwen38_protocol:
            # 3.8 embeds ASR and detects the source language automatically.
            # Its session schema is different from the 3.5 flat fields.
            session = {
                "input_modalities": ["audio"],
                "output_modalities": modalities,
                "audio": {
                    "input": {
                        "format": {
                            "type": "pcm",
                            "sample_rate": INPUT_SAMPLE_RATE,
                        },
                        "turn_detection": (
                            {"type": "server_vad"}
                            if self._qwen38_fast_subtitles
                            else {"type": "speaker_detection", "threshold": 0.5}
                        ),
                    },
                },
                "translation": {"language": self._target_language},
            }
            if self._audio_output:
                session["audio"]["output"] = {"voice": self._voice_name}
        else:
            session = {
                "modalities": modalities,
                "sample_rate": INPUT_SAMPLE_RATE,
                "input_audio_format": "pcm",
                "output_audio_format": "pcm",
                "input_audio_transcription": {
                    "model": "qwen3-asr-flash-realtime",
                    "language": self._source_language,
                },
                "translation": {"language": self._target_language},
            }
            if self._audio_output:
                session["voice"] = self._voice_name
        return {
            "event_id": self._next_event_id(),
            "type": "session.update",
            "session": session,
        }

    def _run_socket(self) -> None:
        assert self._ws is not None
        self._ws.run_forever(
            ping_interval=PING_INTERVAL_SECONDS,
            ping_timeout=PING_TIMEOUT_SECONDS,
            sockopt=((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),),
        )

    def _on_open(
        self,
        generation: int,
        _: websocket.WebSocketApp,
    ) -> None:
        if generation != self._generation or self._stopping:
            return
        self._socket_open = True
        try:
            self._send_json(self.build_session_update())
        except Exception as exc:
            self._startup_error = f"发送千问会话配置失败：{exc}"
            self._signal_transport_failure(self._startup_error)

    def _on_message(
        self,
        generation: int,
        _: websocket.WebSocketApp,
        message: str,
    ) -> None:
        if generation != self._generation:
            return
        try:
            self._handle_message(message)
        except Exception as exc:
            if not self._stopping:
                self._on_error(f"解析千问{self._name}响应失败：{exc}")

    def _handle_message(self, message: str) -> None:
        event = json.loads(message)
        event_type = str(event.get("type", ""))

        if event_type == "error":
            error = event.get("error") or {}
            code = error.get("code", event.get("code", "unknown"))
            detail = error.get("message", event.get("message", "未知错误"))
            self._retryable_close = self._is_retryable_server_error(code)
            self._last_transport_error = f"服务错误 {code}：{detail}"
            if not self._ever_connected:
                self._startup_error = self._last_transport_error
            self._capture.request_stop()
            ws = self._ws
            if ws:
                ws.close()
            return

        if event_type == "session.updated":
            if self._stopping:
                return
            self._configured = True
            self._ever_connected = True
            self._connection_ready.set()
            if not self._capture_started:
                self._capture_started = True
                ws = self._ws
                self._capture.start(lambda pcm: self._send_audio(ws, pcm))
            self._emit_status("connected", attempt=self._current_attempt)
            return

        if event_type == "session.finished":
            self._session_finished.set()
            return

        if event_type == "input_audio_buffer.speech_started":
            self._process_alignment(self._aligner.speech_started)
            return

        if event_type == "input_audio_buffer.speech_stopped":
            self._process_alignment(self._aligner.speech_stopped)
            return

        if event_type == "conversation.item.input_audio_transcription.delta":
            self._process_alignment(
                self._aligner.source_delta,
                str(event.get("item_id", "")),
                str(event.get("delta", "")),
            )
            return

        if event_type == "conversation.item.input_audio_transcription.text":
            preview = f"{event.get('text', '')}{event.get('stash', '')}".strip()
            if preview:
                self._process_alignment(
                    self._aligner.source_preview,
                    str(event.get("item_id", "")),
                    preview,
                )
            return

        if event_type == "conversation.item.input_audio_transcription.completed":
            transcript = str(event.get("transcript", "")).strip()
            self._process_alignment(
                self._aligner.source_completed,
                str(event.get("item_id", "")),
                transcript,
            )
            return

        if event_type == "response.created":
            response = event.get("response") or {}
            self._process_alignment(
                self._aligner.response_started,
                str(response.get("id", "")),
            )
            return

        if event_type == "response.output_item.added":
            item = event.get("item") or {}
            self._process_alignment(
                self._aligner.response_started,
                str(event.get("response_id", "")),
                str(item.get("id", "")),
            )
            return

        if event_type in {
            "response.text.delta",
            "response.audio_transcript.delta",
        }:
            self._process_alignment(
                self._aligner.translation_delta,
                str(event.get("response_id", "")),
                str(event.get("item_id", "")),
                str(event.get("delta", "")),
            )
            return

        if event_type in {
            "response.text.text",
            "response.audio_transcript.text",
        }:
            translated = f"{event.get('text', '')}{event.get('stash', '')}".strip()
            if translated:
                self._process_alignment(
                    self._aligner.translation_preview,
                    str(event.get("response_id", "")),
                    str(event.get("item_id", "")),
                    translated,
                )
            return

        if event_type in {
            "response.text.done",
            "response.audio_transcript.done",
        }:
            key = "transcript" if event_type.endswith("transcript.done") else "text"
            translated = str(event.get(key, "")).strip()
            self._process_alignment(
                self._aligner.translation_completed,
                str(event.get("response_id", event.get("event_id", ""))),
                str(event.get("item_id", "")),
                translated,
            )
            return

        if event_type == "response.audio.delta" and self._playback:
            encoded = str(event.get("delta", ""))
            if encoded:
                self._playback.enqueue(base64.b64decode(encoded))
            return

        if event_type == "response.done":
            response = event.get("response") or {}
            response_id = str(response.get("id", ""))
            if response_id:
                output_item_id, translated = _translation_from_response(response)
                self._process_alignment(
                    self._aligner.translation_completed,
                    response_id,
                    output_item_id,
                    translated,
                )
            stats = UsageStats.from_response(response)
            if stats.total_tokens:
                self._on_usage(stats)

    def _process_alignment(
        self,
        operation: Callable[..., tuple[AlignmentOutput, ...]],
        *args: object,
    ) -> None:
        with self._alignment_lock:
            outputs = operation(*args)
            self._schedule_alignment_timer_locked()
        self._deliver_alignment(outputs)

    def _deliver_alignment(
        self,
        outputs: tuple[AlignmentOutput, ...],
    ) -> None:
        for output in outputs:
            self._on_translation(replace(
                output.event,
                segment_id=(
                    f"{self._stream_id}:{self._generation}:{output.segment_index}"
                ),
            ))
            if output.event.is_final:
                timing = (
                    f" first_target_ms={output.first_target_ms}"
                    if output.first_target_ms is not None else ""
                )
                status = ConnectionStatus(
                    direction=self._direction,
                    state="alignment",
                    attempt=self._current_attempt,
                    detail=(
                        f"segment={output.segment_index} "
                        "event=alignment.finalized "
                        f"result={output.event.alignment_status} "
                        f"reason={output.reason} generation={self._generation} "
                        f"first_target={output.first_target}{timing}"
                    ),
                )
                self._on_diagnostic(status)

    def _schedule_alignment_timer_locked(self) -> None:
        deadline = self._aligner.expiry_deadline()
        scheduled = self._alignment_timer_deadline
        if scheduled == deadline or (
            scheduled is not None and deadline is not None and scheduled < deadline
        ):
            # A pending timer rechecks the latest idle deadline when it fires.
            return
        if self._alignment_timer is not None:
            self._alignment_timer.cancel()
            self._alignment_timer = None
        self._alignment_timer_deadline = deadline
        delay = self._aligner.seconds_until_expiry()
        if delay is None:
            return
        timer = threading.Timer(
            max(0.01, delay),
            self._on_alignment_timeout,
            args=(self._generation, deadline),
        )
        timer.daemon = True
        self._alignment_timer = timer
        timer.start()

    def _on_alignment_timeout(self, generation: int, deadline: float) -> None:
        with self._alignment_lock:
            if (
                generation != self._generation
                or deadline != self._alignment_timer_deadline
            ):
                return
            self._alignment_timer = None
            self._alignment_timer_deadline = None
            outputs = self._aligner.expire()
            self._schedule_alignment_timer_locked()
        self._deliver_alignment(outputs)

    def _reset_alignment(self) -> None:
        with self._alignment_lock:
            if self._alignment_timer is not None:
                self._alignment_timer.cancel()
                self._alignment_timer = None
            self._alignment_timer_deadline = None
            self._aligner.reset()

    def _flush_alignment(self, reason: str) -> None:
        with self._alignment_lock:
            if self._alignment_timer is not None:
                self._alignment_timer.cancel()
                self._alignment_timer = None
            self._alignment_timer_deadline = None
            outputs = self._aligner.flush(reason)
        self._deliver_alignment(outputs)

    def _send_audio(
        self, ws: websocket.WebSocketApp | None, pcm: bytes
    ) -> None:
        if ws is None or ws is not self._ws or not self._configured:
            return
        try:
            event = {
                "event_id": self._next_event_id(),
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
            ws.send(json.dumps(event))
        except Exception as exc:
            if ws is self._ws:
                self._signal_transport_failure(
                    f"发送音频失败：{type(exc).__name__}: {exc}"
                )

    def _send_json(self, event: dict[str, Any]) -> None:
        with self._send_lock:
            if not self._ws or not self._socket_open:
                return
            self._ws.send(json.dumps(event, ensure_ascii=False))

    def _next_event_id(self) -> str:
        self._event_sequence += 1
        return f"event_{self._name}_{int(time.time() * 1000)}_{self._event_sequence}"

    def _on_socket_error(
        self,
        generation: int,
        _: websocket.WebSocketApp,
        error: Any,
    ) -> None:
        if generation != self._generation or self._stopping:
            return
        self._last_transport_error = f"{type(error).__name__}: {error}"
        if not self._ever_connected:
            self._startup_error = self._last_transport_error
        self._capture.request_stop()

    def _on_close(
        self,
        generation: int,
        _: websocket.WebSocketApp,
        close_status_code: int | None,
        close_message: str | None,
    ) -> None:
        if generation != self._generation:
            return
        self._socket_open = False
        self._configured = False
        self._close_status_code = close_status_code
        self._close_message = close_message or ""
        self._session_finished.set()
        self._capture.request_stop()

    def _signal_transport_failure(self, detail: str) -> None:
        if self._stopping:
            return
        self._last_transport_error = detail
        self._retryable_close = True
        self._configured = False
        self._capture.request_stop()
        ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    def _failure_detail(self) -> str:
        parts: list[str] = []
        if self._last_transport_error:
            parts.append(self._last_transport_error)
        if self._close_status_code is not None:
            parts.append(f"关闭码 {self._close_status_code}")
        if self._close_message:
            parts.append(self._close_message)
        if not parts:
            parts.append("连接无关闭码断开")
        return "；".join(dict.fromkeys(parts))

    @staticmethod
    def _is_retryable_server_error(code: object) -> bool:
        normalized = str(code or "").strip().lower()
        if normalized in {"401", "403"}:
            return False
        fatal_markers = (
            "auth",
            "permission",
            "forbidden",
            "invalid",
            "unsupported",
            "not_found",
        )
        return not any(marker in normalized for marker in fatal_markers)

    def _emit_status(
        self,
        state: str,
        *,
        attempt: int = 0,
        detail: str = "",
    ) -> None:
        safe_detail = sanitize_diagnostic(
            detail,
            (self._api_key, self._workspace_id, self._api_url),
        )
        status = ConnectionStatus(
            direction=self._direction,
            state=state,
            attempt=attempt,
            detail=safe_detail,
        )
        self._on_diagnostic(status)
        self._on_connection_status(status)


class QwenInterpreterSession:
    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str,
        model: str = MODEL_NAME,
        websocket_url: str = "",
        microphone: Any,
        teams_loopback: Any,
        virtual_output: Any | None,
        english_voice: str,
        on_incoming: TranslationCallback,
        on_outgoing: TranslationCallback,
        on_usage: UsageCallback,
        on_error: MessageCallback,
        on_connection_status: ConnectionStatusCallback | None = None,
        on_diagnostic: ConnectionStatusCallback | None = None,
        use_silence_gate: bool,
        english_audio_output: bool = True,
        qwen38_fast_subtitles: bool = False,
    ) -> None:
        self._on_usage = on_usage
        self._usage = UsageStats()
        self._usage_lock = threading.Lock()
        attempt_limiter = ConnectionAttemptLimiter()
        if english_audio_output and virtual_output is None:
            raise ValueError("启用英文语音输出时必须选择输出设备")
        self._playback = (
            PcmPlaybackWorker(virtual_output, on_error)
            if english_audio_output
            else None
        )
        self._outgoing = QwenLiveTranslateClient(
            api_key=api_key,
            workspace_id=workspace_id,
            model=model,
            websocket_url=websocket_url,
            qwen38_fast_subtitles=qwen38_fast_subtitles,
            source_language="zh",
            target_language="en",
            audio_output=english_audio_output,
            input_device=microphone,
            voice_name=english_voice,
            playback=self._playback,
            on_translation=on_outgoing,
            on_usage=self._add_usage,
            on_error=on_error,
            on_connection_status=on_connection_status,
            on_diagnostic=on_diagnostic,
            attempt_limiter=attempt_limiter,
            direction="outgoing",
            name="中译英",
            use_silence_gate=use_silence_gate,
        )
        self._incoming = QwenLiveTranslateClient(
            api_key=api_key,
            workspace_id=workspace_id,
            model=model,
            websocket_url=websocket_url,
            qwen38_fast_subtitles=qwen38_fast_subtitles,
            source_language="en",
            target_language="zh",
            audio_output=False,
            input_device=teams_loopback,
            on_translation=on_incoming,
            on_usage=self._add_usage,
            on_error=on_error,
            on_connection_status=on_connection_status,
            on_diagnostic=on_diagnostic,
            attempt_limiter=attempt_limiter,
            direction="incoming",
            name="英译中",
            use_silence_gate=use_silence_gate,
        )
        self._started = False

    def start(self) -> None:
        try:
            if self._playback is not None:
                self._playback.start()
            self._outgoing.start()
            self._incoming.start()
            self._started = True
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        for component in (self._incoming, self._outgoing, self._playback):
            if component is None:
                continue
            try:
                component.stop()
            except Exception:
                pass
        self._started = False

    def test_output(self) -> None:
        if self._started and self._playback is not None:
            self._playback.play_test_tone()

    def _add_usage(self, stats: UsageStats) -> None:
        with self._usage_lock:
            self._usage = self._usage + stats
            total = self._usage
        self._on_usage(total)
