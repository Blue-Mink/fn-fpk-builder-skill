from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.fpk_lib.ci_overlay import (
    OverlayTransportError,
    create_metadata,
    restore_overlay,
)
from tests.helpers import elf_header


class CiOverlayTransportTests(unittest.TestCase):
    def test_round_trip_restores_executable_modes_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = root / "prepared"
            binary = prepared / "overlay" / "app" / "bin" / "server"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(elf_header(62))
            binary.chmod(0o755)
            create_metadata(
                prepared / "overlay",
                prepared / "overlay-metadata.json",
                "amd64",
            )

            downloaded = root / "downloaded"
            shutil.copytree(prepared, downloaded)
            for path in (downloaded / "overlay").rglob("*"):
                if path.is_file():
                    path.chmod(0o644)
            destination = root / "restored"
            restore_overlay(downloaded, destination, "amd64")
            restored = destination / "app" / "bin" / "server"
            self.assertEqual(elf_header(62), restored.read_bytes())
            self.assertTrue(restored.stat().st_mode & 0o111)

    def test_restore_rejects_metadata_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "overlay").mkdir()
            (root / "overlay-metadata.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "architecture": "amd64",
                        "files": [
                            {
                                "path": "../escape",
                                "sha256": "0" * 64,
                                "executable": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(OverlayTransportError):
                restore_overlay(root, root / "destination", "amd64")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is unavailable")
    def test_create_rejects_symlink_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            overlay = root / "overlay"
            overlay.mkdir()
            (root / "outside").write_text("outside", encoding="utf-8")
            os.symlink(root / "outside", overlay / "link")
            with self.assertRaises(OverlayTransportError):
                create_metadata(overlay, root / "metadata.json", "amd64")


if __name__ == "__main__":
    unittest.main()
