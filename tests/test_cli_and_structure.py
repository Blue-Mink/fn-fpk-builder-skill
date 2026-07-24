from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.fpk as fpk_cli

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_sources_json_schema(self) -> None:
        process = subprocess.run(
            [sys.executable, "scripts/fpk.py", "sources", "--json"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(
            {"ok", "errors", "warnings", "artifacts", "details"},
            set(payload),
        )

    def test_sources_check_detects_same_length_document_content_drift(self) -> None:
        ledger = json.loads(
            (ROOT / "references" / "provenance.json").read_text(encoding="utf-8")
        )
        ledger = copy.deepcopy(ledger)
        ledger["official_docs"]["sha256"] = hashlib.sha256(b"old").hexdigest()
        ledger["official_docs"]["observed_full_bytes"] = 3
        with patch(
            "scripts.fpk.load_provenance",
            return_value=ledger,
        ), patch(
            "scripts.fpk._fetch_url_snapshot",
            return_value=(b"new", "200"),
        ):
            report = fpk_cli.command_sources(argparse.Namespace(check=True))
        self.assertFalse(report.ok)
        self.assertTrue(
            any("content SHA-256 has drifted" in item for item in report.errors)
        )
        check = report.details["checks"]["official_docs_url"]
        self.assertFalse(check["digest_matches"])
        self.assertTrue(check["size_matches"])

    def test_uninstall_requires_yes_before_ssh(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                "scripts/fnos.py",
                "uninstall",
                "fixture-app",
                "--host",
                "root@example.invalid",
                "--json",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, process.returncode)
        payload = json.loads(process.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("--yes" in item for item in payload["errors"]))

    def test_init_rejects_invalid_appname_before_toolchain_resolution(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                "scripts/fpk.py",
                "init",
                "a",
                "--json",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, process.returncode)
        payload = json.loads(process.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("appname" in item for item in payload["errors"]))

    def test_every_subcommand_exposes_json(self) -> None:
        surfaces = {
            "scripts/fpk.py": ("toolchain", "init", "doctor", "build", "inspect", "sources"),
            "scripts/fnos.py": (
                "doctor",
                "deploy",
                "status",
                "logs",
                "start",
                "stop",
                "uninstall",
                "smoke",
            ),
        }
        for script, commands in surfaces.items():
            for command in commands:
                process = subprocess.run(
                    [sys.executable, script, command, "--help"],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, process.returncode, f"{script} {command}")
                self.assertIn("--json", process.stdout)


class SkillStructureTests(unittest.TestCase):
    def test_skill_frontmatter_and_resources(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        frontmatter = skill.split("---\n", 2)[1]
        keys = {
            line.split(":", 1)[0]
            for line in frontmatter.splitlines()
            if ":" in line
        }
        self.assertEqual({"name", "description"}, keys)
        self.assertLess(len(skill.splitlines()), 500)
        for name in (
            "official-contract.md",
            "build-and-architecture.md",
            "ci-release.md",
            "remote-testing.md",
            "security.md",
            "troubleshooting.md",
        ):
            self.assertIn(f"references/{name}", skill)
            self.assertTrue((ROOT / "references" / name).is_file())

    def test_openai_metadata_mentions_skill(self) -> None:
        metadata = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "fnOS FPK Builder"', metadata)
        self.assertIn("$fn-fpk-builder-skill", metadata)

    def test_requested_readme_is_the_only_auxiliary_document(self) -> None:
        readme = ROOT / "README.md"
        self.assertTrue(readme.is_file())
        content = readme.read_text(encoding="utf-8")
        self.assertIn("# 🧰 fnOS FPK Builder Skill", content)
        self.assertIn("python3 scripts/fpk.py", content)
        self.assertIn("python3 scripts/fnos.py", content)
        for name in ("CHANGELOG.md", "INSTALLATION_GUIDE.md", "QUICK_REFERENCE.md"):
            self.assertFalse((ROOT / name).exists())

    def test_workflow_actions_are_pinned_and_audit_reports_are_uploaded(self) -> None:
        for relative in (
            ".github/workflows/ci.yml",
            "assets/github-actions/fpk.yml",
        ):
            content = (ROOT / relative).read_text(encoding="utf-8")
            for action, reference in re.findall(r"uses:\s*([^@\s]+)@([^\s#]+)", content):
                with self.subTest(workflow=relative, action=action):
                    self.assertRegex(reference, r"^[0-9a-f]{40}$")
        template = (ROOT / "assets/github-actions/fpk.yml").read_text(encoding="utf-8")
        self.assertIn("fpk_lib.ci_overlay", template)
        self.assertIn("dist/fpk/audit/*.json", template)


if __name__ == "__main__":
    unittest.main()
