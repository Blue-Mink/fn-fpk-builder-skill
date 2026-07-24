from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from scripts.fpk_lib.remote import (
    SSHConfig,
    RemoteResult,
    _appcenter_has_error,
    _resolve_install_volume,
    _redact_log_text,
    _safe_remote_cleanup,
    _status_value,
    _verify_installed_payload,
    app_logs,
    application_installed,
    deploy,
    select_artifact,
    uninstall,
    validate_appname,
)
from scripts.fpk_lib.report import Report, UsageError
from scripts.fpk_lib.archive import build_payload_checksum_manifest, sha256_file
from tests.helpers import create_fpk, elf_header


class RemoteSafetyTests(unittest.TestCase):
    def test_ssh_config_rejects_option_and_whitespace_injection(self) -> None:
        for host in ("-oProxyCommand=bad", "root@host name", "root@host\ncommand"):
            with self.subTest(host=host):
                with self.assertRaises(UsageError):
                    SSHConfig(host)

    def test_ssh_config_keeps_host_key_verification_enabled(self) -> None:
        args = SSHConfig("root@example.invalid").ssh_base()
        rendered = " ".join(args)
        self.assertNotIn("StrictHostKeyChecking=no", rendered)
        self.assertNotIn("UserKnownHostsFile=/dev/null", rendered)
        self.assertIn("BatchMode=yes", rendered)

    def test_appname_validation(self) -> None:
        validate_appname("safe-app_1")
        for appname in ("a", "../bad", "bad app", "-option", "x" * 33):
            with self.subTest(appname=appname):
                with self.assertRaises(UsageError):
                    validate_appname(appname)

    def test_appcenter_semantic_errors_override_zero_exit_code(self) -> None:
        error = RemoteResult("install", 0, "[Error]Something wrong: code 10111", "")
        healthy = RemoteResult("status", 0, "running\n", "")
        missing = RemoteResult("status", 0, "noinstall\n", "")
        self.assertTrue(_appcenter_has_error(error))
        self.assertFalse(_appcenter_has_error(healthy))
        self.assertEqual("running", _status_value(healthy))
        self.assertEqual("noinstall", _status_value(missing))

    def test_volume_resolution_uses_only_unambiguous_existing_volume(self) -> None:
        config = SSHConfig("root@example.invalid")
        default = RemoteResult("default-volume", 0, "0\n", "")
        discovered = RemoteResult("find volumes", 0, "1\n", "")
        with patch(
            "scripts.fpk_lib.remote.remote_run", return_value=default
        ), patch(
            "scripts.fpk_lib.remote.remote_shell", return_value=discovered
        ):
            self.assertEqual(
                ("1", "single-existing-appcenter-volume"),
                _resolve_install_volume(config, None),
            )

        multiple = RemoteResult("find volumes", 0, "1\n2\n", "")
        with patch(
            "scripts.fpk_lib.remote.remote_run", return_value=default
        ), patch(
            "scripts.fpk_lib.remote.remote_shell", return_value=multiple
        ):
            with self.assertRaises(UsageError):
                _resolve_install_volume(config, None)

    def test_artifact_selection_prefers_exact_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            neutral = create_fpk(root / "neutral.fpk", platform="all")
            exact = create_fpk(
                root / "exact.fpk",
                platform="x86",
                binaries=[("bin/server", elf_header(62))],
            )
            selected, manifest, validation = select_artifact(
                [neutral, exact],
                "amd64",
            )
            self.assertTrue(validation.ok, validation.errors)
            self.assertEqual(exact.resolve(), selected)
            self.assertEqual("x86", manifest["platform"])

    def test_artifact_selection_rejects_wrong_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrong = create_fpk(
                root / "arm64.fpk",
                platform="arm",
                binaries=[("bin/server", elf_header(183))],
            )
            with self.assertRaises(UsageError):
                select_artifact([wrong], "amd64")

    def test_remote_cleanup_requires_a_successful_rm(self) -> None:
        config = SSHConfig("root@example.invalid")
        with patch("scripts.fpk_lib.remote.remote_run") as run:
            _safe_remote_cleanup(config, "/tmp/fn-fpk-builder-123456abcdef")
        run.assert_called_once_with(
            config,
            ["rm", "-rf", "--", "/tmp/fn-fpk-builder-123456abcdef"],
            check=True,
        )

    def test_log_output_redacts_high_confidence_secrets(self) -> None:
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        value, count = _redact_log_text(
            f"ready password=hunter2 token={token} "
            "AKIAABCDEFGHIJKLMNOP\n"
        )
        self.assertGreaterEqual(count, 3)
        self.assertNotIn("hunter2", value)
        self.assertNotIn(token, value)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", value)

    def test_log_read_error_does_not_reintroduce_raw_secret(self) -> None:
        config = SSHConfig("root@example.invalid")
        secret = "TOPSECRET123"
        responses = (
            RemoteResult("readlink base", 0, "/vol1/@appdata/fixture-app\n", ""),
            RemoteResult(
                "readlink target",
                0,
                "/vol1/@appdata/fixture-app/service.log\n",
                "",
            ),
            RemoteResult(
                "tail",
                1,
                "",
                f"tail failed password={secret}",
            ),
        )
        with patch(
            "scripts.fpk_lib.remote.remote_run",
            side_effect=responses,
        ):
            report = app_logs(
                config,
                "fixture-app",
                relative_path="service.log",
            )
        self.assertFalse(report.ok)
        rendered = json.dumps(report.as_dict())
        self.assertNotIn(secret, rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_installed_detection_uses_documented_status_noinstall(self) -> None:
        config = SSHConfig("root@example.invalid")
        with patch(
            "scripts.fpk_lib.remote.remote_run",
            return_value=RemoteResult("status", 0, "noinstall\n", ""),
        ) as run:
            self.assertFalse(application_installed(config, "fixture-app"))
        run.assert_called_once_with(
            config,
            ["appcenter-cli", "status", "fixture-app"],
        )

    def test_uninstall_requires_noinstall_postcondition(self) -> None:
        config = SSHConfig("root@example.invalid")
        responses = (
            RemoteResult("stop", 0, "ok\n", ""),
            RemoteResult("uninstall", 0, "ok\n", ""),
            RemoteResult("status", 0, "running\n", ""),
        )
        with patch(
            "scripts.fpk_lib.remote.remote_run",
            side_effect=responses,
        ):
            report = uninstall(config, "fixture-app", confirmed=True)
        self.assertFalse(report.ok)
        self.assertFalse(report.details["uninstall_verified"])
        self.assertTrue(any("still installed" in item for item in report.errors))

    def test_installed_payload_verification_uses_complete_checksum_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = create_fpk(Path(temporary) / "fixture.fpk")
            checksum_data, expected_count = build_payload_checksum_manifest(package)
            checksum_digest = hashlib.sha256(checksum_data).hexdigest()
            with patch(
                "scripts.fpk_lib.remote.remote_copy",
            ) as copy, patch(
                "scripts.fpk_lib.remote.remote_run",
                return_value=RemoteResult("chmod", 0, "", ""),
            ), patch(
                "scripts.fpk_lib.remote._remote_sha256",
                return_value=checksum_digest,
            ), patch(
                "scripts.fpk_lib.remote.remote_shell",
                return_value=RemoteResult(
                    "verify",
                    0,
                    f"verified_regular_files={expected_count}\n",
                    "",
                ),
            ) as shell:
                evidence = _verify_installed_payload(
                    SSHConfig("root@example.invalid"),
                    "fixture-app",
                    package,
                    "/tmp/fn-fpk-builder-123456abcdef",
                )
            self.assertEqual(expected_count, evidence["verified_regular_files"])
            self.assertEqual(checksum_digest, evidence["checksum_manifest_sha256"])
            copy.assert_called_once()
            self.assertTrue(shell.call_args.kwargs["check"])
            self.assertIn("readlink -f", shell.call_args.args[1])

    def test_successful_deploy_records_payload_process_manifest_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = create_fpk(Path(temporary) / "fixture.fpk")
            package_sha = sha256_file(package)
            installed_manifest = {
                "path": "/var/apps/fixture-app/manifest",
                "appname": "fixture-app",
                "version": "1.2.3",
                "sha256": "b" * 64,
                "matches_fpk": True,
            }

            def run(_config, argv, **_kwargs):
                if argv[:2] == ["appcenter-cli", "install-fpk"]:
                    return RemoteResult("install", 0, "[Info]complete", "")
                if argv[:2] == ["appcenter-cli", "status"]:
                    return RemoteResult("status", 0, "running\n", "")
                return RemoteResult(" ".join(argv), 0, "", "")

            with patch(
                "scripts.fpk_lib.remote.remote_architecture",
                return_value=("amd64", "x86_64"),
            ), patch(
                "scripts.fpk_lib.remote.select_artifact",
                return_value=(
                    package,
                    {"appname": "fixture-app", "version": "1.2.3", "platform": "all"},
                    Report(),
                ),
            ), patch(
                "scripts.fpk_lib.remote.application_installed",
                return_value=False,
            ), patch(
                "scripts.fpk_lib.remote._resolve_install_volume",
                return_value=("1", "test"),
            ), patch(
                "scripts.fpk_lib.remote.remote_run",
                side_effect=run,
            ), patch(
                "scripts.fpk_lib.remote.remote_copy",
            ), patch(
                "scripts.fpk_lib.remote._remote_sha256",
                return_value=package_sha,
            ), patch(
                "scripts.fpk_lib.remote._verify_installed_payload",
                return_value={"verified_regular_files": 1},
            ), patch(
                "scripts.fpk_lib.remote._verify_installed_manifest",
                return_value=installed_manifest,
            ) as verify_manifest, patch(
                "scripts.fpk_lib.remote.app_logs",
                return_value=Report(details={"logs": "ready"}),
            ):
                report = deploy(
                    SSHConfig("root@example.invalid"),
                    [package],
                )
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(
                {"verified_regular_files": 1},
                report.details["installed_payload"],
            )
            self.assertTrue(report.details["process_status"]["verified_running"])
            self.assertEqual(
                installed_manifest,
                report.details["installed_manifest"],
            )
            self.assertEqual("ready", report.details["post_deploy_logs"]["details"]["logs"])
            verify_manifest.assert_called_once_with(
                ANY,
                "fixture-app",
                "1.2.3",
                report.details["fpk_manifest_sha256"],
            )

    def test_replacement_stops_if_uninstall_postcondition_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = create_fpk(Path(temporary) / "fixture.fpk")
            commands: list[list[str]] = []

            def run(_config, argv, **_kwargs):
                commands.append(argv)
                return RemoteResult(" ".join(argv), 0, "ok\n", "")

            with patch(
                "scripts.fpk_lib.remote.remote_architecture",
                return_value=("amd64", "x86_64"),
            ), patch(
                "scripts.fpk_lib.remote.select_artifact",
                return_value=(
                    package,
                    {"appname": "fixture-app", "version": "1.2.3", "platform": "all"},
                    Report(),
                ),
            ), patch(
                "scripts.fpk_lib.remote.application_installed",
                side_effect=(True, True, True),
            ), patch(
                "scripts.fpk_lib.remote.remote_run",
                side_effect=run,
            ), patch(
                "scripts.fpk_lib.remote.remote_copy",
            ), patch(
                "scripts.fpk_lib.remote._remote_sha256",
                return_value=sha256_file(package),
            ):
                report = deploy(
                    SSHConfig("root@example.invalid"),
                    [package],
                    volume="1",
                )
            self.assertFalse(report.ok)
            self.assertTrue(any("still installed" in item for item in report.errors))
            self.assertFalse(
                any(command[:2] == ["appcenter-cli", "install-fpk"] for command in commands)
            )

    def test_existing_app_is_always_uninstalled_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = create_fpk(Path(temporary) / "fixture.fpk")
            commands: list[list[str]] = []
            events: list[tuple[str, ...]] = []

            def run(_config, argv, **_kwargs):
                commands.append(argv)
                events.append(tuple(argv[:2]))
                if argv[:2] == ["appcenter-cli", "status"]:
                    return RemoteResult("status", 0, "running\n", "")
                return RemoteResult(" ".join(argv), 0, "[Info]complete\n", "")

            def copy(_config, _local, _remote_path):
                events.append(("upload",))

            def remote_digest(_config, _remote_path):
                events.append(("remote-sha256",))
                return sha256_file(package)

            with patch(
                "scripts.fpk_lib.remote.remote_architecture",
                return_value=("amd64", "x86_64"),
            ), patch(
                "scripts.fpk_lib.remote.select_artifact",
                return_value=(
                    package,
                    {"appname": "fixture-app", "version": "1.2.3", "platform": "all"},
                    Report(),
                ),
            ), patch(
                "scripts.fpk_lib.remote.application_installed",
                side_effect=(True, True, False),
            ), patch(
                "scripts.fpk_lib.remote.remote_run",
                side_effect=run,
            ), patch(
                "scripts.fpk_lib.remote.remote_copy",
                side_effect=copy,
            ), patch(
                "scripts.fpk_lib.remote._remote_sha256",
                side_effect=remote_digest,
            ), patch(
                "scripts.fpk_lib.remote._verify_installed_payload",
                return_value={"verified_regular_files": 1},
            ), patch(
                "scripts.fpk_lib.remote._verify_installed_manifest",
                return_value={"matches_fpk": True},
            ), patch(
                "scripts.fpk_lib.remote.app_logs",
                return_value=Report(details={"logs": "ready"}),
            ):
                report = deploy(
                    SSHConfig("root@example.invalid"),
                    [package],
                    volume="1",
                )

            self.assertTrue(report.ok, report.errors)
            stop_index = next(
                index
                for index, command in enumerate(commands)
                if command[:2] == ["appcenter-cli", "stop"]
            )
            uninstall_index = next(
                index
                for index, command in enumerate(commands)
                if command[:2] == ["appcenter-cli", "uninstall"]
            )
            install_index = next(
                index
                for index, command in enumerate(commands)
                if command[:2] == ["appcenter-cli", "install-fpk"]
            )
            self.assertLess(stop_index, uninstall_index)
            self.assertLess(uninstall_index, install_index)
            self.assertLess(
                events.index(("remote-sha256",)),
                events.index(("appcenter-cli", "uninstall")),
            )
            self.assertEqual("uninstall-then-install", report.details["deployment_mode"])
            self.assertEqual("explicit", report.details["install_volume_source"])
            self.assertTrue(
                report.details["preinstall_uninstall"]["verified_noinstall"]
            )

    def test_successful_rollback_verifies_status_and_installed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = create_fpk(root / "new.fpk")
            rollback = create_fpk(root / "rollback.fpk")
            package_sha = sha256_file(package)
            rollback_sha = sha256_file(rollback)

            def run(_config, argv, **_kwargs):
                if argv[:2] == ["appcenter-cli", "install-fpk"]:
                    if argv[2].endswith("/package.fpk"):
                        return RemoteResult("install new", 0, "[Error]new failed", "")
                    return RemoteResult("install rollback", 0, "[Info]complete", "")
                if argv[:2] == ["appcenter-cli", "status"]:
                    return RemoteResult("status", 0, "running\n", "")
                return RemoteResult(" ".join(argv), 0, "", "")

            def remote_digest(_config, path):
                if path.endswith("/package.fpk"):
                    return package_sha
                if path.endswith("/rollback.fpk"):
                    return rollback_sha
                return "a" * 64

            installed_manifest = {
                "path": "/var/apps/fixture-app/manifest",
                "appname": "fixture-app",
                "version": "1.2.3",
                "sha256": "a" * 64,
            }
            with patch(
                "scripts.fpk_lib.remote.remote_architecture",
                return_value=("amd64", "x86_64"),
            ), patch(
                "scripts.fpk_lib.remote.select_artifact",
                return_value=(
                    package,
                    {"appname": "fixture-app", "version": "1.2.3", "platform": "all"},
                    Report(),
                ),
            ), patch(
                "scripts.fpk_lib.remote.application_installed",
                side_effect=(True, True, False, True, False),
            ), patch(
                "scripts.fpk_lib.remote.remote_run",
                side_effect=run,
            ), patch(
                "scripts.fpk_lib.remote.remote_copy",
            ), patch(
                "scripts.fpk_lib.remote._remote_sha256",
                side_effect=remote_digest,
            ), patch(
                "scripts.fpk_lib.remote._verify_installed_manifest",
                return_value=installed_manifest,
            ) as verify, patch(
                "scripts.fpk_lib.remote._verify_installed_payload",
                return_value={"verified_regular_files": 1},
            ), patch(
                "scripts.fpk_lib.remote.app_logs",
                return_value=Report(details={"logs": "rollback ready"}),
            ):
                report = deploy(
                    SSHConfig("root@example.invalid"),
                    [package],
                    rollback_fpk=rollback,
                    volume="1",
                )
            self.assertFalse(report.ok)
            self.assertTrue(report.details["rollback"]["verified"])
            self.assertTrue(
                report.details["rollback"]["preinstall_uninstall"][
                    "verified_noinstall"
                ]
            )
            self.assertEqual("running", report.details["rollback"]["status"]["stdout"])
            self.assertEqual(
                installed_manifest,
                report.details["rollback"]["installed_manifest"],
            )
            verify.assert_called_once_with(
                ANY,
                "fixture-app",
                "1.2.3",
                ANY,
            )


if __name__ == "__main__":
    unittest.main()
