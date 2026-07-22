from __future__ import annotations

from collections.abc import Sequence

from PIL import Image, ImageDraw


LOGO_ASPECT_RATIO = 565 / 648
LOGO_DARK = "#5B5D62"
LOGO_ORANGE = "#FF6A13"

_UPPER_Z = (
    (0.000, 0.000),
    (1.000, 0.000),
    (0.522, 0.531),
    (0.406, 0.531),
    (0.000, 0.988),
    (0.000, 0.762),
    (0.519, 0.194),
    (0.000, 0.194),
)
_LOWER_Z = (
    (0.244, 1.000),
    (0.929, 1.000),
    (0.929, 0.721),
    (0.497, 0.721),
)
_ORANGE_STROKE = (
    (0.076, 1.000),
    (0.150, 1.000),
    (0.437, 0.646),
    (0.572, 0.646),
    (0.929, 0.262),
    (0.850, 0.262),
    (0.550, 0.591),
    (0.435, 0.591),
)


def _scaled_polygon(
    points: Sequence[tuple[float, float]],
    left: float,
    top: float,
    width: float,
    height: float,
) -> list[tuple[float, float]]:
    return [
        (left + x_position * width, top + y_position * height)
        for x_position, y_position in points
    ]


def render_brand_logo(
    width: int = 52,
    height: int = 56,
    *,
    oversample: int = 4,
) -> Image.Image:
    """Render the application mark on a transparent antialiased canvas."""

    if width <= 0 or height <= 0:
        raise ValueError("Logo 尺寸必须大于 0")
    if oversample <= 0:
        raise ValueError("Logo 抗锯齿倍数必须大于 0")

    render_width = width * oversample
    render_height = height * oversample
    padding = 2 * oversample
    available_width = max(1, render_width - padding * 2)
    available_height = max(1, render_height - padding * 2)
    mark_width = min(available_width, available_height * LOGO_ASPECT_RATIO)
    mark_height = mark_width / LOGO_ASPECT_RATIO
    left = (render_width - mark_width) / 2
    top = (render_height - mark_height) / 2

    image = Image.new("RGBA", (render_width, render_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for polygon in (_UPPER_Z, _LOWER_Z):
        draw.polygon(
            _scaled_polygon(polygon, left, top, mark_width, mark_height),
            fill=LOGO_DARK,
        )
    draw.polygon(
        _scaled_polygon(_ORANGE_STROKE, left, top, mark_width, mark_height),
        fill=LOGO_ORANGE,
    )
    return image.resize((width, height), Image.Resampling.LANCZOS)
