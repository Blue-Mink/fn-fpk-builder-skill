from __future__ import annotations

import unittest

from scripts.fpk_lib.manifest import (
    parse_manifest_text,
    update_manifest_text,
    validate_manifest,
)


class ManifestTests(unittest.TestCase):
    def test_parses_quotes_spaces_and_multiline(self) -> None:
        document = parse_manifest_text(
            """
appname = "demo-app"
version='1.2.3'
display_name=Demo
desc=\"\"\"first line
second=line\"\"\"
maintainer=Tests
source=thirdparty
platform = all
"""
        )
        self.assertEqual([], document.errors)
        self.assertEqual("demo-app", document.values["appname"])
        self.assertEqual("first line\nsecond=line", document.values["desc"])
        self.assertEqual(([], []), validate_manifest(document))

    def test_reports_duplicate_and_unterminated_value(self) -> None:
        document = parse_manifest_text(
            'appname=one\nappname=two\ndesc="""unterminated\n'
        )
        errors, _ = validate_manifest(document)
        self.assertTrue(any("duplicate" in item for item in errors))
        self.assertTrue(any("unterminated" in item for item in errors))

    def test_update_changes_only_requested_fields(self) -> None:
        original = (
            "# retained\n"
            "appname=demo-app\n"
            "version=1.0.0\n"
            'desc="""line one\nline two"""\n'
            "platform=all\n"
        )
        updated = update_manifest_text(
            original, {"version": "2.0.0-beta", "platform": "arm"}
        )
        self.assertIn("# retained\n", updated)
        self.assertIn('desc="""line one\nline two"""\n', updated)
        self.assertIn("version=2.0.0-beta\n", updated)
        self.assertIn("platform=arm\n", updated)

    def test_validation_rejects_invalid_platform_and_appname(self) -> None:
        document = parse_manifest_text(
            """
appname=bad app
version=1.0.0
display_name=Bad
desc=Bad
maintainer=Tests
platform=mips
"""
        )
        errors, warnings = validate_manifest(document)
        self.assertTrue(any("appname" in item for item in errors))
        self.assertTrue(any("platform" in item for item in errors))
        self.assertTrue(any("source" in item for item in warnings))

    def test_observed_appcenter_name_length_constraint(self) -> None:
        for appname, valid in (("abc", True), ("x" * 32, True), ("ab", False), ("x" * 33, False)):
            with self.subTest(appname=appname):
                document = parse_manifest_text(
                    "\n".join(
                        [
                            f"appname={appname}",
                            "version=1.0.0",
                            "display_name=Fixture",
                            "desc=Fixture",
                            "maintainer=Tests",
                            "source=thirdparty",
                            "platform=all",
                        ]
                    )
                )
                errors, _ = validate_manifest(document)
                self.assertEqual(valid, not any("appname" in item for item in errors))

    def test_rejects_invalid_compatibility_dependency_port_and_booleans(self) -> None:
        document = parse_manifest_text(
            """
appname=fixture-app
version=1.0.0
display_name=Fixture
desc=Fixture
maintainer=Tests
source=thirdparty
platform=all
os_min_version=not-a-version
os_max_version=0
install_dep_apps=database>>broken::
service_port=70000
ctl_stop=maybe
checkport=not-bool
install_type=elsewhere
"""
        )
        errors, _ = validate_manifest(document)
        for field in (
            "os_min_version",
            "os_max_version",
            "install_dep_apps",
            "service_port",
            "ctl_stop",
            "checkport",
            "install_type",
        ):
            with self.subTest(field=field):
                self.assertTrue(any(field in item for item in errors), errors)

    def test_rejects_inverted_os_version_range_and_unsafe_ui_directory(self) -> None:
        document = parse_manifest_text(
            """
appname=fixture-app
version=1.0.0
display_name=Fixture
desc=Fixture
maintainer=Tests
source=thirdparty
platform=all
os_min_version=2.0.0
os_max_version=1.9.9
desktop_uidir=../../outside
"""
        )
        errors, _ = validate_manifest(document)
        self.assertTrue(any("os_min_version" in item and "os_max_version" in item for item in errors))
        self.assertTrue(any("desktop_uidir" in item for item in errors))


    def test_two_segment_version_warns_for_newer_fnpack(self) -> None:
        base = "appname=demo-app\ndisplay_name=Demo\ndesc=d\nmaintainer=Tests\nsource=thirdparty\nplatform=all\n"
        two = parse_manifest_text(base.replace("platform=all", "version=1.0\nplatform=all"))
        errors, warnings = validate_manifest(two)
        self.assertEqual([], errors)
        self.assertTrue(any("three numeric segments" in item for item in warnings))

        three = parse_manifest_text(base.replace("platform=all", "version=1.0.1\nplatform=all"))
        errors, warnings = validate_manifest(three)
        self.assertEqual([], errors)
        self.assertFalse(any("three numeric segments" in item for item in warnings))


if __name__ == "__main__":
    unittest.main()
