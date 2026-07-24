"""Create a disposable no-network fnOS package for live lifecycle testing."""

from __future__ import annotations

import os
import struct
import tempfile
import uuid
import zlib
from pathlib import Path

from .builder import BuildOptions, build_packages
from .remote import (
    SSHConfig,
    app_action,
    app_logs,
    application_installed,
    deploy,
    remote_run,
    remote_shell,
    uninstall,
)
from .report import OperationError, Report, UsageError
from .toolchain import FnpackInfo


def _solid_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\x3b\x82\xf6\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


MAIN_SCRIPT = """#!/bin/sh
PIDFILE="${TRIM_PKGTMP:-/tmp}/${TRIM_APPNAME:-fpk-skill-smoke}.pid"
LOGFILE="${TRIM_PKGVAR:-/tmp}/smoke.log"

case "$1" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      exit 0
    fi
    nohup sleep 2147483647 >>"$LOGFILE" 2>&1 &
    echo "$!" >"$PIDFILE"
    echo "smoke service started" >>"$LOGFILE"
    exit 0
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    echo "smoke service stopped" >>"$LOGFILE"
    exit 0
    ;;
  status)
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && exit 0
    exit 3
    ;;
  *)
    exit 1
    ;;
esac
"""


NOOP_SCRIPT = """#!/bin/sh
exit 0
"""


def _smoke_username(appname: str) -> str:
    suffix = "".join(character for character in appname if character.isalnum())[-8:]
    return f"fpksmoke{suffix}"


def create_smoke_project(parent: Path, appname: str) -> Path:
    project = parent / appname
    username = _smoke_username(appname)
    for relative in ("app", "cmd", "config", "wizard"):
        (project / relative).mkdir(parents=True, exist_ok=True)
    (project / "manifest").write_text(
        "\n".join(
            [
                f"appname={appname}",
                "version=1.0.0",
                "display_name=FPK Skill Smoke",
                "desc=Disposable lifecycle verification package.",
                "maintainer=fn-fpk-builder-skill",
                "maintainer_url=https://developer.fnnas.com/",
                "source=thirdparty",
                "platform=all",
                "ctl_stop=true",
                "checkport=false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (project / "config" / "privilege").write_text(
        (
            '{"defaults":{"run-as":"package"},'
            f'"username":"{username}","groupname":"{username}"'
            "}\n"
        ),
        encoding="utf-8",
    )
    (project / "config" / "resource").write_text("{}\n", encoding="utf-8")
    (project / "app" / "SMOKE_MARKER").write_text(
        f"{appname}\n", encoding="utf-8"
    )
    (project / "ICON.PNG").write_bytes(_solid_png(64, 64))
    (project / "ICON_256.PNG").write_bytes(_solid_png(256, 256))
    scripts = (
        "install_init",
        "install_callback",
        "upgrade_init",
        "upgrade_callback",
        "uninstall_init",
        "uninstall_callback",
        "config_init",
        "config_callback",
    )
    main = project / "cmd" / "main"
    main.write_text(MAIN_SCRIPT, encoding="utf-8")
    main.chmod(0o755)
    for name in scripts:
        path = project / "cmd" / name
        path.write_text(NOOP_SCRIPT, encoding="utf-8")
        path.chmod(0o755)
    return project


def smoke_test(
    config: SSHConfig,
    fnpack: FnpackInfo,
    *,
    volume: str | None = None,
) -> Report:
    report = Report()
    appname = f"fpk-skill-smoke-{uuid.uuid4().hex[:12]}"
    username = _smoke_username(appname)
    report.details["appname"] = appname
    report.details["package_username"] = username
    report.details["host"] = config.host
    with tempfile.TemporaryDirectory(prefix="fn-fpk-smoke-") as temporary:
        root = Path(temporary)
        project = create_smoke_project(root, appname)
        output = root / "dist"
        built = build_packages(
            BuildOptions(
                project=project,
                output=output,
                architectures=("all",),
                fnpack=fnpack,
            )
        )
        report.merge(built, detail_key="build")
        if not built.ok:
            return report
        fpks = sorted(output.glob("*.fpk"))
        if len(fpks) != 1:
            report.error("smoke build did not produce exactly one FPK")
            return report
        try:
            deployed = deploy(
                config,
                [fpks[0]],
                volume=volume,
                start=True,
                status_retries=15,
            )
            report.merge(deployed, detail_key="deploy")
            if deployed.ok:
                status = app_action(config, "status", appname)
                report.merge(status, detail_key="status")
                logs = app_logs(config, appname, lines=100)
                report.merge(logs, detail_key="logs")
                marker = remote_run(
                    config,
                    ["test", "-f", f"/var/apps/{appname}/target/SMOKE_MARKER"],
                )
                report.details["installed_marker_exit_code"] = marker.returncode
                if marker.returncode != 0:
                    report.error("installed smoke marker is missing")
                stopped = app_action(config, "stop", appname)
                report.merge(stopped, detail_key="stop")
        finally:
            absent_confirmed = False
            try:
                if application_installed(config, appname):
                    removed = uninstall(config, appname, confirmed=True)
                    report.merge(removed, detail_key="uninstall")
                    absent_confirmed = not application_installed(config, appname)
                else:
                    absent_confirmed = True
                    report.details["uninstall"] = {
                        "skipped": True,
                        "reason": "smoke application is already absent",
                    }
                if absent_confirmed:
                    metadata_cleanup = remote_shell(
                        config,
                        "for p in "
                        f"/vol[1-9]*/@appcenter/{appname} "
                        f"/vol[1-9]*/@appconf/{appname} "
                        f"/vol[1-9]*/@apphome/{appname} "
                        f"/vol[1-9]*/@apptemp/{appname} "
                        f"/vol[1-9]*/@appmeta/{appname}; do "
                        '[ -d "$p" ] || continue; '
                        'if rmdir -- "$p" 2>/dev/null; then echo "$p"; fi; '
                        "done",
                    )
                    report.details["cleaned_empty_metadata_paths"] = (
                        metadata_cleanup.stdout.splitlines()
                    )
                    if metadata_cleanup.returncode != 0:
                        report.warn("could not check empty smoke metadata directories")
                    data_cleanup = remote_shell(
                        config,
                        f"for p in /vol[1-9]*/@appdata/{appname}; do "
                        '[ -d "$p" ] || continue; '
                        'count=$(find "$p" -mindepth 1 -maxdepth 1 -print | wc -l); '
                        '[ "$count" -eq 1 ] '
                        '&& [ -f "$p/smoke.log" ] '
                        '&& [ ! -L "$p/smoke.log" ] '
                        '|| { echo "unsafe-appdata:$p"; exit 68; }; '
                        'rm -- "$p/smoke.log" || exit 69; '
                        'rmdir -- "$p" || exit 70; '
                        'echo "$p"; '
                        "done",
                    )
                    report.details["cleaned_smoke_appdata_paths"] = (
                        data_cleanup.stdout.splitlines()
                    )
                    if data_cleanup.returncode != 0:
                        report.error(
                            "smoke appdata cleanup failed its strict allowlist: "
                            f"{data_cleanup.stderr.strip() or data_cleanup.stdout.strip() or 'no output'}"
                        )
                    account_cleanup = remote_shell(
                        config,
                        f"u={username}; "
                        'if getent passwd "$u" >/dev/null 2>&1; then '
                        'home=$(getent passwd "$u" | cut -d: -f6); '
                        'shell=$(getent passwd "$u" | cut -d: -f7); '
                        '[ "$home" = "/home/$u" ] && [ "$shell" = "/usr/sbin/nologin" ] '
                        '|| { echo "unsafe-passwd:$u"; exit 65; }; '
                        'userdel "$u" || exit 66; echo "passwd:$u"; '
                        'if [ -d "/home/$u" ] && rmdir -- "/home/$u" 2>/dev/null; then '
                        'echo "home:/home/$u"; fi; '
                        "fi; "
                        'if getent group "$u" >/dev/null 2>&1; then '
                        'groupdel "$u" || exit 67; echo "group:$u"; '
                        "fi",
                    )
                    report.details["cleaned_package_accounts"] = (
                        account_cleanup.stdout.splitlines()
                    )
                    if account_cleanup.returncode != 0:
                        report.error(
                            "smoke package-account cleanup failed: "
                            f"{account_cleanup.stderr.strip() or account_cleanup.stdout.strip() or 'no output'}"
                        )
                else:
                    report.error(
                        "smoke application absence could not be confirmed; "
                        "metadata and account cleanup were skipped"
                    )
                residue = remote_shell(
                    config,
                    "for p in "
                    f"/var/apps/{appname} "
                    f"/usr/local/apps/@appcenter/{appname} "
                    f"/usr/local/apps/@appconf/{appname} "
                    f"/usr/local/apps/@appdata/{appname} "
                    f"/usr/local/apps/@apphome/{appname} "
                    f"/usr/local/apps/@apptemp/{appname} "
                    f"/vol[1-9]*/@appcenter/{appname} "
                    f"/vol[1-9]*/@appdata/{appname} "
                    f"/vol[1-9]*/@appconf/{appname} "
                    f"/vol[1-9]*/@apphome/{appname} "
                    f"/vol[1-9]*/@apptemp/{appname} "
                    f"/vol[1-9]*/@appmeta/{appname} "
                    f"/home/{username}; do "
                    '[ ! -e "$p" ] && [ ! -L "$p" ] || { echo "$p"; exit 1; }; '
                    "done; "
                    f"getent passwd {username} >/dev/null 2>&1 "
                    f"&& {{ echo passwd:{username}; exit 1; }}; "
                    f"getent group {username} >/dev/null 2>&1 "
                    f"&& {{ echo group:{username}; exit 1; }}; "
                    "exit 0",
                )
                report.details["residue_check_exit_code"] = residue.returncode
                report.details["residue_paths"] = residue.stdout.splitlines()
                if residue.returncode != 0:
                    report.error(
                        "smoke application residue remains: "
                        f"{', '.join(residue.stdout.splitlines()) or 'unknown path'}"
                    )
            except (OperationError, UsageError) as exc:
                report.error(f"smoke cleanup failed: {exc}")
    return report
