#!/usr/bin/env python3
"""Generate fnOS-compatible icon sets with optional rounded-corner masks.

This helper intentionally stays outside fpk.py because icon polishing is often a
release-prep task, not a mandatory package build step.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError as exc:  # pragma: no cover - depends on host environment
    raise SystemExit(
        "Pillow is required for icon_fit.py. Install it with: python3 -m pip install Pillow"
    ) from exc


ROOT_ICON_NAMES = {64: "ICON.PNG", 256: "ICON_256.PNG"}
UI_ICON_NAMES = {
    64: ("icon_64.png", "icon_0.png"),
    256: ("icon_256.png", "icon_0_256.png"),
}
STYLE_RADII = {
    "fnos-rounded-dark": {64: 20, 256: 80},
    "legacy-r20": {64: 20, 256: 80},
    "soft-rounded": {64: 16, 256: 64},
    "fnos-squircle": {64: 0, 256: 0},  # radius unused: profile-driven official curve
    "none": {64: 0, 256: 0},
}

# Left-edge profile of official fnOS desktop app icons (224x224 canvas,
# alpha>128), extracted 2026-09-06 from
# http://<fnos-host>:5666/static/app/icons/trim.file-manager/icon.png?size=256
# and verified byte-identical across trim.app-center, trim.setting,
# trim.docker, trim.download-center, trim.resource-manager,
# trim.file-manager.trash (all official system icons share this curve).
# Index = row y (0..223); value = leftmost opaque x. It covers the
# top-left corner arc (rows 0-57), the straight left edge, and the
# bottom-left corner arc (rows 166-223); the tile is horizontally
# symmetric (verified: right edge == mirror of left on all 224 rows).
#
# IMPORTANT: this is a continuous-curvature ("squircle") corner, NOT a
# circular arc. A plain rounded_rectangle with the equivalent radius
# (~24.8% of canvas, ~55.5px @224) looks visibly rounder — the user
# confirmed when a real package's icon was rejected on exactly that difference
# (2026-09-06). Match the curve, not just the radius.
OFFICIAL_LEFT_PROFILE_224 = [
    57, 46, 41, 37, 34, 32, 30, 28, 26, 25, 23, 22, 21, 20, 18, 17,
    16, 15, 15, 14, 13, 12, 11, 10, 10, 9, 8, 8, 7, 7, 6, 6,
    5, 5, 4, 4, 4, 3, 3, 3, 3, 2, 2, 2, 2, 2, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 5, 5,
    6, 6, 7, 7, 8, 8, 9, 10, 10, 11, 12, 13, 14, 14, 15, 16,
    17, 19, 20, 21, 22, 23, 25, 26, 28, 30, 32, 34, 37, 41, 46, 58,
]


def official_corner_mask(size: int) -> Image.Image:
    """Build the fnOS official squircle corner mask at any pixel size.

    Rebuilds the 224px binary tile mask from OFFICIAL_LEFT_PROFILE_224,
    then LANCZOS-rescales to the requested size for smooth anti-aliasing
    (works for downscale to 64 and upscale to 512+).
    """
    n = len(OFFICIAL_LEFT_PROFILE_224)
    mask = Image.new("L", (n, n), 0)
    px = mask.load()
    for y in range(n):
        x0 = OFFICIAL_LEFT_PROFILE_224[y]
        for x in range(x0, n - x0):
            px[x, y] = 255
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create root and UI icon files for an fnOS FPK project."
    )
    parser.add_argument("--source", required=True, help="source image path")
    parser.add_argument("--out-root", required=True, help="FPK project root to update")
    parser.add_argument(
        "--style",
        choices=sorted(STYLE_RADII),
        default="fnos-squircle",
        help=(
            "corner preset; fnos-squircle (default) replicates the official fnOS "
            "corner curve, user-verified 2026-09-06; fnos-rounded-dark is the "
            "legacy pure-circle r20/r80 style that looked \"rounder\" than official"
        ),
    )
    parser.add_argument("--radius64", type=int, help="override 64x64 corner radius")
    parser.add_argument("--radius256", type=int, help="override 256x256 corner radius")
    parser.add_argument(
        "--no-app-ui",
        action="store_true",
        help="only write root icons and ui/images, skip app/ui/images mirror",
    )
    parser.add_argument(
        "--contact-sheet",
        help="optional path for a preview sheet containing 64 and 256 outputs",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON summary")
    return parser.parse_args()


def contain_square(image: Image.Image, size: int) -> Image.Image:
    image = image.convert("RGBA")
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - image.width) // 2
    y = (size - image.height) // 2
    canvas.alpha_composite(image, (x, y))
    return canvas


def apply_rounded_mask(image: Image.Image, radius: int) -> Image.Image:
    if radius <= 0:
        return image
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    rounded = Image.new("RGBA", image.size, (0, 0, 0, 0))
    rounded.alpha_composite(image)
    rounded.putalpha(Image.composite(rounded.getchannel("A"), Image.new("L", image.size, 0), mask))
    return rounded


def save_icon(image: Image.Image, path: Path) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)
    pixels = image.load()
    width, height = image.size
    corners = [pixels[0, 0][3], pixels[width - 1, 0][3], pixels[0, height - 1][3], pixels[width - 1, height - 1][3]]
    return {"path": str(path), "size": [width, height], "corner_alpha": corners, "bytes": path.stat().st_size}


def make_contact_sheet(images: dict[int, Image.Image], path: Path) -> None:
    padding = 16
    width = 256 + 64 + padding * 3
    height = 256 + padding * 2
    sheet = Image.new("RGBA", (width, height), (245, 245, 245, 255))
    sheet.alpha_composite(images[64], (padding, padding))
    sheet.alpha_composite(images[256], (padding * 2 + 64, padding))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, format="PNG", optimize=True)


def main() -> int:
    args = parse_args()
    source = Path(args.source).expanduser().resolve()
    out_root = Path(args.out_root).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"source image not found: {source}")
    if not out_root.is_dir():
        raise SystemExit(f"FPK project root not found: {out_root}")

    radii = dict(STYLE_RADII[args.style])
    if args.radius64 is not None:
        radii[64] = args.radius64
    if args.radius256 is not None:
        radii[256] = args.radius256

    source_image = Image.open(source)
    outputs: list[dict[str, object]] = []
    rendered: dict[int, Image.Image] = {}
    for size in (64, 256):
        icon = contain_square(source_image.copy(), size)
        if args.style == "fnos-squircle":
            # Profile-driven official curve; the radius params do not apply.
            mask = official_corner_mask(size)
            icon.putalpha(Image.composite(icon.getchannel("A"), Image.new("L", icon.size, 0), mask))
        else:
            icon = apply_rounded_mask(icon, radii[size])
        rendered[size] = icon
        outputs.append(save_icon(icon, out_root / ROOT_ICON_NAMES[size]))
        for ui_base in [out_root / "ui" / "images", out_root / "app" / "ui" / "images"]:
            if args.no_app_ui and "app/ui" in ui_base.as_posix():
                continue
            for name in UI_ICON_NAMES[size]:
                outputs.append(save_icon(icon, ui_base / name))

    if args.contact_sheet:
        make_contact_sheet(rendered, Path(args.contact_sheet).expanduser().resolve())

    summary = {
        "ok": True,
        "source": str(source),
        "out_root": str(out_root),
        "style": args.style,
        "radii": radii,
        "outputs": outputs,
        "contact_sheet": str(Path(args.contact_sheet).expanduser().resolve()) if args.contact_sheet else None,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("Generated fnOS icon set")
        print(f"style={args.style} radii={radii}")
        for item in outputs:
            print(f"- {item['path']} size={item['size']} corner_alpha={item['corner_alpha']}")
        if args.contact_sheet:
            print(f"contact_sheet={summary['contact_sheet']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
