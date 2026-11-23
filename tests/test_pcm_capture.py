from __future__ import annotations

import sys
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simultaneous_interpreter.qwen_backend import (
    BLOCK_FRAMES,
    CAPTURE_QUEUE_BLOCKS,
    GATE_PRE_ROLL_BLOCKS,
    MAX_APPEND_BLOCKS,
    PcmCaptureWorker,
    _CaptureBuffer,
)


class CaptureTests(unittest.TestCase):
    def test_short_send_stall_preserves_order_and_capture_continues(self) -> None:
        sending = threading.Event()
        captured = threading.Event()
        resume_send = threading.Event()
        end_recording = threading.Event()
        chunks: list[bytes] = []
        errors: list[str] = []
        blocks = [
            np.full((BLOCK_FRAMES, 1), level / 10, dtype=np.float32)
            for level in range(1, 7)
        ]
        frames = iter(blocks)

        def record(*, numframes: int) -> np.ndarray:
            block = next(frames, None)
            if block is None:
                captured.set()
                end_recording.wait(2)
                return blocks[-1]
            if block is blocks[1]:
                sending.wait(2)
            return block

        def send(pcm: bytes) -> None:
            chunks.append(pcm)
            sending.set()
            resume_send.wait(2)

        device = Mock()
        device.recorder.return_value = nullcontext(Mock(record=record))
        worker = PcmCaptureWorker(
            input_device=device, on_error=errors.append, use_silence_gate=False
        )
        worker.start(send)
        try:
            self.assertTrue(sending.wait(1))
            self.assertTrue(captured.wait(1))
            self.assertEqual(len(chunks), 1)
        finally:
            worker._buffer.close()
            end_recording.set()
            resume_send.set()
            worker.stop()

        expected = (np.concatenate(blocks) * 32767).astype("<i2").tobytes()
        self.assertEqual(b"".join(chunks), expected)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(errors, [])

    def test_full_buffer_backpressures_capture_and_drains_in_order(self) -> None:
        buffer = _CaptureBuffer()
        blocks = [bytes([value]) for value in range(CAPTURE_QUEUE_BLOCKS + 1)]
        buffer.put(blocks[:-1])
        started = threading.Event()
        appended = threading.Event()

        def put_last_block() -> None:
            started.set()
            buffer.put(blocks[-1:])
            appended.set()

        producer = threading.Thread(target=put_last_block, daemon=True)
        producer.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertFalse(appended.is_set())
            first = buffer.take()
            self.assertEqual(first, b"".join(blocks[:MAX_APPEND_BLOCKS]))
            self.assertTrue(appended.wait(1))
        finally:
            buffer.close()
            producer.join(1)
        received = first + b"".join(iter(buffer.take, b""))
        self.assertEqual(received, b"".join(blocks))

    def test_gate_preroll_is_sent_together_and_disconnect_discards_backlog(self) -> None:
        buffer = _CaptureBuffer()
        preroll = [bytes([value]) for value in range(GATE_PRE_ROLL_BLOCKS)]
        buffer.put(preroll)
        self.assertEqual(buffer.take(), b"".join(preroll))
        buffer.put([b"old"])
        buffer.close(discard=True)
        buffer.put([b"late"])
        self.assertEqual(buffer.take(), b"")
