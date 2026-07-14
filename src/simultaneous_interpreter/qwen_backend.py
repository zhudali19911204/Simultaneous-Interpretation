from __future__ import annotations

import base64
import json
import queue
import re
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import numpy as np
import websocket


INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
BLOCK_FRAMES = 320  # 20 ms of 16-bit mono PCM for lower capture latency
PLAYBACK_BLOCK_FRAMES = 480  # 20 ms at 24 kHz
MODEL_NAME = "qwen3.5-livetranslate-flash-realtime"
GATE_THRESHOLD_RMS = 0.004
GATE_PRE_ROLL_BLOCKS = 8  # 160 ms
# Keep sending silence slightly beyond the typical server VAD boundary. Stopping
# exactly at the boundary can delay finalization until the next speech segment.
GATE_HANGOVER_BLOCKS = 50  # 1000 ms


@dataclass(frozen=True)
class TranslationEvent:
    source_text: str
    translated_text: str
    is_final: bool


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


TranslationCallback = Callable[[TranslationEvent], None]
UsageCallback = Callable[[UsageStats], None]
MessageCallback = Callable[[str], None]


def normalize_realtime_model_name(model: str) -> str:
    normalized = model.strip()
    if not normalized:
        raise ValueError("同传模型不能为空")
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+", normalized):
        raise ValueError("同传模型名称包含不支持的字符")
    return normalized


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


class PcmCaptureWorker:
    def __init__(
        self,
        *,
        input_device: Any,
        on_audio: Callable[[bytes], None],
        on_error: MessageCallback,
        use_silence_gate: bool,
    ) -> None:
        self._input_device = input_device
        self._on_audio = on_audio
        self._on_error = on_error
        self._use_silence_gate = use_silence_gate
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="qwen-pcm-capture",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self) -> None:
        self._stop_event.set()

    def stop(self) -> None:
        self.request_stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            gate = EnergySpeechGate()
            with self._input_device.recorder(
                samplerate=INPUT_SAMPLE_RATE,
                channels=1,
                blocksize=BLOCK_FRAMES,
            ) as recorder:
                while not self._stop_event.is_set():
                    block = recorder.record(numframes=BLOCK_FRAMES)
                    chunks = (
                        gate.process(block)
                        if self._use_silence_gate
                        else [_pcm_from_float(block)]
                    )
                    for pcm in chunks:
                        self._on_audio(pcm)
        except Exception as exc:
            if not self._stop_event.is_set():
                self._on_error(f"音频捕获失败：{exc}")


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
        name: str,
        use_silence_gate: bool,
    ) -> None:
        if audio_output and playback is None:
            raise ValueError("启用音频输出时必须提供播放设备")
        self._api_key = api_key.strip()
        if not self._api_key:
            raise ValueError("同传 API Key 不能为空")
        self._api_url = build_api_url(workspace_id, model, websocket_url)
        self._source_language = source_language
        self._target_language = target_language
        self._audio_output = audio_output
        self._voice_name = voice_name
        self._playback = playback
        self._on_translation = on_translation
        self._on_usage = on_usage
        self._on_error = on_error
        self._name = name
        self._capture = PcmCaptureWorker(
            input_device=input_device,
            on_audio=self._send_audio,
            on_error=on_error,
            use_silence_gate=use_silence_gate,
        )

        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._connection_ready = threading.Event()
        self._session_finished = threading.Event()
        self._send_lock = threading.Lock()
        self._socket_open = False
        self._configured = False
        self._capture_started = False
        self._stopping = False
        self._startup_error = ""
        self._event_sequence = 0
        self._latest_source = ""
        self._pending_sources: deque[str] = deque()
        self._finished_translations: set[str] = set()

    def start(self) -> None:
        self._connection_ready.clear()
        self._session_finished.clear()
        self._pending_sources.clear()
        self._finished_translations.clear()
        self._latest_source = ""
        self._event_sequence = 0
        self._stopping = False
        self._socket_open = False
        self._configured = False
        self._capture_started = False
        self._startup_error = ""
        self._ws = websocket.WebSocketApp(
            self._api_url,
            header=[f"Authorization: Bearer {self._api_key}"],
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_socket_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(
            target=self._run_socket,
            name=f"qwen-{self._name}-websocket",
            daemon=True,
        )
        self._thread.start()
        if not self._connection_ready.wait(timeout=15.0):
            self.stop()
            raise RuntimeError(f"连接千问{self._name}会话超时")
        if not self._configured:
            error = self._startup_error or f"千问{self._name}会话配置失败"
            self.stop()
            raise RuntimeError(error)

    def stop(self) -> None:
        self._stopping = True
        self._capture.stop()
        if self._configured and self._socket_open:
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
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._socket_open = False
        self._configured = False

    def build_session_update(self) -> dict[str, Any]:
        session: dict[str, Any] = {
            "modalities": ["text", "audio"]
            if self._audio_output
            else ["text"],
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
            ping_interval=20,
            ping_timeout=10,
            sockopt=((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),),
        )

    def _on_open(self, _: websocket.WebSocketApp) -> None:
        self._socket_open = True
        try:
            self._send_json(self.build_session_update())
        except Exception as exc:
            self._startup_error = f"发送千问会话配置失败：{exc}"
            self._connection_ready.set()

    def _on_message(self, _: websocket.WebSocketApp, message: str) -> None:
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
            self._startup_error = f"千问错误 {code}：{detail}"
            self._connection_ready.set()
            self._capture.request_stop()
            self._on_error(self._startup_error)
            return

        if event_type == "session.updated":
            self._configured = True
            self._connection_ready.set()
            if not self._capture_started:
                self._capture_started = True
                self._capture.start()
            return

        if event_type == "session.finished":
            self._session_finished.set()
            return

        if event_type == "conversation.item.input_audio_transcription.text":
            preview = f"{event.get('text', '')}{event.get('stash', '')}".strip()
            if preview:
                self._latest_source = preview
                self._on_translation(TranslationEvent(preview, "", False))
            return

        if event_type == "conversation.item.input_audio_transcription.completed":
            transcript = str(event.get("transcript", "")).strip()
            if transcript:
                self._latest_source = transcript
                self._pending_sources.append(transcript)
            return

        if event_type in {
            "response.text.text",
            "response.audio_transcript.text",
        }:
            translated = f"{event.get('text', '')}{event.get('stash', '')}".strip()
            if translated:
                source = (
                    self._pending_sources[0]
                    if self._pending_sources
                    else self._latest_source
                )
                self._on_translation(
                    TranslationEvent(source, translated, False)
                )
            return

        if event_type in {
            "response.text.done",
            "response.audio_transcript.done",
        }:
            fingerprint = str(
                event.get("response_id", event.get("event_id", ""))
            )
            if fingerprint and fingerprint in self._finished_translations:
                return
            if fingerprint:
                self._finished_translations.add(fingerprint)
            key = "transcript" if event_type.endswith("transcript.done") else "text"
            translated = str(event.get(key, "")).strip()
            source = (
                self._pending_sources.popleft()
                if self._pending_sources
                else self._latest_source
            )
            if source or translated:
                self._on_translation(
                    TranslationEvent(source, translated, True)
                )
            return

        if event_type == "response.audio.delta" and self._playback:
            encoded = str(event.get("delta", ""))
            if encoded:
                self._playback.enqueue(base64.b64decode(encoded))
            return

        if event_type == "response.done":
            stats = UsageStats.from_response(event.get("response") or {})
            if stats.total_tokens:
                self._on_usage(stats)

    def _send_audio(self, pcm: bytes) -> None:
        if not self._configured:
            return
        self._send_json(
            {
                "event_id": self._next_event_id(),
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )

    def _send_json(self, event: dict[str, Any]) -> None:
        with self._send_lock:
            if not self._ws or not self._socket_open:
                return
            self._ws.send(json.dumps(event, ensure_ascii=False))

    def _next_event_id(self) -> str:
        self._event_sequence += 1
        return f"event_{self._name}_{int(time.time() * 1000)}_{self._event_sequence}"

    def _on_socket_error(self, _: websocket.WebSocketApp, error: Any) -> None:
        self._startup_error = f"千问{self._name}连接错误：{error}"
        self._connection_ready.set()
        self._capture.request_stop()
        if not self._stopping:
            self._on_error(self._startup_error)

    def _on_close(
        self,
        _: websocket.WebSocketApp,
        close_status_code: int | None,
        close_message: str | None,
    ) -> None:
        was_open = self._socket_open
        self._socket_open = False
        self._session_finished.set()
        self._connection_ready.set()
        self._capture.request_stop()
        if was_open and not self._stopping:
            detail = close_message or str(close_status_code or "")
            self._on_error(
                f"千问{self._name}连接已断开：{detail}".rstrip("：")
            )


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
        virtual_output: Any,
        english_voice: str,
        on_incoming: TranslationCallback,
        on_outgoing: TranslationCallback,
        on_usage: UsageCallback,
        on_error: MessageCallback,
        use_silence_gate: bool,
    ) -> None:
        self._on_usage = on_usage
        self._usage = UsageStats()
        self._usage_lock = threading.Lock()
        self._playback = PcmPlaybackWorker(virtual_output, on_error)
        self._outgoing = QwenLiveTranslateClient(
            api_key=api_key,
            workspace_id=workspace_id,
            model=model,
            websocket_url=websocket_url,
            source_language="zh",
            target_language="en",
            audio_output=True,
            input_device=microphone,
            voice_name=english_voice,
            playback=self._playback,
            on_translation=on_outgoing,
            on_usage=self._add_usage,
            on_error=on_error,
            name="中译英",
            use_silence_gate=use_silence_gate,
        )
        self._incoming = QwenLiveTranslateClient(
            api_key=api_key,
            workspace_id=workspace_id,
            model=model,
            websocket_url=websocket_url,
            source_language="en",
            target_language="zh",
            audio_output=False,
            input_device=teams_loopback,
            on_translation=on_incoming,
            on_usage=self._add_usage,
            on_error=on_error,
            name="英译中",
            use_silence_gate=use_silence_gate,
        )
        self._started = False

    def start(self) -> None:
        try:
            self._playback.start()
            self._outgoing.start()
            self._incoming.start()
            self._started = True
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        for component in (self._incoming, self._outgoing, self._playback):
            try:
                component.stop()
            except Exception:
                pass
        self._started = False

    def test_output(self) -> None:
        if self._started:
            self._playback.play_test_tone()

    def _add_usage(self, stats: UsageStats) -> None:
        with self._usage_lock:
            self._usage = self._usage + stats
            total = self._usage
        self._on_usage(total)
