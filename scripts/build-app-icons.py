"""Build deterministic desktop icons from the official Tiangong v3 mark."""
from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "app" / "assets"
SOURCE = ASSETS / "tiangong-logo-icon.png"
WINDOWS_ICON = ASSETS / "tiangong-logo.ico"
MACOS_ICON = ASSETS / "tiangong-logo.icns"


def main() -> None:
    source = Image.open(SOURCE).convert("RGBA")
    if source.size != (512, 512):
        raise SystemExit(f"official icon must be 512x512, got {source.size}")
    source.save(
        WINDOWS_ICON,
        format="ICO",
        sizes=[
            (16, 16),
            (24, 24),
            (32, 32),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        ],
        bitmap_format="png",
    )
    source.save(
        MACOS_ICON,
        format="ICNS",
        append_images=[],
    )
    print(f"built {WINDOWS_ICON.relative_to(ROOT)}")
    print(f"built {MACOS_ICON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
