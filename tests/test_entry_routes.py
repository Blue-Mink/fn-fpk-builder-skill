"""Static checks for desktop-entry routing (gateway / CGI / file types).

A wrong ``gatewayPrefix`` or a socket name containing a path separator is not a
build failure — it becomes a dead desktop icon after install. These cases pin
``_validate_entry_routes`` so the inspect gate keeps catching them.
"""

from __future__ import annotations

import json
import unittest

from scripts.fpk_lib.archive import _validate_entry_routes
from scripts.fpk_lib.report import Report


def run(entry: dict, appname: str = "demo") -> Report:
    raw = json.dumps({".url": {"demo.Application": entry}}).encode()
    report = Report()
    _validate_entry_routes(raw, {"appname": appname}, "app/ui/config", report)
    return report


class EntryRouteTests(unittest.TestCase):
    def test_valid_gateway_entry_is_clean(self) -> None:
        report = run(
            {
                "type": "iframe",
                "gatewaySocket": "app.sock",
                "gatewayPrefix": "/app/demo",
                "url": "/app/demo/",
            }
        )
        self.assertTrue(report.ok, report.errors)
        self.assertEqual([], report.errors)
        self.assertEqual([], report.warnings)

    def test_prefix_without_socket_is_an_error(self) -> None:
        report = run({"type": "iframe", "gatewayPrefix": "/app/demo", "url": "/app/demo/"})
        self.assertFalse(report.ok)
        self.assertTrue(any("without gatewaySocket" in item for item in report.errors), report.errors)

    def test_socket_without_prefix_is_an_error(self) -> None:
        report = run({"type": "iframe", "gatewaySocket": "app.sock"})
        self.assertFalse(report.ok)
        self.assertTrue(any("without gatewayPrefix" in item for item in report.errors), report.errors)

    def test_socket_with_path_separator_is_an_error(self) -> None:
        report = run(
            {"gatewaySocket": "/run/app.sock", "gatewayPrefix": "/app/demo", "url": "/app/demo/"}
        )
        self.assertFalse(report.ok)
        self.assertTrue(any("bare filename" in item for item in report.errors), report.errors)

    def test_prefix_must_start_with_app(self) -> None:
        report = run({"gatewaySocket": "app.sock", "gatewayPrefix": "/demo", "url": "/demo/"})
        self.assertFalse(report.ok)
        self.assertTrue(any("must start with /app/" in item for item in report.errors), report.errors)

    def test_prefix_that_differs_from_appname_only_warns(self) -> None:
        report = run(
            {"gatewaySocket": "app.sock", "gatewayPrefix": "/app/other", "url": "/app/other/"}
        )
        self.assertTrue(report.ok, report.errors)
        self.assertTrue(any("differs from /app/demo" in item for item in report.warnings), report.warnings)

    def test_url_outside_prefix_warns(self) -> None:
        report = run(
            {"gatewaySocket": "app.sock", "gatewayPrefix": "/app/demo", "url": "/elsewhere/"}
        )
        self.assertTrue(report.ok)
        self.assertTrue(any("outside gatewayPrefix" in item for item in report.warnings), report.warnings)

    def test_trailing_slash_prefix_warns(self) -> None:
        report = run(
            {"gatewaySocket": "app.sock", "gatewayPrefix": "/app/demo/", "url": "/app/demo/"}
        )
        self.assertTrue(any("trailing slash" in item for item in report.warnings), report.warnings)

    def test_cgi_entry_pointing_at_another_app_warns(self) -> None:
        report = run({"type": "iframe", "url": "/cgi/ThirdParty/other/index.cgi"})
        self.assertTrue(report.ok)
        self.assertTrue(any("CGI path targets" in item for item in report.warnings), report.warnings)

    def test_cgi_entry_for_same_app_is_clean(self) -> None:
        report = run({"type": "iframe", "url": "/cgi/ThirdParty/demo/index.cgi/"})
        self.assertEqual([], report.errors)
        self.assertEqual([], report.warnings)

    def test_file_types_must_be_a_list(self) -> None:
        report = run({"type": "url", "url": "/app/demo/", "fileTypes": "txt"})
        self.assertFalse(report.ok)
        self.assertTrue(any("fileTypes must be a list" in item for item in report.errors), report.errors)

    def test_unknown_entry_type_warns(self) -> None:
        report = run({"type": "window", "url": "/app/demo/"})
        self.assertTrue(any("unknown type" in item for item in report.warnings), report.warnings)


if __name__ == "__main__":
    unittest.main()
