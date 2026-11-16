"""Generate a macOS AppIcon.icns file for RIYA using PIL and iconutil."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from PIL import Image, ImageDraw


def generate_icon(output_icns_path: Path) -> Path:
    """Generate high-resolution squircle icon and compile to .icns format."""
    iconset_dir = output_icns_path.parent / "RIYA.iconset"
    iconset_dir.mkdir(parents=True, exist_ok=True)

    sizes = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]

    base_size = 1024
    img = Image.new("RGBA", (base_size, base_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # macOS squircle rounded rectangle
    # Inset slightly for macOS dock padding
    inset = 80
    corner_radius = 210
    squircle_box = [inset, inset, base_size - inset, base_size - inset]

    # Draw gradient-like background rounded rectangle
    draw.rounded_rectangle(
        squircle_box,
        radius=corner_radius,
        fill=(15, 23, 42, 255),  # Deep slate navy (#0F172A)
        outline=(56, 189, 248, 200),  # Cyan glowing border (#38BDF8)
        width=12,
    )

    # Draw inner AI orbital wave rings
    center_x, center_y = base_size // 2, base_size // 2

    # Glowing outer circle
    draw.ellipse(
        [center_x - 320, center_y - 320, center_x + 320, center_y + 320],
        outline=(59, 130, 246, 120),  # Royal blue
        width=16,
    )

    # Inner orbital ring
    draw.ellipse(
        [center_x - 220, center_y - 220, center_x + 220, center_y + 220],
        outline=(147, 51, 234, 160),  # Purple glow
        width=14,
    )

    # Central glowing node
    draw.ellipse(
        [center_x - 120, center_y - 120, center_x + 120, center_y + 120],
        fill=(37, 99, 235, 255),  # Cobalt blue
        outline=(255, 255, 255, 220),
        width=8,
    )

    # Core white pupil / star
    draw.ellipse(
        [center_x - 45, center_y - 45, center_x + 45, center_y + 45],
        fill=(255, 255, 255, 255),
    )

    # Generate each required size
    for filename, s in sizes:
        resized = img.resize((s, s), Image.Resampling.LANCZOS)
        resized.save(iconset_dir / filename)

    # Compile with iconutil if available
    iconutil = shutil.which("iconutil")
    if iconutil:
        subprocess.run(
            [iconutil, "-c", "icns", str(iconset_dir), "-o", str(output_icns_path)],
            check=True,
        )
        shutil.rmtree(iconset_dir, ignore_errors=True)
    else:
        # Fallback: save 512x512 png as placeholder
        img.resize((512, 512), Image.Resampling.LANCZOS).save(output_icns_path)

    return output_icns_path


if __name__ == "__main__":
    out = Path(__file__).parent / "AppIcon.icns"
    generate_icon(out)
    print(f"Generated icon: {out}")

