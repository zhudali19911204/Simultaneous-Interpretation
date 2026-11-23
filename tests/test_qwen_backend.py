from __future__ import annotations

import base64
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import websocket


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
    QWEN35_MODEL_NAME,
    QWEN38_MODEL_NAME,
    QwenInterpreterSession,
    QwenLiveTranslateClient,
    TranslationAligner,
    UsageStats,
    build_api_url,
    format_startup_failure,
    is_retryable_startup_failure,
    reconnect_delay,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakePlayback:
    def __init__(self) -> None:
        self.audio: list[bytes] = []

    def enqueue(self, pcm: bytes) -> None:
        self.audio.append(pcm)


def make_client(
    *,
    audio_output: bool = False,
    model: str | None = None,
    use_silence_gate: bool = True,
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
        use_silence_gate=use_silence_gate,
        **({"model": model} if model is not None else {}),
    )
    return client, events, usages, errors, playback


class FakeCapture:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.starts = 0
        self.stops = 0

    def start(self, _on_audio) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.starts += 1

    def request_stop(self) -> None:
        self.active = 0

    def stop(self) -> bool:
        self.active = 0
        self.stops += 1
        return True


class InterpreterSessionOutputTests(unittest.TestCase):
    def test_fast_subtitles_changes_both_official_qwen38_directions_only(self) -> None:
        def session(model: str, url: str, *, fast: bool) -> QwenInterpreterSession:
            return QwenInterpreterSession(
                api_key="test-key", workspace_id="ws-test",
                model=model, websocket_url=url,
                microphone=object(), teams_loopback=object(),
                virtual_output=object(), english_voice="Tina",
                on_incoming=lambda _event: None, on_outgoing=lambda _event: None,
                on_usage=lambda _usage: None, on_error=lambda _message: None,
                use_silence_gate=True, qwen38_fast_subtitles=fast,
            )

        for model, url, supported in (
            (QWEN38_MODEL_NAME, "", True),
            (QWEN38_MODEL_NAME + "-2026-09-17", "", True),
            (QWEN35_MODEL_NAME, "", False),
            (QWEN38_MODEL_NAME, "wss://gateway.example.com/realtime", False),
            ("vendor/other", "wss://gateway.example.com/realtime", False),
        ):
            with self.subTest(model=model, url=url):
                baseline = session(model, url, fast=False)
                fast_session = session(model, url, fast=True)
                for original, fast in (
                    (baseline._incoming, fast_session._incoming),
                    (baseline._outgoing, fast_session._outgoing),
                ):
                    expected = original.build_session_update()["session"]
                    if supported:
                        self.assertEqual(
                            expected["audio"]["input"]["turn_detection"],
                            {"type": "speaker_detection", "threshold": 0.5},
                        )
                        expected["audio"]["input"]["turn_detection"] = {
                            "type": "server_vad",
                        }
                        self.assertFalse(fast._capture._use_silence_gate)
                    self.assertEqual(fast.build_session_update()["session"], expected)

    @patch("simultaneous_interpreter.qwen_backend.PcmPlaybackWorker")
    @patch("simultaneous_interpreter.qwen_backend.QwenLiveTranslateClient")
    def test_english_audio_can_be_disabled_without_stopping_text_translation(
        self,
        live_client,
        playback_worker,
    ) -> None:
        session = QwenInterpreterSession(
            api_key="test-key",
            workspace_id="ws-test",
            microphone=object(),
            teams_loopback=object(),
            virtual_output=None,
            english_voice="Tina",
            on_incoming=lambda _event: None,
            on_outgoing=lambda _event: None,
            on_usage=lambda _usage: None,
            on_error=lambda _message: None,
            use_silence_gate=True,
            english_audio_output=False,
        )

        playback_worker.assert_not_called()
        outgoing_options = live_client.call_args_list[0].kwargs
        self.assertFalse(outgoing_options["audio_output"])
        self.assertIsNone(outgoing_options["playback"])
        for call in live_client.call_args_list:
            self.assertEqual(call.kwargs["model"], QWEN38_MODEL_NAME)
        session.start()
        session.test_output()
        session.stop()


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
            "/api-ws/v1/realtime?model=qwen3.8-livetranslate-flash-realtime",
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

    def test_qwen35_audio_session_configuration(self) -> None:
        client, *_ = make_client(model=QWEN35_MODEL_NAME, audio_output=True)
        session = client.build_session_update()["session"]
        self.assertEqual(session["modalities"], ["text", "audio"])
        self.assertEqual(session["input_audio_transcription"]["language"], "zh")
        self.assertEqual(session["translation"]["language"], "en")
        self.assertEqual(session["voice"], "Tina")

    def test_qwen35_text_session_configuration(self) -> None:
        client, *_ = make_client(model=QWEN35_MODEL_NAME, audio_output=False)
        session = client.build_session_update()["session"]
        self.assertEqual(session["modalities"], ["text"])
        self.assertEqual(session["input_audio_transcription"]["language"], "en")
        self.assertEqual(session["translation"]["language"], "zh")
        self.assertNotIn("voice", session)

    def test_default_client_uses_qwen38_audio_and_output_configuration(self) -> None:
        for audio_output in (False, True):
            with self.subTest(audio_output=audio_output):
                client, *_ = make_client(audio_output=audio_output)
                self.assertTrue(client._api_url.endswith(f"model={QWEN38_MODEL_NAME}"))
                session = client.build_session_update()["session"]
                self.assertEqual(session["input_modalities"], ["audio"])
                self.assertEqual(
                    session["output_modalities"],
                    ["text", "audio"] if audio_output else ["text"],
                )
                self.assertEqual(
                    session["audio"]["input"]["format"],
                    {"type": "pcm", "sample_rate": INPUT_SAMPLE_RATE},
                )
                self.assertEqual(
                    session["audio"]["input"]["turn_detection"],
                    {"type": "speaker_detection", "threshold": 0.5},
                )
                self.assertEqual(
                    session["translation"]["language"],
                    "en" if audio_output else "zh",
                )
                if audio_output:
                    self.assertEqual(session["audio"]["output"]["voice"], "Tina")
                else:
                    self.assertNotIn("output", session["audio"])
                for legacy_field in (
                    "modalities", "sample_rate", "input_audio_format",
                    "output_audio_format", "input_audio_transcription", "voice",
                ):
                    self.assertNotIn(legacy_field, session)

    def test_qwen38_keeps_audio_continuous_for_server_speech_detection(self) -> None:
        for model in (
            "qwen3.8-livetranslate-flash-realtime",
            "qwen3.8-livetranslate-flash-realtime-2026-09-17",
        ):
            with self.subTest(model=model):
                client, *_ = make_client(model=model)
                self.assertFalse(client._capture._use_silence_gate)
        for use_silence_gate in (False, True):
            client, *_ = make_client(
                model=QWEN35_MODEL_NAME, use_silence_gate=use_silence_gate,
            )
            self.assertEqual(client._capture._use_silence_gate, use_silence_gate)

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

    def test_startup_failure_classifies_dns_and_fatal_configuration(self) -> None:
        self.assertTrue(
            is_retryable_startup_failure(
                "WebSocketAddressException: [Errno 11002] getaddrinfo failed",
                None,
                True,
            )
        )
        self.assertFalse(
            is_retryable_startup_failure(
                "服务错误 invalid_value：bad model",
                1008,
                False,
            )
        )
        message = format_startup_failure(
            "WebSocketAddressException: getaddrinfo failed",
            2,
        )
        self.assertIn("DNS", message)
        self.assertIn("自动重试 2 次", message)


class ReconnectionTests(unittest.TestCase):
    def test_initial_dns_failure_retries_and_then_connects(self) -> None:
        statuses = []
        release = threading.Event()

        class DnsThenConnectedWebSocket:
            instances = 0

            def __init__(self, _url, **callbacks):
                self.index = type(self).instances
                type(self).instances += 1
                self.callbacks = callbacks
                self.closed = False

            def send(self, payload):
                event = json.loads(payload)
                if event.get("type") == "session.finish":
                    self.callbacks["on_message"](
                        self,
                        json.dumps({"type": "session.finished"}),
                    )

            def close(self):
                if not self.closed:
                    self.closed = True
                    self.callbacks["on_close"](self, 1000, "")
                    release.set()

            def run_forever(self, **_options):
                if self.index == 0:
                    self.callbacks["on_error"](
                        self,
                        websocket.WebSocketAddressException(
                            "[Errno 11002] getaddrinfo failed"
                        ),
                    )
                    self.callbacks["on_close"](self, None, None)
                    return
                self.callbacks["on_open"](self)
                self.callbacks["on_message"](
                    self,
                    json.dumps({"type": "session.updated"}),
                )
                release.wait(2.0)

        client, *_ = make_client(statuses=statuses)
        client._capture = FakeCapture()  # type: ignore[assignment]
        with patch(
            "simultaneous_interpreter.qwen_backend.websocket.WebSocketApp",
            DnsThenConnectedWebSocket,
        ), patch(
            "simultaneous_interpreter.qwen_backend.reconnect_delay",
            return_value=0.01,
        ):
            client.start()
            client.stop()

        self.assertEqual(DnsThenConnectedWebSocket.instances, 2)
        self.assertIn("reconnecting", [status.state for status in statuses])
        self.assertIn("connected", [status.state for status in statuses])

    def test_persistent_initial_dns_failure_reports_actionable_error(self) -> None:
        statuses = []

        class DnsFailureWebSocket:
            instances = 0

            def __init__(self, _url, **callbacks):
                type(self).instances += 1
                self.callbacks = callbacks

            def send(self, _payload):
                return None

            def close(self):
                return None

            def run_forever(self, **_options):
                self.callbacks["on_error"](
                    self,
                    websocket.WebSocketAddressException(
                        "[Errno 11002] getaddrinfo failed"
                    ),
                )
                self.callbacks["on_close"](self, None, None)

        client, *_ = make_client(statuses=statuses)
        client._capture = FakeCapture()  # type: ignore[assignment]
        with patch(
            "simultaneous_interpreter.qwen_backend.websocket.WebSocketApp",
            DnsFailureWebSocket,
        ), patch(
            "simultaneous_interpreter.qwen_backend.reconnect_delay",
            return_value=0.01,
        ), patch(
            "simultaneous_interpreter.qwen_backend.INITIAL_CONNECTION_TIMEOUT_SECONDS",
            0.06,
        ):
            with self.assertRaisesRegex(RuntimeError, "DNS"):
                client.start()

        self.assertGreater(DnsFailureWebSocket.instances, 1)
        self.assertEqual(statuses[-1].state, "failed")
        self.assertIn("手机热点", statuses[-1].detail)

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
        client._send_audio(socket, b"\x00\x00")  # type: ignore[arg-type]
        self.assertIn("发送音频失败", client._last_transport_error)
        self.assertFalse(client._configured)
        self.assertTrue(socket.closed)
        self.assertEqual(errors, [])

    def test_previous_connection_send_failure_cannot_stop_new_capture(self) -> None:
        client, _, _, errors, _ = make_client()
        in_send = threading.Event()
        disconnected = threading.Event()

        class OldSocket:
            def send(self, _payload: str) -> None:
                in_send.set()
                disconnected.wait(2)
                raise OSError("old connection closed")

        old = OldSocket()
        client._configured = True
        client._ws = old  # type: ignore[assignment]
        sender = threading.Thread(
            target=client._send_audio, args=(old, b"old"), daemon=True
        )
        sender.start()
        try:
            self.assertTrue(in_send.wait(1))
            client._ws = object()  # type: ignore[assignment]
        finally:
            disconnected.set()
            sender.join(1)
        client._send_audio(old, b"queued")  # type: ignore[arg-type]
        self.assertEqual(client._last_transport_error, "")
        self.assertTrue(client._configured)
        self.assertEqual(errors, [])


    def test_reconnect_flushes_old_pair_and_ignores_stale_generation(self) -> None:
        client, events, _, _, _ = make_client()
        client._generation = 1
        client._handle_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "source-old",
                    "transcript": "Old source",
                }
            )
        )
        client._flush_alignment("disconnect")
        self.assertEqual(events[-1].alignment_status, "source_only")

        client._generation = 2
        client._reset_alignment()
        client._on_message(
            1,
            object(),  # type: ignore[arg-type]
            json.dumps(
                {
                    "type": "response.text.done",
                    "response_id": "resp-old",
                    "text": "旧译文",
                },
                ensure_ascii=False,
            ),
        )
        self.assertEqual(len([event for event in events if event.is_final]), 1)

        client._handle_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "source-new",
                    "transcript": "New source",
                }
            )
        )
        client._handle_message(
            json.dumps(
                {
                    "type": "response.text.done",
                    "response_id": "resp-new",
                    "text": "新译文",
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual(events[-1].source_text, "New source")
        self.assertEqual(events[-1].translated_text, "新译文")


class TranslationAlignerTests(unittest.TestCase):
    def test_translation_can_complete_before_source(self) -> None:
        clock = FakeClock()
        aligner = TranslationAligner(clock=clock)

        aligner.response_started("resp-a")
        preview = aligner.translation_completed(
            "resp-a",
            "output-a",
            "四个小时。",
        )
        self.assertFalse(preview[-1].event.is_final)

        clock.advance(0.4)
        outputs = aligner.source_completed(
            "source-a",
            "Four hours.",
        )
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].event.source_text, "Four hours.")
        self.assertEqual(outputs[0].event.translated_text, "四个小时。")
        self.assertEqual(outputs[0].event.alignment_status, "matched")
        self.assertTrue(outputs[0].event.is_final)

    def test_interleaved_segments_keep_original_order(self) -> None:
        aligner = TranslationAligner(clock=FakeClock())

        aligner.speech_started()
        aligner.source_preview("source-a", "Sentence A")
        aligner.speech_stopped()
        aligner.response_started("resp-a")

        aligner.speech_started()
        aligner.source_preview("source-b", "Sentence B")
        aligner.speech_stopped()
        aligner.response_started("resp-b")

        aligner.translation_completed("resp-a", "out-a", "译文 A")
        aligner.translation_completed("resp-b", "out-b", "译文 B")
        self.assertFalse(
            any(
                output.event.is_final
                for output in aligner.source_completed("source-b", "Sentence B")
            )
        )
        outputs = aligner.source_completed("source-a", "Sentence A")

        self.assertEqual(
            [output.event.source_text for output in outputs],
            ["Sentence A", "Sentence B"],
        )
        self.assertEqual(
            [output.event.translated_text for output in outputs],
            ["译文 A", "译文 B"],
        )

    def test_duplicate_response_completion_is_ignored(self) -> None:
        aligner = TranslationAligner(clock=FakeClock())
        aligner.source_completed("source-a", "Hello")
        first = aligner.translation_completed("resp-a", "out-a", "你好")
        duplicate = aligner.translation_completed("resp-a", "out-a", "你好")

        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].event.is_final)
        self.assertEqual(duplicate, ())

    def test_unmatched_segment_expires_without_shifting_next_pair(self) -> None:
        clock = FakeClock()
        aligner = TranslationAligner(timeout_seconds=5.0, clock=clock)
        aligner.source_completed("source-a", "Untranslated sentence")

        clock.advance(5.1)
        expired = aligner.expire()
        self.assertEqual(expired[0].event.alignment_status, "source_only")
        self.assertEqual(expired[0].reason, "timeout")

        aligner.source_completed("source-b", "Next sentence")
        outputs = aligner.translation_completed("resp-b", "out-b", "下一句")
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].event.source_text, "Next sentence")
        self.assertEqual(outputs[0].event.translated_text, "下一句")

    def test_other_segments_and_repeated_snapshots_do_not_delay_timeout(self) -> None:
        clock = FakeClock()
        aligner = TranslationAligner(timeout_seconds=5.0, clock=clock)
        aligner.source_completed("a", "First")
        clock.advance(1.0)
        aligner.translation_preview("a", "out-a", "首")
        clock.advance(3.0)
        aligner.translation_preview("a", "out-a", "首")
        aligner.source_completed("a", "First")
        aligner.source_completed("b", "Second")
        aligner.translation_completed("b", "out-b", "第二句。")
        clock.advance(2.0)

        outputs = aligner.expire()
        self.assertEqual(
            [(output.event.source_text, output.reason) for output in outputs],
            [("First", "timeout"), ("Second", "completed")],
        )
        self.assertEqual(outputs[0].event.alignment_status, "source_only")
        self.assertEqual(outputs[1].first_target, "final_only")
        self.assertIsNone(outputs[1].first_target_ms)

    def test_new_sentence_preview_follows_an_older_timeout(self) -> None:
        clock = FakeClock()
        aligner = TranslationAligner(clock=clock)
        aligner.source_completed("a", "First")
        clock.advance(5.1)
        outputs = aligner.source_completed("b", "Second")
        self.assertEqual(
            [(output.event.source_text, output.reason) for output in outputs],
            [("First", "timeout"), ("Second", "preview")],
        )
        self.assertEqual(outputs[0].first_target, "none")
        self.assertIsNone(outputs[0].first_target_ms)

    def test_source_progress_keeps_completed_translation_waiting(self) -> None:
        clock = FakeClock()
        aligner = TranslationAligner(timeout_seconds=5.0, clock=clock)
        aligner.translation_completed("a", "out-a", "完整译文")
        clock.advance(4.0)
        aligner.source_preview("a", "Full")
        clock.advance(4.0)
        self.assertEqual(aligner.expire(), ())
        aligner.source_delta("a", " text")
        clock.advance(4.0)
        self.assertEqual(aligner.expire(), ())

        outputs = aligner.source_completed("a", "Full text.")
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].event.alignment_status, "matched")

    def test_flush_preserves_only_completed_side(self) -> None:
        aligner = TranslationAligner(clock=FakeClock())
        aligner.source_preview("source-a", "unfinished preview")
        aligner.translation_completed("resp-a", "out-a", "最终译文")

        outputs = aligner.flush("disconnect")
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].event.source_text, "")
        self.assertEqual(outputs[0].event.translated_text, "最终译文")
        self.assertEqual(outputs[0].event.alignment_status, "translation_only")
        self.assertEqual(outputs[0].reason, "disconnect")


class ResponseParsingTests(unittest.TestCase):
    def test_first_target_diagnostics_follow_their_own_speech_start(self) -> None:
        for event_type, field in (
            ("response.text.delta", "delta"),
            ("response.text.text", "text"),
        ):
            with self.subTest(event_type=event_type):
                diagnostics = []
                client, *_ = make_client(diagnostics=diagnostics)
                clock = FakeClock()
                client._aligner = TranslationAligner(clock=clock)
                self.addCleanup(client._reset_alignment)

                def receive(kind: str, **fields: str) -> None:
                    client._handle_message(json.dumps({"type": kind, **fields}))

                receive("input_audio_buffer.speech_started")
                receive(
                    "conversation.item.input_audio_transcription.completed",
                    item_id="source-a", transcript="Private source A",
                )
                clock.advance(0.25)
                receive(event_type, response_id="resp-a", **{field: " "})
                clock.advance(0.5)
                receive(event_type, response_id="resp-a", **{field: "Private A"})
                clock.advance(0.25)
                receive("input_audio_buffer.speech_started")
                clock.advance(0.5)
                receive(
                    "response.text.done", response_id="resp-b", text="Private B",
                )
                receive(
                    "conversation.item.input_audio_transcription.completed",
                    item_id="source-b", transcript="Private source B",
                )
                self.assertEqual(diagnostics, [])
                clock.advance(0.5)
                receive(
                    "response.text.done", response_id="resp-a", text="Private A.",
                )
                self.assertEqual(len(diagnostics), 2)
                self.assertIn(
                    "first_target=preview first_target_ms=750", diagnostics[0].detail,
                )
                self.assertIn(
                    "first_target=final_only first_target_ms=500", diagnostics[1].detail,
                )
                for status in diagnostics:
                    self.assertNotIn("resp-", status.detail)
                    self.assertNotIn("Private", status.detail)

    def test_long_translation_keeps_streaming_across_timer_wakeups(self) -> None:
        client, events, *_ = make_client()
        clock = FakeClock()
        client._aligner = TranslationAligner(clock=clock)
        with patch("simultaneous_interpreter.qwen_backend.threading.Timer") as timer:
            client._handle_message(json.dumps({
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "source", "transcript": "One long sentence.",
            }))
            first_wakeup = timer.call_args.kwargs["args"]
            for _ in range(4):
                clock.advance(1.0)
                client._handle_message(json.dumps({
                    "type": "response.text.delta", "response_id": "response",
                    "delta": "字",
                }))
            timer.assert_called_once()
            clock.advance(1.0)
            client._on_alignment_timeout(*first_wakeup)
            self.assertEqual(timer.call_count, 2)
            self.assertEqual(timer.call_args.args[0], 4.0)
            self.assertFalse(any(event.is_final for event in events))
            clock.advance(2.0)
            client._handle_message(json.dumps({
                "type": "response.text.done", "response_id": "response",
                "text": "字字字字。",
            }))
            self.assertIsNone(client._alignment_timer)
            self.assertEqual(len({event.segment_id for event in events}), 1)
            self.assertEqual(
                [event.translated_text for event in events if event.is_final],
                ["字字字字。"],
            )

    def test_segment_id_is_stable_until_final_and_unique_across_connections(
        self,
    ) -> None:
        client, events, *_ = make_client()
        self.addCleanup(client._reset_alignment)
        preview = json.dumps({
            "type": "response.text.delta",
            "response_id": "response-a", "delta": "你好",
        })
        client._handle_message(preview)
        client._handle_message(json.dumps({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "source-a", "transcript": "Hello",
        }))
        client._handle_message(json.dumps({
            "type": "response.text.done",
            "response_id": "response-a", "text": "你好",
        }))
        first_id = events[0].segment_id
        self.assertTrue(first_id)
        self.assertTrue(events[-1].is_final)
        self.assertEqual({event.segment_id for event in events}, {first_id})

        client._prepare_connection(1)
        client._handle_message(preview)
        self.assertNotEqual(events[-1].segment_id, first_id)

        other_client, other_events, *_ = make_client()
        other_client._handle_message(preview)
        self.assertNotEqual(other_events[0].segment_id, first_id)

    def test_streaming_deltas_reuse_timeout_and_still_expire_missing_source(
        self,
    ) -> None:
        client, events, *_ = make_client(
            model="qwen3.8-livetranslate-flash-realtime",
        )
        clock = FakeClock()
        client._aligner = TranslationAligner(clock=clock)
        with patch("simultaneous_interpreter.qwen_backend.threading.Timer") as timer:
            client._handle_message(json.dumps({
                "type": "input_audio_buffer.speech_started",
            }))
            client._handle_message(json.dumps({
                "type": "input_audio_buffer.speech_stopped",
            }))
            for _ in range(100):
                client._handle_message(json.dumps({
                    "type": "response.text.delta",
                    "response_id": "resp-1", "delta": "字",
                }))
            timer.assert_called_once()
            self.assertEqual(events[-1].translated_text, "字" * 100)
            client._handle_message(json.dumps({
                "type": "response.text.done",
                "response_id": "resp-1", "text": "字" * 100,
            }))
            timer.assert_called_once()
            timeout_args = timer.call_args.kwargs["args"]
            clock.advance(5.1)
            client._on_alignment_timeout(*timeout_args)
            self.assertTrue(events[-1].is_final)
            self.assertEqual(events[-1].alignment_status, "translation_only")
            self.assertIsNone(client._alignment_timer)

    def test_reconnect_drops_old_deltas_and_resets_accumulated_text(self) -> None:
        client, events, *_ = make_client(
            model="qwen3.8-livetranslate-flash-realtime",
        )
        client._generation = 1

        def message(delta: str) -> str:
            return json.dumps({
                "type": "response.text.delta",
                "response_id": "resp-1", "item_id": "out-1", "delta": delta,
            })

        client._on_message(1, object(), message("Old"))
        client._prepare_connection(1)
        client._on_message(1, object(), message(" stale"))
        client._on_message(2, object(), message("New"))
        self.assertEqual(
            [event.translated_text for event in events], ["Old", "New"],
        )

    def test_qwen38_source_deltas_stream_before_completion(self) -> None:
        client, events, _, errors, _ = make_client(
            model="qwen3.8-livetranslate-flash-realtime",
        )
        for delta in ("Please", " ", "review", " the proposal."):
            client._handle_message(json.dumps({
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "source-1",
                "delta": delta,
            }))
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(events), 3)
        self.assertEqual(events[0].source_text, "Please")
        self.assertEqual(events[-1].source_text, "Please review the proposal.")
        self.assertFalse(any(event.is_final for event in events))

    def test_qwen38_translation_deltas_stream_and_keep_word_boundaries(self) -> None:
        for event_type in ("response.text.delta", "response.audio_transcript.delta"):
            with self.subTest(event_type=event_type):
                client, events, _, errors, _ = make_client(
                    model="qwen3.8-livetranslate-flash-realtime",
                    audio_output="audio_transcript" in event_type,
                )
                for delta in ("Let's", " ", "start", " the meeting."):
                    client._handle_message(json.dumps({
                        "type": event_type,
                        "response_id": "resp-1",
                        "item_id": "output-1",
                        "delta": delta,
                    }))
                self.assertEqual(errors, [])
                self.assertGreaterEqual(len(events), 3)
                self.assertEqual(events[0].translated_text, "Let's")
                self.assertEqual(
                    events[-1].translated_text, "Let's start the meeting.",
                )
                self.assertFalse(any(event.is_final for event in events))

    def test_qwen38_interleaved_deltas_finalize_once_without_mixing(self) -> None:
        client, events, _, _, _ = make_client(
            model="qwen3.8-livetranslate-flash-realtime",
        )
        self.addCleanup(client._reset_alignment)
        for item_id, delta in (("a", "First"), ("b", "Second"), ("a", " sentence")):
            client._handle_message(json.dumps({
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": item_id,
                "delta": delta,
            }))
        for response_id, delta in (("a", "第一"), ("b", "第二"), ("a", "句")):
            client._handle_message(json.dumps({
                "type": "response.text.delta",
                "response_id": response_id,
                "item_id": "out-" + response_id,
                "delta": delta,
            }))
        self.assertTrue(events)
        self.assertEqual(events[-1].source_text, "First sentence")
        self.assertEqual(events[-1].translated_text, "第一句")
        for item_id, source, translated in (
            ("a", "First sentence.", "第一句。"),
            ("b", "Second sentence.", "第二句。"),
        ):
            for event in (
                {"type": "conversation.item.input_audio_transcription.completed",
                 "item_id": item_id, "transcript": source},
                {"type": "response.text.done", "response_id": item_id,
                 "item_id": "out-" + item_id, "text": translated},
                {"type": "response.text.delta", "response_id": item_id,
                 "item_id": "out-" + item_id, "delta": "late duplicate"},
                {"type": "response.done", "response": {"id": item_id}},
            ):
                client._handle_message(json.dumps(event))
        finals = [event for event in events if event.is_final]
        self.assertEqual(
            [(event.source_text, event.translated_text) for event in finals],
            [("First sentence.", "第一句。"), ("Second sentence.", "第二句。")],
        )
        self.assertFalse(any("duplicate" in event.translated_text for event in events))

    def test_qwen38_first_audio_chunk_does_not_wait_for_final_text(self) -> None:
        client, events, _, errors, playback = make_client(
            model="qwen3.8-livetranslate-flash-realtime", audio_output=True,
        )
        pcm = b"\x00\x00\x01\x00"
        client._handle_message(json.dumps({
            "type": "response.audio_transcript.delta",
            "response_id": "resp-1", "item_id": "out-1", "delta": "Hello",
        }))
        client._handle_message(json.dumps({
            "type": "response.audio.delta",
            "response_id": "resp-1",
            "delta": base64.b64encode(pcm).decode("ascii"),
        }))
        self.assertEqual(errors, [])
        self.assertEqual(playback.audio, [pcm])
        self.assertTrue(events)
        self.assertFalse(any(event.is_final for event in events))

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

    def test_text_and_audio_done_for_same_response_emit_one_final(self) -> None:
        client, events, _, _, _ = make_client(audio_output=True)
        client._handle_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "source-1",
                    "transcript": "你好",
                },
                ensure_ascii=False,
            )
        )
        for event_type, key in (
            ("response.text.done", "text"),
            ("response.audio_transcript.done", "transcript"),
        ):
            client._handle_message(
                json.dumps(
                    {
                        "type": event_type,
                        "response_id": "resp-1",
                        "item_id": "output-1",
                        key: "Hello",
                    }
                )
            )

        finals = [event for event in events if event.is_final]
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0].source_text, "你好")
        self.assertEqual(finals[0].translated_text, "Hello")

    def test_alignment_diagnostic_does_not_contain_transcript(self) -> None:
        diagnostics = []
        client, _, _, _, _ = make_client(diagnostics=diagnostics)
        client._handle_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "source-private",
                    "transcript": "private meeting content",
                }
            )
        )
        client._handle_message(
            json.dumps(
                {
                    "type": "response.text.done",
                    "response_id": "response-private",
                    "text": "confidential translation",
                }
            )
        )

        detail = diagnostics[-1].detail
        self.assertIn("event=alignment.finalized", detail)
        self.assertIn("first_target=final_only", detail)
        self.assertNotIn("first_target_ms=", detail)
        self.assertNotIn("private meeting content", detail)
        self.assertNotIn("confidential translation", detail)

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
