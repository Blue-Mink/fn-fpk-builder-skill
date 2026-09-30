"""Desktop-entry icon resolution and ``desktop_applaunchname`` consistency.

Both checks mirror platform behaviour measured on a device rather than inferred
from the docs: the icon slot the operating system actually fetches is the
``{0}`` fallback (``<prefix>_0.png``), and fnpack aborts the build when the
manifest's launch name has no matching ``.url`` entry.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.fpk_lib.archive import inspect_fpk, inspect_project
from tests.helpers import PNG_64, PNG_256, create_fpk, create_project, file_info

TEMPLATE_CONFIG = {
    ".url": {
        "fixture-app.Application": {
            "title": "Fixture",
            "icon": "images/icon_{0}.png",
            "type": "url",
            "protocol": 1,
            "port": 8080,
            "url": "/",
            "noDisplay": False,
        }
    }
}


def write_ui(project: Path, config: dict, icons: dict[str, bytes]) -> None:
    images = project / "app" / "ui" / "images"
    images.mkdir(parents=True, exist_ok=True)
    (project / "app" / "ui" / "config").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )
    for name, payload in icons.items():
        (images / name).write_bytes(payload)


def with_manifest_line(project: Path, line: str) -> None:
    manifest = project / "manifest"
    manifest.write_text(manifest.read_text(encoding="utf-8").rstrip("\n") + f"\n{line}\n", encoding="utf-8")


class EntryIconTests(unittest.TestCase):
    def test_placeholder_without_fallback_slot_is_an_error(self) -> None:
        """The 64/256 pair is not enough; the OS fetches ``icon_0.png``."""

        with tempfile.TemporaryDirectory() as raw:
            project = create_project(Path(raw))
            write_ui(
                project,
                TEMPLATE_CONFIG,
                {"icon_64.png": PNG_64, "icon_256.png": PNG_256},
            )
            report = inspect_project(project, expected_arch="all")
            self.assertTrue(
                any("icon_0.png" in item for item in report.errors),
                report.errors,
            )
            self.assertFalse(
                any("official 64 variant" in item for item in report.warnings),
                report.warnings,
            )

    def test_fallback_slot_and_variants_present_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = create_project(Path(raw))
            write_ui(
                project,
                TEMPLATE_CONFIG,
                {
                    "icon_0.png": PNG_256,
                    "icon_64.png": PNG_64,
                    "icon_256.png": PNG_256,
                },
            )
            report = inspect_project(project, expected_arch="all")
            self.assertTrue(report.ok, report.errors)
            entry = report.details["entry_icons"]["fixture-app.Application"]
            self.assertEqual(entry["requested_slot"], "images/icon_0.png")
            self.assertTrue(entry["present"])
            self.assertTrue(all(entry["official_variants"].values()))

    def test_literal_icon_path_must_exist(self) -> None:
        config = {".url": {"fixture-app.Application": {"icon": "images/ICON.PNG"}}}
        with tempfile.TemporaryDirectory() as raw:
            project = create_project(Path(raw))
            write_ui(project, config, {})
            report = inspect_project(project, expected_arch="all")
            self.assertTrue(any("ICON.PNG" in item for item in report.errors), report.errors)

    def test_icon_path_cannot_escape_the_ui_directory(self) -> None:
        config = {".url": {"fixture-app.Application": {"icon": "../../etc/passwd"}}}
        with tempfile.TemporaryDirectory() as raw:
            project = create_project(Path(raw))
            write_ui(project, config, {})
            report = inspect_project(project, expected_arch="all")
            self.assertTrue(
                any("escapes the ui directory" in item for item in report.errors),
                report.errors,
            )

    def test_unknown_desktop_applaunchname_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = create_project(Path(raw))
            write_ui(
                project,
                TEMPLATE_CONFIG,
                {"icon_0.png": PNG_256, "icon_64.png": PNG_64, "icon_256.png": PNG_256},
            )
            with_manifest_line(project, "desktop_applaunchname=fixture-app.Gone")
            report = inspect_project(project, expected_arch="all")
            self.assertTrue(
                any("desktop_applaunchname" in item for item in report.errors),
                report.errors,
            )

    def test_matching_desktop_applaunchname_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = create_project(Path(raw))
            write_ui(
                project,
                TEMPLATE_CONFIG,
                {"icon_0.png": PNG_256, "icon_64.png": PNG_64, "icon_256.png": PNG_256},
            )
            with_manifest_line(project, "desktop_applaunchname=fixture-app.Application")
            report = inspect_project(project, expected_arch="all")
            self.assertTrue(report.ok, report.errors)

    def test_final_package_is_checked_the_same_way(self) -> None:
        """A payload-only regression must not pass because the tree looked fine."""

        config = json.dumps(TEMPLATE_CONFIG).encode()
        with tempfile.TemporaryDirectory() as raw:
            fpk = create_fpk(
                Path(raw) / "fixture.fpk",
                inner_extra=[
                    (file_info("ui/config", config), config),
                    (file_info("ui/images/icon_64.png", PNG_64), PNG_64),
                    (file_info("ui/images/icon_256.png", PNG_256), PNG_256),
                ],
            )
            report = inspect_fpk(fpk, expected_arch="all")
            self.assertTrue(
                any("icon_0.png" in item for item in report.errors),
                report.errors,
            )



class EntryPortTests(unittest.TestCase):
    """`service_port` and `checkport` must agree with the declared entry ports."""

    ICONS = {"icon_0.png": PNG_256, "icon_64.png": PNG_64, "icon_256.png": PNG_256}

    def _report(self, root: str, *manifest_lines: str):
        project = create_project(Path(root))
        write_ui(project, TEMPLATE_CONFIG, self.ICONS)
        for line in manifest_lines:
            with_manifest_line(project, line)
        return inspect_project(project, expected_arch="all")

    def test_uncovered_service_port_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = self._report(raw, "service_port=9999")
            self.assertTrue(
                any("service_port 9999" in item for item in report.warnings),
                report.warnings,
            )

    def test_matching_service_port_is_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = self._report(raw, "service_port=8080")
            self.assertFalse(
                any("service_port" in item for item in report.warnings),
                report.warnings,
            )

    def test_checkport_with_resident_entry_port_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = self._report(raw, "service_port=8080", "checkport=true")
            self.assertTrue(
                any("11000" in item for item in report.warnings), report.warnings
            )

    def test_checkport_false_is_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = self._report(raw, "service_port=8080", "checkport=false")
            self.assertFalse(
                any("checkport" in item for item in report.warnings), report.warnings
            )


if __name__ == "__main__":
    unittest.main()
