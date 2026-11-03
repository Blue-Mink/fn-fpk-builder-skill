"""Self-test for the sanitization gate: clean tree passes, seeded leak fails."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.sanitize import ROOT, scan


class SanitizeTests(unittest.TestCase):
    def test_current_tree_is_clean(self) -> None:
        self.assertEqual([], scan(ROOT))

    def test_internal_address_is_detected(self) -> None:
        # Synthetic RFC1918 fixture for the detector self-test. Never replace
        # it with a real device address (repo is public).
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "notes.md"
            path.write_text("device: http://192.168.254.254:8080\n", encoding="utf-8")
            errors = scan(Path(td))
            self.assertTrue(any("内网 IPv4" in e for e in errors), errors)

    def test_token_shape_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.sh"
            path.write_text("export GH=ghp_" + "a" * 30 + "\n", encoding="utf-8")
            errors = scan(Path(td))
            self.assertTrue(any("GitHub token" in e for e in errors), errors)

    def test_placeholders_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.md"
            path.write_text("curl http://192.0.2.10/app  # example.com\n", encoding="utf-8")
            self.assertEqual([], scan(Path(td)))


if __name__ == "__main__":
    unittest.main()
