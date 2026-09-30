"""Offline tests for assets/install-runbook/reinstall.sh.

The lifecycle CLI is replaced with a PATH stub that records every call, so
these tests never touch a real device. They pin the runbook's invariants:
the four-step order, the uninstall postcondition gate, and the wizard/env
preflight.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "assets" / "install-runbook" / "reinstall.sh"

STUB = """#!/bin/bash
echo "$*" >> "$CALLS"
case "$1" in
  check)  exit "${STUB_CHECK_RC:-1}";;
  status) echo "running"; exit 0;;
  *)      echo "[Info] stub ok"; exit 0;;
esac
"""

SUDO_STUB = """#!/bin/bash
exit 1
"""

JOURNAL_STUB = """#!/bin/bash
exit 0
"""


def make_fpk(path: Path, *, wizard: bool) -> None:
    with tarfile.open(path, "w:gz") as archive:
        manifest = b"appname=demo-app\nversion=1.0.0\n"
        info = tarfile.TarInfo("manifest")
        info.size = len(manifest)
        archive.addfile(info, __import__("io").BytesIO(manifest))
        if wizard:
            payload = b"[]\n"
            info = tarfile.TarInfo("wizard/install")
            info.size = len(payload)
            archive.addfile(info, __import__("io").BytesIO(payload))


class ReinstallScriptTests(unittest.TestCase):
    def _run(self, temporary: str, *, fpk: Path, check_rc: str = "1",
             extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        root = Path(temporary)
        stub = root / "bin"
        stub.mkdir(exist_ok=True)
        (stub / "appcenter-cli").write_text(STUB, encoding="utf-8")
        (stub / "sudo").write_text(SUDO_STUB, encoding="utf-8")
        (stub / "journalctl").write_text(JOURNAL_STUB, encoding="utf-8")
        for name in ("appcenter-cli", "sudo", "journalctl"):
            (stub / name).chmod(0o755)
        calls = root / "calls.log"
        env = {
            **os.environ,
            "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
            "APP": "demo-app",
            "FPK": str(fpk),
            "VOLUME": "1",
            "WAIT": "0",
            "CHECK_WAIT": "0",
            "BAK_DIR": str(root),
            "CALLS": str(calls),
            "STUB_CHECK_RC": check_rc,
        }
        env.update(extra_env or {})
        proc = subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
        )
        proc.calls = calls.read_text().splitlines() if calls.exists() else []
        return proc

    def test_script_passes_bash_syntax_check(self) -> None:
        proc = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_happy_path_runs_the_four_steps_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fpk = Path(temporary) / "demo.fpk"
            make_fpk(fpk, wizard=False)
            proc = self._run(temporary, fpk=fpk)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            calls = proc.calls
            indexes = {}
            for position, line in enumerate(calls):
                command = line.split(" ", 1)[0]
                indexes.setdefault(command, []).append(position)
            for command in ("stop", "uninstall", "check", "install-fpk", "start", "status"):
                self.assertIn(command, indexes, calls)
            order = [
                indexes["stop"][0],
                indexes["uninstall"][0],
                indexes["check"][-1],
                indexes["install-fpk"][0],
                indexes["start"][0],
                indexes["status"][0],
            ]
            self.assertEqual(order, sorted(order), calls)
            install = next(line for line in calls if line.startswith("install-fpk"))
            self.assertIn("--volume 1", install)
            self.assertNotIn("--env", install)  # must not appear without ENV_FILE

    def test_aborts_when_uninstall_postcondition_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fpk = Path(temporary) / "demo.fpk"
            make_fpk(fpk, wizard=False)
            proc = self._run(temporary, fpk=fpk, check_rc="0")
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("still installed", proc.stderr)
            self.assertFalse(any(line.startswith("install-fpk") for line in proc.calls), proc.calls)

    def test_wizard_package_without_env_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fpk = Path(temporary) / "demo.fpk"
            make_fpk(fpk, wizard=True)
            proc = self._run(temporary, fpk=fpk)
            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("ENV_FILE", proc.stderr)
            self.assertEqual(proc.calls, [])

    def test_wizard_package_with_env_file_passes_env_path_through(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fpk = Path(temporary) / "demo.fpk"
            make_fpk(fpk, wizard=True)
            env_file = Path(temporary) / "demo.env"
            env_file.write_text("wizard_access_port=8080\n", encoding="utf-8")
            proc = self._run(temporary, fpk=fpk, extra_env={"ENV_FILE": str(env_file)})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            install = next(line for line in proc.calls if line.startswith("install-fpk"))
            self.assertIn(f"--env {env_file}", install)

    def test_missing_env_file_is_refused_before_any_lifecycle_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fpk = Path(temporary) / "demo.fpk"
            make_fpk(fpk, wizard=True)
            proc = self._run(temporary, fpk=fpk, extra_env={"ENV_FILE": str(Path(temporary) / "nope.env")})
            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertEqual(proc.calls, [])

    def test_dry_run_never_calls_the_real_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fpk = Path(temporary) / "demo.fpk"
            make_fpk(fpk, wizard=False)
            proc = self._run(temporary, fpk=fpk, extra_env={"DRY_RUN": "1"})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("[dry-run] appcenter-cli stop demo-app", proc.stdout)
            self.assertFalse(any(line.startswith("install-fpk") for line in proc.calls), proc.calls)


if __name__ == "__main__":
    unittest.main()
