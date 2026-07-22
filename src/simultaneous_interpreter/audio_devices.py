from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import soundcard as sc


@dataclass(frozen=True)
class AudioDeviceChoice:
    label: str
    device: Any


class AudioDeviceCatalog:
    """Enumerates Windows audio endpoints through WASAPI."""

    def __init__(self) -> None:
        self.inputs: list[AudioDeviceChoice] = []
        self.loopbacks: list[AudioDeviceChoice] = []
        self.outputs: list[AudioDeviceChoice] = []
        self.default_input_label = ""
        self.default_loopback_label = ""
        self.default_output_label = ""

    def refresh(self) -> None:
        all_microphones = list(sc.all_microphones(include_loopback=True))
        physical_microphones = [
            device for device in all_microphones if not self._is_loopback(device)
        ]
        loopback_microphones = [
            device for device in all_microphones if self._is_loopback(device)
        ]

        # Some SoundCard backends don't expose isloopback. In that case the
        # non-loopback query remains a reliable source for physical inputs.
        if not physical_microphones:
            physical_microphones = list(sc.all_microphones(include_loopback=False))

        self.inputs = self._make_choices(physical_microphones, "麦克风")
        self.loopbacks = self._make_choices(loopback_microphones, "回放捕获")
        self.outputs = self._make_choices(list(sc.all_speakers()), "扬声器")

        default_input = self._safe_default(sc.default_microphone)
        default_output = self._safe_default(sc.default_speaker)
        self.default_input_label = self._match_default(self.inputs, default_input)
        self.default_output_label = self._preferred_output(self.outputs, default_output)
        self.default_loopback_label = self._preferred_loopback(
            self.loopbacks, default_output
        )

    @staticmethod
    def _safe_default(factory: Any) -> Any | None:
        try:
            return factory()
        except Exception:
            return None

    @staticmethod
    def _is_loopback(device: Any) -> bool:
        marker = getattr(device, "isloopback", False)
        if callable(marker):
            try:
                marker = marker()
            except Exception:
                marker = False
        if marker:
            return True
        name = str(getattr(device, "name", device)).lower()
        return "loopback" in name or "回环" in name

    @staticmethod
    def _make_choices(
        devices: Iterable[Any], kind: str
    ) -> list[AudioDeviceChoice]:
        choices: list[AudioDeviceChoice] = []
        counts: dict[str, int] = {}
        for device in devices:
            name = str(getattr(device, "name", device)).strip() or kind
            counts[name] = counts.get(name, 0) + 1
            suffix = f" ({counts[name]})" if counts[name] > 1 else ""
            choices.append(AudioDeviceChoice(f"{name}{suffix}", device))
        return choices

    @staticmethod
    def _same_device(left: Any, right: Any) -> bool:
        if left is None or right is None:
            return False
        left_id = getattr(left, "id", None)
        right_id = getattr(right, "id", None)
        if left_id is not None and right_id is not None:
            return left_id == right_id
        return getattr(left, "name", None) == getattr(right, "name", None)

    def _match_default(
        self, choices: list[AudioDeviceChoice], default: Any | None
    ) -> str:
        for choice in choices:
            if self._same_device(choice.device, default):
                return choice.label
        return choices[0].label if choices else ""

    def _preferred_output(
        self, choices: list[AudioDeviceChoice], default: Any | None
    ) -> str:
        # VB-CABLE's playback endpoint is normally named "CABLE Input".
        for choice in choices:
            lowered = choice.label.lower()
            if "cable input" in lowered or "virtual cable input" in lowered:
                return choice.label
        return self._match_default(choices, default)

    def _preferred_loopback(
        self, choices: list[AudioDeviceChoice], default_output: Any | None
    ) -> str:
        if default_output is not None:
            output_name = str(getattr(default_output, "name", "")).lower()
            for choice in choices:
                loopback_name = str(
                    getattr(choice.device, "name", choice.label)
                ).lower()
                if output_name and (
                    output_name in loopback_name or loopback_name in output_name
                ):
                    return choice.label
        return choices[0].label if choices else ""
