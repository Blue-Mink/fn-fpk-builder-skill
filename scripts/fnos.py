#!/usr/bin/env python3
"""Manage fnOS application lifecycle over SSH."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from fpk_lib.remote import (  # noqa: E402
    SSHConfig,
    app_action,
    app_logs,
    deploy,
    remote_doctor,
    uninstall,
    verify_web_app,
)
from fpk_lib.report import OperationError, Report, UsageError  # noqa: E402
from fpk_lib.smoke import smoke_test  # noqa: E402
from fpk_lib.toolchain import resolve_fnpack  # noqa: E402


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit the stable JSON result schema")


def _add_remote(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host",
        default=os.environ.get("FNOS_HOST"),
        help="SSH target, for example root@fnos-host (or set FNOS_HOST)",
    )
    parser.add_argument("--port", type=int, default=22, help="SSH port")
    parser.add_argument("--identity", help="SSH private-key path")
    parser.add_argument("--connect-timeout", type=int, default=15)


def _add_app_action(commands, name: str, help_text: str) -> None:
    parser = commands.add_parser(name, help=help_text)
    _add_json(parser)
    _add_remote(parser)
    parser.add_argument("appname", help="installed fnOS application identifier")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely manage fnOS apps over SSH.")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="diagnose a remote fnOS device")
    _add_json(doctor)
    _add_remote(doctor)

    deploy_parser = commands.add_parser(
        "deploy",
        help="upload and install an FPK; existing apps are uninstalled first",
    )
    _add_json(deploy_parser)
    _add_remote(deploy_parser)
    deploy_parser.add_argument(
        "artifacts",
        nargs="+",
        help="one or more FPKs or directories; the matching architecture is selected",
    )
    deploy_parser.add_argument(
        "--clean",
        action="store_true",
        help="deprecated compatibility flag; replacement is always uninstall-then-install",
    )
    deploy_parser.add_argument("--env", dest="env_file", help="installation environment file")
    deploy_parser.add_argument("--volume", help="fnOS installation volume")
    deploy_parser.add_argument("--rollback-fpk", help="explicit previous package for rollback")
    deploy_parser.add_argument("--no-start", action="store_true", help="do not start after install")
    deploy_parser.add_argument("--status-retries", type=int, default=10)

    for action in ("status", "start", "stop"):
        _add_app_action(commands, action, f"{action} an installed fnOS application")

    verify = commands.add_parser("verify-web-app", help="collect AppCenter/DB/runtime/HTTP evidence for a Web app")
    _add_json(verify)
    _add_remote(verify)
    verify.add_argument("appname")
    verify.add_argument("--web-port", type=int, help="expected HTTP service port")
    verify.add_argument("--container", help="expected Docker container name")
    verify.add_argument("--health-path", default="/", help="HTTP path to probe, default /")

    logs = commands.add_parser("logs", help="discover or tail app-owned logs")
    _add_json(logs)
    _add_remote(logs)
    logs.add_argument("appname")
    logs.add_argument("--path", help="relative log path below /var/apps/{app}/var")
    logs.add_argument("--lines", type=int, default=200)

    remove = commands.add_parser("uninstall", help="explicitly uninstall an fnOS application")
    _add_json(remove)
    _add_remote(remove)
    remove.add_argument("appname")
    remove.add_argument("--yes", action="store_true", help="confirm destructive uninstall")

    smoke = commands.add_parser("smoke", help="exercise a unique disposable application")
    _add_json(smoke)
    _add_remote(smoke)
    smoke.add_argument("--volume", help="fnOS installation volume")
    smoke.add_argument("--fnpack", help="explicit fnpack binary (or set FNPACK_BIN)")
    smoke.add_argument("--cache-dir", help="verified fnpack cache directory")
    smoke.add_argument(
        "--allow-unverified-fnpack",
        action="store_true",
        help="accept an explicitly trusted non-pinned fnpack",
    )
    return parser


def _config(args: argparse.Namespace) -> SSHConfig:
    if not args.host:
        raise UsageError("remote operation requires --host or FNOS_HOST")
    if args.port < 1 or args.port > 65535:
        raise UsageError("--port must be between 1 and 65535")
    if args.connect_timeout < 1 or args.connect_timeout > 300:
        raise UsageError("--connect-timeout must be between 1 and 300")
    if args.identity and not Path(args.identity).expanduser().is_file():
        raise UsageError(f"SSH identity file does not exist: {args.identity}")
    return SSHConfig(
        host=args.host,
        port=args.port,
        identity=Path(args.identity) if args.identity else None,
        connect_timeout=args.connect_timeout,
    )


def execute(args: argparse.Namespace) -> Report:
    config = _config(args)
    if args.command == "doctor":
        return remote_doctor(config)
    if args.command == "deploy":
        if args.status_retries < 1 or args.status_retries > 120:
            raise UsageError("--status-retries must be between 1 and 120")
        return deploy(
            config,
            args.artifacts,
            clean=args.clean,
            env_file=args.env_file,
            volume=args.volume,
            rollback_fpk=args.rollback_fpk,
            start=not args.no_start,
            status_retries=args.status_retries,
        )
    if args.command in {"status", "start", "stop"}:
        return app_action(config, args.command, args.appname)
    if args.command == "verify-web-app":
        return verify_web_app(
            config,
            args.appname,
            port=args.web_port,
            container=args.container,
            health_path=args.health_path,
        )
    if args.command == "logs":
        return app_logs(
            config,
            args.appname,
            relative_path=args.path,
            lines=args.lines,
        )
    if args.command == "uninstall":
        return uninstall(config, args.appname, confirmed=args.yes)
    if args.command == "smoke":
        fnpack = resolve_fnpack(
            args.fnpack,
            cache_dir=args.cache_dir,
            allow_unverified=args.allow_unverified_fnpack,
        )
        return smoke_test(config, fnpack, volume=args.volume)
    raise UsageError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = execute(args)
        exit_code = 0 if report.ok else 1
    except UsageError as exc:
        report = Report(ok=False, errors=[str(exc)])
        exit_code = 2
    except OperationError as exc:
        report = Report(ok=False, errors=[str(exc)])
        exit_code = 1
    except KeyboardInterrupt:
        report = Report(ok=False, errors=["interrupted"])
        exit_code = 1
    except Exception as exc:  # Preserve the JSON contract for unexpected host failures.
        report = Report(
            ok=False,
            errors=[f"unexpected {type(exc).__name__}: {exc}"],
        )
        exit_code = 1
    print(report.render(args.json))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
