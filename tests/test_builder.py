from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scripts.fpk_lib.archive import inspect_fpk
from scripts.fpk_lib.builder import BuildOptions, build_packages
from scripts.fpk_lib.toolchain import FnpackInfo
from tests.helpers import create_fake_fnpack, create_project, elf_header


class BuilderTests(unittest.TestCase):
    def test_isolated_dual_arch_build_does_not_mutate_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_project(root, platform="all")
            original_manifest = (project / "manifest").read_bytes()
            (project / ".DS_Store").write_bytes(b"must not ship")

            amd64 = root / "overlay-amd64" / "app" / "bin"
            arm64 = root / "overlay-arm64" / "app" / "bin"
            amd64.mkdir(parents=True)
            arm64.mkdir(parents=True)
            (amd64 / "server").write_bytes(elf_header(62))
            (arm64 / "server").write_bytes(elf_header(183))
            (amd64 / "server").chmod(0o755)
            (arm64 / "server").chmod(0o755)

            fake = create_fake_fnpack(root / "fnpack")
            info = FnpackInfo(
                path=fake,
                version="1.2.3",
                sha256="fake",
                verified=False,
                host="test",
            )
            output = root / "dist"
            report = build_packages(
                BuildOptions(
                    project=project,
                    output=output,
                    architectures=("amd64", "arm64"),
                    fnpack=info,
                    overlay_amd64=root / "overlay-amd64",
                    overlay_arm64=root / "overlay-arm64",
                )
            )
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(original_manifest, (project / "manifest").read_bytes())
            fpks = sorted(output.glob("*.fpk"))
            self.assertEqual(2, len(fpks))
            self.assertTrue(any(path.name.endswith("-amd64.fpk") for path in fpks))
            self.assertTrue(any(path.name.endswith("-arm64.fpk") for path in fpks))
            for path in fpks:
                expected = "amd64" if path.name.endswith("-amd64.fpk") else "arm64"
                inspected = inspect_fpk(path, expected_arch=expected)
                self.assertTrue(inspected.ok, inspected.errors)
                self.assertTrue(path.with_name(f"{path.name}.sha256").is_file())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "mkfifo is unavailable")
    def test_overlay_special_node_is_rejected_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_project(root, platform="all")
            overlay = root / "overlay-amd64" / "app"
            overlay.mkdir(parents=True)
            os.mkfifo(overlay / "unsafe-fifo")
            fake = create_fake_fnpack(root / "fnpack")
            report = build_packages(
                BuildOptions(
                    project=project,
                    output=root / "dist",
                    architectures=("amd64",),
                    fnpack=FnpackInfo(
                        path=fake,
                        version="1.2.3",
                        sha256="fake",
                        verified=False,
                        host="test",
                    ),
                    overlay_amd64=root / "overlay-amd64",
                )
            )
            self.assertFalse(report.ok)
            self.assertTrue(any("special filesystem node" in item for item in report.errors))
            self.assertFalse((root / "dist").exists())


if __name__ == "__main__":
    unittest.main()
