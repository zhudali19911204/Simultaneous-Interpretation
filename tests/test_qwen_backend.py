from __future__ import annotations

import base64
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.qwen_backend import (  # noqa: E402
    BLOCK_FRAMES,
    PING_INTERVAL_SECONDS,
    PING_TIMEOUT_SECONDS,
    ConnectionAttemptLimiter,
    EnergySpeechGate,
    GATE_HANGOVER_BLOCKS,
    INPUT_SAMPLE_RATE,
    MODEL_NAME,
    OUTPUT_SAMPLE_RATE,
    PLAYBACK_BLOCK_FRAMES,
    QwenLiveTranslateClient,
    UsageStats,
    build_api_url,
    reconnect_delay,
)


class FakePlayback:
    def __init__(self) -> None:
        self.audio: list[bytes] = []

    def enqueue(self, pcm: bytes) -> None:
        self.audio.append(pcm)


def make_client(
    *,
    audio_output: bool = False,
    statuses=None,
    diagnostics=None,
    attempt_limiter=None,
):
    events = []
    usages = []
    errors = []
    playback = FakePlayback() if audio_output else None
    client = QwenLiveTranslateClient(
        api_key="test-key",
        workspace_id="ws-test_123",
        source_language="zh" if audio_output else "en",
        target_language="en" if audio_output else "zh",
        audio_output=audio_output,
        input_device=object(),
        voice_name="Tina",
        playback=playback,  # type: ignore[arg-type]
        on_translation=events.append,
        on_usage=usages.append,
        on_error=errors.append,
        on_connection_status=(statuses if statuses is not None else []).append,
        on_diagnostic=(diagnostics if diagnostics is not None else []).append,
        attempt_limiter=attempt_limiter,
        direction="outgoing" if audio_output else "incoming",
        name="test",
        use_silence_gate=True,
    )
    return client, events, usages, errors, playback


class FakeCapture:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.starts += 1

    def request_stop(self) -> None:
        self.active = 0

    def stop(self) -> None:
        self.active = 0
        self.stops += 1


class ConfigurationTests(unittest.TestCase):
    def test_low_latency_audio_buffers_and_tcp_nodelay(self) -> None:
        self.assertLessEqual(BLOCK_FRAMES / INPUT_SAMPLE_RATE, 0.020)
        self.assertLessEqual(PLAYBACK_BLOCK_FRAMES / OUTPUT_SAMPLE_RATE, 0.020)
        self.assertGreaterEqual(
            GATE_HANGOVER_BLOCKS * BLOCK_FRAMES / INPUT_SAMPLE_RATE,
            1.0,
        )

        class FakeWebSocket:
            options = None

            def run_forever(self, **options):
                self.options = options

        client, *_ = make_client()
        fake_socket = FakeWebSocket()
        client._ws = fake_socket  # type: ignore[assignment]
        client._run_socket()
        self.assertIn(
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
            fake_socket.options["sockopt"],  # type: ignore[index]
        )
        self.assertEqual(fake_socket.options["ping_interval"], PING_INTERVAL_SECONDS)
        self.assertEqual(fake_socket.options["ping_timeout"], PING_TIMEOUT_SECONDS)

    def test_energy_gate_drops_silence_and_keeps_speech_edges(self) -> None:
        import numpy as np

        gate = EnergySpeechGate()
        silence = np.zeros((BLOCK_FRAMES, 1), dtype=np.float32)
        speech = np.full((BLOCK_FRAMES, 1), 0.02, dtype=np.float32)
        for _ in range(10):
            self.assertEqual(gate.process(silence), [])
        chunks = gate.process(speech)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertTrue(all(len(chunk) == BLOCK_FRAMES * 2 for chunk in chunks))

    def test_api_url_uses_china_workspace_domain(self) -> None:
        url = build_api_url("ws-test_123")
        self.assertEqual(
            url,
            "wss://ws-test_123.cn-beijing.maas.aliyuncs.com"
            f"/api-ws/v1/realtime?model={MODEL_NAME}",
        )

    def test_api_url_rejects_unsafe_workspace(self) -> None:
        with self.assertRaises(ValueError):
            build_api_url("bad.workspace/path")

    def test_custom_api_url_uses_configured_model(self) -> None:
        self.assertEqual(
            build_api_url(
                "",
                "vendor/live-model",
                "wss://gateway.example.com/realtime?tenant=demo&model=old",
            ),
            "wss://gateway.example.com/realtime?tenant=demo&model=vendor%2Flive-model",
        )
        with self.assertRaises(ValueError):
            build_api_url("", MODEL_NAME, "https://gateway.example.com/realtime")

    def test_audio_session_configuration(self) -> None:
        client, *_ = make_client(audio_output=True)
        session = client.build_session_update()["session"]
        self.assertEqual(session["modalities"], ["text", "audio"])
        self.assertEqual(session["input_audio_transcription"]["language"], "zh")
        self.assertEqual(session["translation"]["language"], "en")
        self.assertEqual(session["voice"], "Tina")

    def test_text_session_configuration(self) -> None:
        client, *_ = make_client(audio_output=False)
        session = client.build_session_update()["session"]
        self.assertEqual(session["modalities"], ["text"])
        self.assertEqual(session["input_audio_transcription"]["language"], "en")
        self.assertEqual(session["translation"]["language"], "zh")
        self.assertNotIn("voice", session)

    def test_reconnect_delay_is_capped(self) -> None:
        self.assertEqual(
            [reconnect_delay(index) for index in range(1, 8)],
            [2.0, 5.0, 10.0, 20.0, 30.0, 30.0, 30.0],
        )

    def test_connection_attempt_limiter_uses_rolling_window(self) -> None:
        now = [100.0]
        limiter = ConnectionAttemptLimiter(
            max_attempts=2,
            window_seconds=60.0,
            clock=lambda: now[0],
        )
        self.assertEqual(limiter.reserve_delay(), 0.0)
        self.assertEqual(limiter.reserve_delay(), 0.0)
        self.assertEqual(limiter.reserve_delay(), 60.0)
        now[0] = 160.1
        self.assertEqual(limiter.reserve_delay(), 0.0)

    def test_server_error_retry_classification(self) -> None:
        fatal = ("401", "403", "invalid_value", "authentication_failed")
        retryable = ("429", "rate_limit", "internal_error", "500")
        self.assertTrue(
            all(
                not QwenLiveTranslateClient._is_retryable_server_error(code)
                for code in fatal
            )
        )
        self.assertTrue(
            all(
                QwenLiveTranslateClient._is_retryable_server_error(code)
                for code in retryable
            )
        )


class ReconnectionTests(unittest.TestCase):
    def test_disconnect_reconnects_once_and_restarts_capture(self) -> None:
        statuses = []
        diagnostics = []
        reconnected = threading.Event()
        release = threading.Event()

        class SequencedWebSocket:
            instances = []

            def __init__(self, _url, **callbacks):
                self.callbacks = callbacks
                self.index = len(self.instances)
                self.closed = False
                self.instances.append(self)

            def send(self, payload):
                event = json.loads(payload)
                if event.get("type") == "session.finish":
                    self.callbacks["on_message"](
                        self, json.dumps({"type": "session.finished"})
                    )

            def close(self):
                if self.closed:
                    return
                self.closed = True
                self.callbacks["on_close"](self, 1000, "")
                release.set()

            def run_forever(self, **_options):
                self.callbacks["on_open"](self)
                self.callbacks["on_message"](
                    self, json.dumps({"type": "session.updated"})
                )
                if self.index == 0:
                    self.callbacks["on_error"](
                        self, TimeoutError("ping timeout")
                    )
                    self.callbacks["on_close"](self, None, None)
                    return
                reconnected.set()
                release.wait(2.0)

        client, _, _, errors, _ = make_client(
            statuses=statuses,
            diagnostics=diagnostics,
        )
        capture = FakeCapture()
        client._capture = capture  # type: ignore[assignment]
        with patch(
            "simultaneous_interpreter.qwen_backend.websocket.WebSocketApp",
            SequencedWebSocket,
        ), patch(
            "simultaneous_interpreter.qwen_backend.reconnect_delay",
            return_value=0.01,
        ):
            client.start()
            self.assertTrue(reconnected.wait(2.0))
            client.stop()

        states = [status.state for status in statuses]
        self.assertEqual(states.count("connected"), 2)
        self.assertEqual(states.count("reconnecting"), 1)
        self.assertNotIn("failed", states)
        reconnecting = next(
            status for status in statuses if status.state == "reconnecting"
        )
        self.assertIn("TimeoutError", reconnecting.detail)
        self.assertEqual(statuses, diagnostics)
        self.assertEqual(errors, [])
        self.assertEqual(capture.starts, 2)
        self.assertLessEqual(capture.max_active, 1)

    def test_initial_invalid_configuration_does_not_retry(self) -> None:
        statuses = []

        class InvalidWebSocket:
            instances = 0

            def __init__(self, _url, **callbacks):
                type(self).instances += 1
                self.callbacks = callbacks
                self.closed = False

            def send(self, _payload):
                return None

            def close(self):
                if not self.closed:
                    self.closed = True
                    self.callbacks["on_close"](self, 1008, "invalid")

            def run_forever(self, **_options):
                self.callbacks["on_open"](self)
                self.callbacks["on_message"](
                    self,
                    json.dumps(
                        {
                            "type": "error",
                            "error": {
                                "code": "invalid_value",
                                "message": "bad model",
                            },
                        }
                    ),
                )

        client, *_ = make_client(statuses=statuses)
        client._capture = FakeCapture()  # type: ignore[assignment]
        with patch(
            "simultaneous_interpreter.qwen_backend.websocket.WebSocketApp",
            InvalidWebSocket,
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid_value"):
                client.start()
        self.assertEqual(InvalidWebSocket.instances, 1)
        self.assertEqual(statuses[-1].state, "failed")

    def test_stop_interrupts_reconnect_wait(self) -> None:
        reconnecting = threading.Event()

        class StatusCollector(list):
            def append(self, status):
                super().append(status)
                if status.state == "reconnecting":
                    reconnecting.set()

        statuses = StatusCollector()

        class DisconnectingWebSocket:
            instances = 0

            def __init__(self, _url, **callbacks):
                type(self).instances += 1
                self.callbacks = callbacks

            def send(self, _payload):
                return None

            def close(self):
                return None

            def run_forever(self, **_options):
                self.callbacks["on_open"](self)
                self.callbacks["on_message"](
                    self, json.dumps({"type": "session.updated"})
                )
                self.callbacks["on_close"](self, None, None)

        client, *_ = make_client(statuses=statuses)
        client._capture = FakeCapture()  # type: ignore[assignment]
        with patch(
            "simultaneous_interpreter.qwen_backend.websocket.WebSocketApp",
            DisconnectingWebSocket,
        ), patch(
            "simultaneous_interpreter.qwen_backend.reconnect_delay",
            return_value=60.0,
        ):
            client.start()
            self.assertTrue(reconnecting.wait(1.0))
            client.stop()
        self.assertFalse(client._thread and client._thread.is_alive())
        self.assertEqual(DisconnectingWebSocket.instances, 1)

    def test_stale_callbacks_and_send_errors_are_transport_only(self) -> None:
        client, _, _, errors, _ = make_client()
        client._generation = 2
        client._on_socket_error(1, object(), TimeoutError("old"))  # type: ignore[arg-type]
        self.assertEqual(client._last_transport_error, "")

        class BrokenSocket:
            closed = False

            def send(self, _payload):
                raise OSError("network down")

            def close(self):
                self.closed = True

        socket = BrokenSocket()
        capture = FakeCapture()
        client._ws = socket  # type: ignore[assignment]
        client._capture = capture  # type: ignore[assignment]
        client._socket_open = True
        client._configured = True
        client._send_audio(b"\x00\x00")
        self.assertIn("发送音频失败", client._last_transport_error)
        self.assertFalse(client._configured)
        self.assertTrue(socket.closed)
        self.assertEqual(errors, [])


class ResponseParsingTests(unittest.TestCase):
    def test_text_translation_pairs_source_and_target(self) -> None:
        client, events, _, errors, _ = make_client(audio_output=False)
        client._handle_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Please review the proposal.",
                }
            )
        )
        client._handle_message(
            json.dumps(
                {
                    "type": "response.text.text",
                    "response_id": "resp-1",
                    "text": "请审阅",
                    "stash": "这份提案。",
                },
                ensure_ascii=False,
            )
        )
        client._handle_message(
            json.dumps(
                {
                    "type": "response.text.done",
                    "response_id": "resp-1",
                    "text": "请审阅这份提案。",
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(errors, [])
        self.assertEqual(events[-1].source_text, "Please review the proposal.")
        self.assertEqual(events[-1].translated_text, "请审阅这份提案。")
        self.assertTrue(events[-1].is_final)

    def test_audio_translation_emits_pcm_and_final_text(self) -> None:
        client, events, _, errors, playback = make_client(audio_output=True)
        client._handle_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "我们开始开会。",
                },
                ensure_ascii=False,
            )
        )
        pcm = b"\x00\x00\x01\x00"
        client._handle_message(
            json.dumps(
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(pcm).decode("ascii"),
                }
            )
        )
        client._handle_message(
            json.dumps(
                {
                    "type": "response.audio_transcript.done",
                    "response_id": "resp-2",
                    "transcript": "Let's start the meeting.",
                }
            )
        )

        self.assertEqual(errors, [])
        self.assertEqual(playback.audio, [pcm])  # type: ignore[union-attr]
        self.assertEqual(events[-1].source_text, "我们开始开会。")
        self.assertEqual(events[-1].translated_text, "Let's start the meeting.")

    def test_usage_details_are_reported(self) -> None:
        client, _, usages, _, _ = make_client()
        client._handle_message(
            json.dumps(
                {
                    "type": "response.done",
                    "response": {
                        "usage": {
                            "input_tokens_details": {
                                "text_tokens": 2,
                                "audio_tokens": 70,
                            },
                            "output_tokens_details": {
                                "text_tokens": 8,
                                "audio_tokens": 25,
                            },
                        }
                    },
                }
            )
        )
        self.assertEqual(usages, [UsageStats(2, 70, 8, 25)])
        self.assertEqual(usages[0].total_tokens, 105)


if __name__ == "__main__":
    unittest.main()
