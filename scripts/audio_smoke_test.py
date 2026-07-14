"""Open the physical microphone and speaker loopback concurrently for 0.4 s."""

from __future__ import annotations

import threading

import soundcard as sc


def capture(name: str, device: object, results: list[str], errors: list[str]) -> None:
    try:
        with device.recorder(
            samplerate=16_000,
            channels=1,
            blocksize=1_280,
        ) as recorder:
            for _ in range(20):
                recorder.record(numframes=320)
        results.append(name)
    except Exception as exc:
        errors.append(f"{name}: {exc}")


def main() -> int:
    physical = list(sc.all_microphones(include_loopback=False))
    loopbacks = [
        device
        for device in sc.all_microphones(include_loopback=True)
        if bool(getattr(device, "isloopback", False))
    ]
    if not physical or not loopbacks:
        print("FAIL: physical microphone or loopback endpoint not found")
        return 1

    results: list[str] = []
    errors: list[str] = []
    workers = [
        threading.Thread(
            target=capture,
            args=("physical microphone", physical[0], results, errors),
        ),
        threading.Thread(
            target=capture,
            args=("speaker loopback", loopbacks[0], results, errors),
        ),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: concurrent capture opened " + " and ".join(sorted(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

