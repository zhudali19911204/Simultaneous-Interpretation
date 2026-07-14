from __future__ import annotations

import base64
import json
import socket
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.qwen_backend import (  # noqa: E402
    BLOCK_FRAMES,
    EnergySpeechGate,
    GATE_HANGOVER_BLOCKS,
    INPUT_SAMPLE_RATE,
    MODEL_NAME,
    OUTPUT_SAMPLE_RATE,
    PLAYBACK_BLOCK_FRAMES,
    QwenLiveTranslateClient,
    UsageStats,
    build_api_url,
)


class FakePlayback:
    def __init__(self) -> None:
        self.audio: list[bytes] = []

    def enqueue(self, pcm: bytes) -> None:
        self.audio.append(pcm)


def make_client(*, audio_output: bool = False):
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
        name="test",
        use_silence_gate=True,
    )
    return client, events, usages, errors, playback


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
