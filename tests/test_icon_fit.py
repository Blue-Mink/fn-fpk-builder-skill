"""Geometry and CLI invariants for ``scripts/icon_fit.py``.

These guards exist because two real defects hid in plain sight: a rasterised
official mask scaled up from the 224px profile produced a visible 3px step on
the left edge, and a source image with its own margins looked like an inset bug
in the script. Both are cheap to assert numerically, so they are asserted here
instead of eyeballed at review time.
"""

from __future__ import annotations

import importlib.util
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    from PIL import Image
except ImportError:  # pragma: no cover - depends on host environment
    Image = None


def load_icon_fit():
    spec = importlib.util.spec_from_file_location("icon_fit_under_test", ROOT / "scripts" / "icon_fit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def left_boundary(mask: "Image.Image") -> list[int]:
    """First opaque x per row (canvas height rows)."""

    pixels = mask.load()
    width, height = mask.size
    edges: list[int] = []
    for y in range(height):
        edge = width
        for x in range(width):
            if pixels[x, y] > 128:
                edge = x
                break
        edges.append(edge)
    return edges


@unittest.skipIf(Image is None, "Pillow is not installed")
class IconFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.icon_fit = load_icon_fit()

    def test_default_style_is_the_official_squircle(self) -> None:
        """The plain circular arc was rejected by the user as "too round"."""

        argv = sys.argv
        sys.argv = ["icon_fit.py", "--source", "x.png", "--out-root", "out"]
        try:
            args = self.icon_fit.parse_args()
        finally:
            sys.argv = argv
        self.assertEqual(args.style, "fnos-squircle")

    def test_official_mask_corners_are_transparent(self) -> None:
        mask = self.icon_fit.official_corner_mask(256)
        self.assertEqual(mask.mode, "L")
        pixels = mask.load()
        for corner in ((0, 0), (255, 0), (0, 255), (255, 255)):
            x, y = corner
            block = [
                pixels[x + dx if x == 0 else x - dx, y + dy if y == 0 else y - dy]
                for dx in range(12)
                for dy in range(12)
            ]
            with self.subTest(corner=corner):
                self.assertLess(sum(block) / len(block), 1.0)

    def test_official_mask_is_full_bleed_on_straight_edges(self) -> None:
        """Official icons touch the canvas edge: a margin means inset artwork."""

        edges = left_boundary(self.icon_fit.official_corner_mask(256))
        middle = len(edges) // 2
        self.assertEqual(edges[middle], 0)

    def test_official_mask_left_edge_has_no_quantisation_step(self) -> None:
        """Rows approaching the straight side must move at most 1px each.

        The pre-rendered 512 asset, produced by thresholding the 224px profile
        after upscaling, walked ``8, 5, 3, 3, 3, 0`` and reads as a nick in an
        otherwise straight edge. A mask rebuilt by interpolating the profile
        steps by one pixel per row.
        """

        edges = left_boundary(self.icon_fit.official_corner_mask(512))
        half = len(edges) // 2
        approach = [edges[y] for y in range(half) if edges[y] <= 8]
        self.assertGreater(len(approach), 50, "接直边采样行太少，用例失效")
        steps = [abs(a - b) for a, b in zip(approach, approach[1:])]
        self.assertLessEqual(max(steps), 1, f"左缘出现 {max(steps)}px 台阶：{approach[:16]}")

    def test_full_bleed_square_produces_expected_slots_without_inset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "art.png"
            Image.new("RGBA", (300, 300), (20, 30, 60, 255)).save(source)
            out_root = root / "pkg"
            (out_root / "ui" / "images").mkdir(parents=True)

            argv = sys.argv
            sys.argv = [
                "icon_fit.py",
                "--source",
                str(source),
                "--out-root",
                str(out_root),
                "--style",
                "fnos-squircle",
            ]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = self.icon_fit.main()
            finally:
                sys.argv = argv
            self.assertEqual(rc, 0)

            for name, size in (
                ("ICON.PNG", 64),
                ("ICON_256.PNG", 256),
                ("ui/images/icon_64.png", 64),
                ("ui/images/icon_0.png", 64),
                ("ui/images/icon_256.png", 256),
                ("ui/images/icon_0_256.png", 256),
            ):
                path = out_root / name
                with self.subTest(slot=name):
                    self.assertTrue(path.is_file(), "槽位未写出")
                    with Image.open(path) as handle:
                        self.assertEqual(handle.size, (size, size))

            with Image.open(out_root / "ICON_256.PNG") as handle:
                composited = handle.convert("RGBA")
            alpha = composited.getchannel("A")
            self.assertEqual(alpha.getpixel((0, 128)), 255, "直边内缩：源图未铺满")
            self.assertEqual(alpha.getpixel((0, 0)), 0, "圆角未切")

    def test_margins_in_the_source_show_up_as_an_inset_tile(self) -> None:
        """Documents the trap the script is *not* responsible for."""

        source = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        box = Image.new("RGBA", (160, 160), (20, 30, 60, 255))
        source.paste(box, (20, 20))
        fitted = self.icon_fit.contain_square(source, 256)
        self.assertEqual(fitted.size, (256, 256))
        self.assertEqual(fitted.getchannel("A").getpixel((0, 128)), 0, "源图自带边距时本应内缩")


if __name__ == "__main__":
    unittest.main()
