from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from simultaneous_interpreter.brand_logo import (  # noqa: E402
    LOGO_DARK,
    LOGO_ORANGE,
    render_brand_logo,
)


class BrandLogoTests(unittest.TestCase):
    def test_logo_has_requested_size_and_transparent_background(self) -> None:
        image = render_brand_logo(52, 56)

        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (52, 56))
        self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_logo_contains_dark_and_orange_brand_shapes(self) -> None:
        image = render_brand_logo(104, 112, oversample=1)
        colors = {
            color
            for _count, color in image.getcolors(
                maxcolors=image.width * image.height
            )
        }

        expected_dark = tuple(bytes.fromhex(LOGO_DARK.removeprefix("#")))
        expected_orange = tuple(bytes.fromhex(LOGO_ORANGE.removeprefix("#")))
        self.assertTrue(any(pixel[:3] == expected_dark for pixel in colors))
        self.assertTrue(any(pixel[:3] == expected_orange for pixel in colors))

    def test_invalid_size_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            render_brand_logo(0, 56)


if __name__ == "__main__":
    unittest.main()
