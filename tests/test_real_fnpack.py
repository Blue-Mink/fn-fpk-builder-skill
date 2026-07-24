from __future__ import annotations

import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.fpk_lib.archive import inspect_fpk
from scripts.fpk_lib.builder import BuildOptions, build_packages
from scripts.fpk_lib.toolchain import inspect_fnpack
from tests.helpers import create_project, elf_header

ROOT = Path(__file__).resolve().parents[1]


class RealFnpackIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        value = os.environ.get("FNPACK_BIN")
        if not value or not Path(value).expanduser().is_file():
            raise unittest.SkipTest("FNPACK_BIN is not set to a real fnpack binary")
        cls.info = inspect_fnpack(value)
        if cls.info.version != "1.2.3":
            raise unittest.SkipTest("real integration requires fnpack 1.2.3")

    def test_official_native_and_docker_templates_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for template in ("native", "docker"):
                appname = f"integration-{template}"
                process = subprocess.run(
                    [
                        str(self.info.path),
                        "create",
                        appname,
                        "--template",
                        template,
                        "--without-ui=true",
                    ],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    0,
                    process.returncode,
                    f"{template}: {process.stdout}\n{process.stderr}",
                )
                project = root / appname
                # Official templates deliberately contain placeholders. Turn
                # them into a minimal installable fixture before integration.
                for script in (project / "cmd").iterdir():
                    if script.is_file():
                        script.chmod(0o755)
                privilege_path = project / "config" / "privilege"
                privilege = json.loads(privilege_path.read_text(encoding="utf-8"))
                if privilege.get("defaults", {}).get("run-as") == "package":
                    privilege["username"] = f"int{template}"
                    privilege["groupname"] = f"int{template}"
                    privilege_path.write_text(
                        json.dumps(privilege, ensure_ascii=False),
                        encoding="utf-8",
                    )
                output = root / f"dist-{template}"
                report = build_packages(
                    BuildOptions(
                        project=project,
                        output=output,
                        architectures=("all",),
                        fnpack=self.info,
                    )
                )
                self.assertTrue(report.ok, f"{template}: {report.errors}")
                fpks = list(output.glob("*.fpk"))
                self.assertEqual(1, len(fpks))
                inspected = inspect_fpk(fpks[0], expected_arch="all")
                self.assertTrue(inspected.ok, inspected.errors)

    def test_cli_init_normalizes_generated_host_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            process = subprocess.run(
                [
                    os.environ.get("PYTHON", "python3"),
                    str(ROOT / "scripts" / "fpk.py"),
                    "init",
                    "integration-init",
                    "--path",
                    temporary,
                    "--without-ui",
                    "--fnpack",
                    str(self.info.path),
                    "--json",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            project = Path(temporary) / "integration-init"
            self.assertTrue(project.is_dir())
            self.assertEqual([], list(project.rglob(".DS_Store")))
            self.assertTrue(all(path.stat().st_mode & 0o111 for path in (project / "cmd").iterdir()))

    def test_real_fnpack_builds_independently_audited_dual_arch_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_project(root, appname="integration-dual", platform="all")
            source_manifest = (project / "manifest").read_bytes()
            for architecture, machine in (("amd64", 62), ("arm64", 183)):
                binary = root / f"overlay-{architecture}" / "app" / "bin" / "server"
                binary.parent.mkdir(parents=True)
                binary.write_bytes(elf_header(machine))
                binary.chmod(0o755)
            output = root / "dual-dist"
            report = build_packages(
                BuildOptions(
                    project=project,
                    output=output,
                    architectures=("amd64", "arm64"),
                    fnpack=self.info,
                    overlay_amd64=root / "overlay-amd64",
                    overlay_arm64=root / "overlay-arm64",
                )
            )
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(source_manifest, (project / "manifest").read_bytes())
            for architecture in ("amd64", "arm64"):
                paths = list(output.glob(f"*-fnos-{architecture}.fpk"))
                self.assertEqual(1, len(paths), architecture)
                inspected = inspect_fpk(paths[0], expected_arch=architecture)
                self.assertTrue(inspected.ok, inspected.errors)
                self.assertEqual(
                    [architecture],
                    inspected.details["detected_elf_architectures"],
                )
                self.assertTrue(
                    paths[0].with_name(f"{paths[0].name}.sha256").is_file()
                )

    def test_real_fnpack_rejects_wrong_architecture_lifecycle_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_project(
                root,
                appname="integration-bad-lifecycle",
                platform="x86",
            )
            lifecycle = project / "cmd" / "main"
            lifecycle.write_bytes(elf_header(183))
            lifecycle.chmod(0o755)
            report = build_packages(
                BuildOptions(
                    project=project,
                    output=root / "dist",
                    architectures=("amd64",),
                    fnpack=self.info,
                )
            )
            self.assertFalse(report.ok)
            self.assertEqual([], list((root / "dist").glob("*.fpk")))
            self.assertTrue(any("does not match amd64" in item for item in report.errors))


if __name__ == "__main__":
    unittest.main()
