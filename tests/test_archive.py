from __future__ import annotations

import os
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.fpk_lib.archive import (
    build_payload_checksum_manifest,
    inspect_fpk,
    inspect_project,
)
from tests.helpers import create_fpk, create_project, elf_header, file_info, make_png


class ProjectInspectionTests(unittest.TestCase):
    def test_valid_project_and_ds_store_exclusion_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary))
            (project / ".DS_Store").write_bytes(b"host metadata")
            report = inspect_project(project, expected_arch="all")
            self.assertTrue(report.ok, report.errors)
            self.assertTrue(any(".DS_Store" in item for item in report.warnings))

    def test_sensitive_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary))
            (project / "app" / "private.pem").write_text("secret")
            report = inspect_project(project)
            self.assertFalse(report.ok)
            self.assertTrue(any("credential" in item for item in report.errors))

    def test_secret_content_is_detected_without_echoing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary))
            secret = "AKIAABCDEFGHIJKLMNOP"
            (project / "app" / "config.txt").write_text(f"key={secret}\n")
            report = inspect_project(project)
            self.assertFalse(report.ok)
            self.assertTrue(any("AWS access key" in item for item in report.errors))
            self.assertFalse(any(secret in item for item in report.errors))

    def test_secret_content_after_first_chunk_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary))
            secret = "-----BEGIN OPENSSH PRIVATE KEY-----"
            body = "A" * 70
            (project / "app" / "late-secret.txt").write_bytes(
                b"x" * (70 * 1024)
                + secret.encode()
                + b"\n"
                + body.encode()
                + b"\n"
            )
            report = inspect_project(project)
            self.assertFalse(report.ok)
            self.assertTrue(any("private-key material" in item for item in report.errors))
            self.assertFalse(any(secret in item for item in report.errors))

    def test_pem_marker_without_key_body_is_not_flagged(self) -> None:
        # Go binaries merge string constants, so lone PEM headers from x509
        # libraries appear in the string table without any key material.
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary))
            (project / "app" / "binary.bin").write_bytes(
                b"someModel/Name-V2-Chat"
                + b"-----BEGIN PRIVATE KEY-----"
                + b"MsgType_FullClientRequest"
                + b"-----END PRIVATE KEY-----"
                + b"%s/api/v3/chat/completions"
            )
            report = inspect_project(project)
            self.assertTrue(
                all("private-key material" not in item for item in report.errors),
                report.errors,
            )

    def test_icon_dimensions_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary))
            (project / "ICON.PNG").write_bytes(make_png(1, 1))
            report = inspect_project(project)
            self.assertFalse(report.ok)
            self.assertTrue(any("must be 64x64" in item for item in report.errors))

    def test_truncated_png_with_plausible_dimensions_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary))
            (project / "ICON.PNG").write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + struct.pack(">I", 13)
                + b"IHDR"
                + struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0)
            )
            report = inspect_project(project)
            self.assertFalse(report.ok)
            self.assertTrue(any("not a readable PNG" in item for item in report.errors))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "mkfifo is unavailable")
    def test_project_fifo_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary))
            os.mkfifo(project / "app" / "unsafe-fifo")
            report = inspect_project(project)
            self.assertFalse(report.ok)
            self.assertTrue(any("special filesystem node" in item for item in report.errors))

    def test_native_payload_must_match_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary), platform="all", native_machine=62)
            report = inspect_project(project)
            self.assertFalse(report.ok)
            self.assertTrue(any("platform=all" in item for item in report.errors))

    def test_elf_below_app_bin_must_be_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary), platform="x86", native_machine=62)
            (project / "app" / "bin" / "server").chmod(0o644)
            report = inspect_project(project, expected_arch="amd64")
            self.assertFalse(report.ok)
            self.assertTrue(any("not executable" in item for item in report.errors))

    def test_wrong_architecture_lifecycle_elf_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_project(Path(temporary), platform="x86")
            lifecycle = project / "cmd" / "main"
            lifecycle.write_bytes(elf_header(183))
            lifecycle.chmod(0o755)
            report = inspect_project(project, expected_arch="amd64")
            self.assertFalse(report.ok)
            self.assertTrue(any("does not match amd64" in item for item in report.errors))


class FpkInspectionTests(unittest.TestCase):
    def test_valid_arch_neutral_fpk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = create_fpk(Path(temporary) / "good.fpk")
            report = inspect_fpk(path, expected_arch="all")
            self.assertTrue(report.ok, report.errors)
            self.assertEqual([], report.details["detected_elf_architectures"])
            self.assertEqual(64, len(report.artifacts[0]["sha256"]))
            for architecture in ("amd64", "arm64"):
                compatible = inspect_fpk(path, expected_arch=architecture)
                self.assertTrue(compatible.ok, compatible.errors)

    def test_valid_amd64_fpk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = create_fpk(
                Path(temporary) / "amd64.fpk",
                platform="x86",
                binaries=[("bin/server", elf_header(62))],
            )
            report = inspect_fpk(path, expected_arch="amd64")
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(["amd64"], report.details["detected_elf_architectures"])

    def test_wrong_architecture_outer_lifecycle_elf_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = create_fpk(
                Path(temporary) / "outer-lifecycle.fpk",
                platform="x86",
                lifecycle_main=elf_header(183),
            )
            report = inspect_fpk(path, expected_arch="amd64")
            self.assertFalse(report.ok)
            self.assertTrue(any("does not match amd64" in item for item in report.errors))

    def test_payload_checksum_manifest_covers_every_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = create_fpk(
                Path(temporary) / "checksums.fpk",
                platform="x86",
                binaries=[("bin/server", elf_header(62))],
            )
            data, count = build_payload_checksum_manifest(path)
            text = data.decode("utf-8")
            self.assertEqual(2, count)
            self.assertIn("  ./marker.txt\n", text)
            self.assertIn("  ./bin/server\n", text)

    def test_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = create_fpk(
                Path(temporary) / "bad.fpk",
                checksum_override="0" * 32,
            )
            report = inspect_fpk(path)
            self.assertFalse(report.ok)
            self.assertTrue(any("checksum mismatch" in item for item in report.errors))

    def test_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = b"escape"
            path = create_fpk(
                Path(temporary) / "traversal.fpk",
                outer_extra=[(file_info("../escape", data), data)],
            )
            report = inspect_fpk(path)
            self.assertFalse(report.ok)
            self.assertTrue(any("traversal" in item for item in report.errors))

    def test_escaping_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            link = tarfile.TarInfo("safe/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../../outside"
            path = create_fpk(
                Path(temporary) / "link.fpk",
                inner_extra=[(link, None)],
            )
            report = inspect_fpk(path)
            self.assertFalse(report.ok)
            self.assertTrue(any("link" in item and "escapes" in item for item in report.errors))

    def test_escaping_hardlink_uses_archive_root_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            link = tarfile.TarInfo("dir/hard")
            link.type = tarfile.LNKTYPE
            link.linkname = "../outside"
            path = create_fpk(
                Path(temporary) / "hardlink.fpk",
                inner_extra=[(link, None)],
            )
            report = inspect_fpk(path)
            self.assertFalse(report.ok)
            self.assertTrue(
                any("hardlink" in item and "traversal" in item for item in report.errors)
            )

    def test_special_filesystem_node_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fifo = tarfile.TarInfo("unsafe-fifo")
            fifo.type = tarfile.FIFOTYPE
            path = create_fpk(
                Path(temporary) / "fifo.fpk",
                outer_extra=[(fifo, None)],
            )
            report = inspect_fpk(path)
            self.assertFalse(report.ok)
            self.assertTrue(any("special filesystem node" in item for item in report.errors))

    def test_macho_and_mixed_elf_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = create_fpk(
                Path(temporary) / "mixed.fpk",
                platform="x86",
                binaries=[
                    ("bin/a", elf_header(62)),
                    ("bin/b", elf_header(183)),
                    ("bin/mac", b"\xcf\xfa\xed\xfe" + b"\0" * 60),
                ],
            )
            report = inspect_fpk(path)
            self.assertFalse(report.ok)
            self.assertTrue(any("mixes ELF" in item for item in report.errors))
            self.assertTrue(any("mach-o" in item for item in report.errors))

    def test_elf_class_and_endianness_must_match_fnos_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            invalid = bytearray(64)
            invalid[:4] = b"\x7fELF"
            invalid[4] = 1  # ELF32 cannot represent an x86_64 fnOS executable.
            invalid[5] = 1
            invalid[6] = 1
            struct.pack_into("<H", invalid, 18, 62)
            path = create_fpk(
                Path(temporary) / "invalid-elf.fpk",
                platform="x86",
                binaries=[("bin/server", bytes(invalid))],
            )
            report = inspect_fpk(path, expected_arch="amd64")
            self.assertFalse(report.ok)
            self.assertTrue(any("unsupported ELF" in item for item in report.errors))

    def test_ds_store_in_final_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = b"metadata"
            path = create_fpk(
                Path(temporary) / "metadata.fpk",
                inner_extra=[(file_info(".DS_Store", data), data)],
            )
            report = inspect_fpk(path)
            self.assertFalse(report.ok)
            self.assertTrue(any(".DS_Store" in item for item in report.errors))


    def test_python_bytecode_in_payload_warns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stale = b"stale cached bytecode\n"
            polluted = create_fpk(
                Path(temporary) / "bytecode.fpk",
                inner_extra=[
                    (file_info("bin/__pycache__/entry.cpython-312.pyc", stale), stale)
                ],
            )
            report = inspect_fpk(polluted)
            self.assertTrue(any("Python bytecode" in item for item in report.warnings))
            clean_report = inspect_fpk(create_fpk(Path(temporary) / "clean.fpk"))
            self.assertFalse(any("Python bytecode" in item for item in clean_report.warnings))


if __name__ == "__main__":
    unittest.main()
