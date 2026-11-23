from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from simultaneous_interpreter.brand_logo import render_brand_logo  # noqa: E402


def main() -> None:
    output_dir = ROOT / "project_presentation_versions"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "brand_logo.png"
    render_brand_logo(width=565, height=648, oversample=2).save(output, "PNG")
    print(output)


if __name__ == "__main__":
    main()
