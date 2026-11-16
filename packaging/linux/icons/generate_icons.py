"""Multi-resolution Freedesktop Linux application icon generator for RIYA.

Generates standard hicolor icon resolutions (16x16 to 512x512) for Debian
packages, AppImage bundles, and desktop launchers using Pillow.
"""

from __future__ import annotations

import os
from pathlib import Path
from PIL import Image, ImageDraw


STANDARD_SIZES = (16, 32, 48, 64, 128, 256, 512)


def generate_master_icon(size: int = 1024) -> Image.Image:
    """Generate high-resolution master application icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded squircle container
    inset = int(size * 0.08)
    corner_radius = int(size * 0.22)
    squircle_box = [inset, inset, size - inset, size - inset]

    # Deep slate navy background (#0F172A)
    draw.rounded_rectangle(
        squircle_box,
        radius=corner_radius,
        fill=(15, 23, 42, 255),
        outline=(56, 189, 248, 220),  # Cyan glow (#38BDF8)
        width=max(2, int(size * 0.015)),
    )

    center_x, center_y = size // 2, size // 2

    # Outer orbital ring (Royal blue)
    r_outer = int(size * 0.30)
    draw.ellipse(
        [center_x - r_outer, center_y - r_outer, center_x + r_outer, center_y + r_outer],
        outline=(59, 130, 246, 140),
        width=max(2, int(size * 0.016)),
    )

    # Middle orbital ring (Purple glow)
    r_mid = int(size * 0.20)
    draw.ellipse(
        [center_x - r_mid, center_y - r_mid, center_x + r_mid, center_y + r_mid],
        outline=(147, 51, 234, 180),
        width=max(2, int(size * 0.014)),
    )

    # Core AI intelligence node (Cobalt blue with cyan border)
    r_core = int(size * 0.11)
    draw.ellipse(
        [center_x - r_core, center_y - r_core, center_x + r_core, center_y + r_core],
        fill=(37, 99, 235, 255),
        outline=(255, 255, 255, 230),
        width=max(2, int(size * 0.010)),
    )

    # Inner bright pinpoint node
    r_pin = int(size * 0.04)
    draw.ellipse(
        [center_x - r_pin, center_y - r_pin, center_x + r_pin, center_y + r_pin],
        fill=(56, 189, 248, 255),
    )

    return img


def generate_all_icons(output_dir: Path | str | None = None) -> dict[int, Path]:
    """Render and save PNG icons for all standard Linux hicolor resolutions."""
    target_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parent
    target_dir.mkdir(parents=True, exist_ok=True)

    master = generate_master_icon(1024)
    generated: dict[int, Path] = {}

    for s in STANDARD_SIZES:
        resized = master.resize((s, s), Image.Resampling.LANCZOS)
        out_path = target_dir / f"riya_{s}x{s}.png"
        resized.save(out_path, format="PNG", optimize=True)
        generated[s] = out_path

    # Save default 512x512 riya.png
    default_icon = target_dir / "riya.png"
    master.resize((512, 512), Image.Resampling.LANCZOS).save(default_icon, format="PNG", optimize=True)
    generated[512] = default_icon

    return generated


if __name__ == "__main__":
    icons = generate_all_icons()
    print(f"Generated {len(icons)} Linux application icons in {Path(__file__).parent}")

