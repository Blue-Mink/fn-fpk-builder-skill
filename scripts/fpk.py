#!/usr/bin/env python3
"""Build and audit fnOS FPK packages with deterministic safety checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
MAX_SOURCE_SNAPSHOT_BYTES = 16 * 1024 * 1024
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from fpk_lib.archive import inspect_fpk, inspect_project  # noqa: E402
from fpk_lib.builder import BuildOptions, build_packages  # noqa: E402
from fpk_lib.manifest import APPNAME_RE  # noqa: E402
from fpk_lib.report import OperationError, Report, UsageError  # noqa: E402
from fpk_lib.toolchain import (  # noqa: E402
    BASE_URL,
    DOWNLOADS,
    FNPACK_VERSION,
    host_key,
    install_fnpack,
    load_provenance,
    resolve_fnpack,
    toolchain_report,
)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit the stable JSON result schema")


def _add_fnpack(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fnpack", help="explicit fnpack binary (or set FNPACK_BIN)")
    parser.add_argument("--cache-dir", help="verified fnpack cache directory")
    parser.add_argument(
        "--allow-unverified-fnpack",
        action="store_true",
        help="accept an explicitly trusted non-pinned fnpack binary",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, build, and audit fnOS FPK application packages."
    )
    parser.add_argument("--version", action="version", version="fn-fpk-builder-skill 1")
    commands = parser.add_subparsers(dest="command", required=True)

    toolchain = commands.add_parser("toolchain", help="inspect or install verified fnpack")
    _add_json(toolchain)
    _add_fnpack(toolchain)
    toolchain.add_argument("--install", action="store_true", help="download verified fnpack")

    initialize = commands.add_parser("init", help="create an official fnOS project")
    _add_json(initialize)
    _add_fnpack(initialize)
    initialize.add_argument("appname", help="new fnOS application identifier")
    initialize.add_argument(
        "--path",
        default=".",
        help="parent directory in which fnpack creates the project",
    )
    initialize.add_argument(
        "--template",
        choices=("native", "docker"),
        default="native",
        help="official fnpack project template",
    )
    initialize.add_argument(
        "--without-ui",
        action="store_true",
        help="create the official service-only variant",
    )

    doctor = commands.add_parser("doctor", help="diagnose host and project readiness")
    _add_json(doctor)
    _add_fnpack(doctor)
    doctor.add_argument("--project", default=".", help="fnOS package project directory")
    doctor.add_argument("--target-arch", choices=("amd64", "arm64", "all"))

    build = commands.add_parser("build", help="stage and build one or more FPK artifacts")
    _add_json(build)
    _add_fnpack(build)
    build.add_argument("--project", default=".", help="fnOS package project directory")
    build.add_argument("--out", default="dist", help="artifact output directory")
    build.add_argument(
        "--arch",
        action="append",
        choices=("amd64", "arm64", "all", "both"),
        help="target architecture; repeat or use both (default: infer manifest)",
    )
    build.add_argument("--overlay-amd64", help="tree merged only into amd64 staging")
    build.add_argument("--overlay-arm64", help="tree merged only into arm64 staging")
    build.add_argument("--version", dest="package_version", help="staged manifest version")
    build.add_argument(
        "--keep-staging",
        action="store_true",
        help="retain the temporary staged trees for diagnostics",
    )

    inspect = commands.add_parser("inspect", help="audit a source tree or final FPK")
    _add_json(inspect)
    inspect.add_argument("target", help="project directory or .fpk path")
    inspect.add_argument("--target-arch", choices=("amd64", "arm64", "all"))

    sources = commands.add_parser("sources", help="show or check the provenance ledger")
    _add_json(sources)
    sources.add_argument(
        "--check",
        action="store_true",
        help="check local source paths, commits, and official documentation URL",
    )
    return parser


def _fnpack_from_args(args: argparse.Namespace):
    return resolve_fnpack(
        args.fnpack,
        cache_dir=args.cache_dir,
        allow_unverified=args.allow_unverified_fnpack,
    )


def command_toolchain(args: argparse.Namespace) -> Report:
    return toolchain_report(
        explicit=args.fnpack,
        cache_dir=args.cache_dir,
        install=args.install,
        allow_unverified=args.allow_unverified_fnpack,
    )


def command_init(args: argparse.Namespace) -> Report:
    if not APPNAME_RE.fullmatch(args.appname):
        raise UsageError("appname must be a 3-32 character fnOS identifier")
    info = _fnpack_from_args(args)
    parent = Path(args.path).expanduser().resolve()
    if not parent.is_dir():
        raise UsageError(f"parent directory does not exist: {parent}")
    target = parent / args.appname
    if target.exists():
        raise UsageError(f"target project already exists: {target}")
    command = [
        str(info.path),
        "create",
        args.appname,
        "--template",
        args.template,
    ]
    if args.without_ui:
        command.append("--without-ui=true")
    process = subprocess.run(
        command,
        cwd=parent,
        check=False,
        capture_output=True,
        text=True,
    )
    report = Report()
    report.details["command"] = command
    report.details["stdout"] = process.stdout[-12000:]
    report.details["stderr"] = process.stderr[-12000:]
    if process.returncode != 0:
        report.error(f"fnpack create failed with exit code {process.returncode}")
        return report
    if not target.is_dir():
        report.error(f"fnpack reported success but did not create {target}")
        return report
    removed_metadata: list[str] = []
    for metadata in target.rglob(".DS_Store"):
        if metadata.is_file():
            metadata.unlink()
            removed_metadata.append(str(metadata.relative_to(target)))
    normalized_scripts: list[str] = []
    command_dir = target / "cmd"
    if command_dir.is_dir():
        for script in command_dir.iterdir():
            if script.is_file() and not script.is_symlink():
                script.chmod(script.stat().st_mode | 0o755)
                normalized_scripts.append(str(script.relative_to(target)))
    report.artifact(target, kind="project")
    validation = inspect_project(target)
    report.details["project_validation"] = validation.as_dict()
    report.details["removed_host_metadata"] = removed_metadata
    report.details["normalized_executable_scripts"] = normalized_scripts
    for item in validation.errors:
        report.warn(f"generated template still requires configuration: {item}")
    report.warnings.extend(validation.warnings)
    return report


def command_doctor(args: argparse.Namespace) -> Report:
    report = Report()
    report.details["host"] = {
        "key": host_key(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "ssh": shutil.which("ssh"),
        "scp": shutil.which("scp"),
    }
    if platform.system().lower() not in {"darwin", "linux"}:
        report.error("this skill executes builds only on macOS or Linux")
    validation = inspect_project(args.project, expected_arch=args.target_arch)
    report.merge(validation, detail_key="project_validation")
    try:
        info = _fnpack_from_args(args)
        report.details["fnpack"] = {
            "path": str(info.path),
            "version": info.version,
            "sha256": info.sha256,
            "verified": info.verified,
        }
    except UsageError as exc:
        report.error(str(exc))
    return report


def _architectures(args: argparse.Namespace) -> tuple[str, ...]:
    requested = args.arch or []
    if "both" in requested:
        if len(requested) != 1:
            raise UsageError("--arch both cannot be combined with another --arch")
        return ("amd64", "arm64")
    if requested:
        result: list[str] = []
        for item in requested:
            if item not in result:
                result.append(item)
        if "all" in result and len(result) > 1:
            raise UsageError("--arch all cannot be combined with architecture-specific targets")
        return tuple(result)

    manifest_path = Path(args.project).expanduser().resolve() / "manifest"
    if not manifest_path.is_file():
        raise UsageError("cannot infer architecture because the project manifest is missing")
    from fpk_lib.manifest import parse_manifest

    platform_value = parse_manifest(manifest_path).values.get("platform")
    inferred = {"x86": "amd64", "arm": "arm64", "all": "all"}.get(platform_value or "")
    if not inferred:
        raise UsageError(f"cannot infer target from manifest platform={platform_value!r}")
    return (inferred,)


def command_build(args: argparse.Namespace) -> Report:
    info = _fnpack_from_args(args)
    architectures = _architectures(args)
    if "all" in architectures and (args.overlay_amd64 or args.overlay_arm64):
        raise UsageError("architecture overlays cannot be used with --arch all")
    options = BuildOptions(
        project=Path(args.project),
        output=Path(args.out),
        architectures=architectures,
        fnpack=info,
        overlay_amd64=Path(args.overlay_amd64) if args.overlay_amd64 else None,
        overlay_arm64=Path(args.overlay_arm64) if args.overlay_arm64 else None,
        version=args.package_version,
        keep_staging=args.keep_staging,
    )
    return build_packages(options)


def command_inspect(args: argparse.Namespace) -> Report:
    target = Path(args.target).expanduser()
    if target.is_dir():
        return inspect_project(target, expected_arch=args.target_arch)
    return inspect_fpk(target, expected_arch=args.target_arch)


def _fetch_url_snapshot(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "fn-fpk-builder-skill/1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SOURCE_SNAPSHOT_BYTES:
                raise OSError(
                    "official documentation exceeds the "
                    f"{MAX_SOURCE_SNAPSHOT_BYTES}-byte source-check limit"
                )
            chunks.append(chunk)
        return b"".join(chunks), str(getattr(response, "status", 200))


def command_sources(args: argparse.Namespace) -> Report:
    report = Report()
    try:
        ledger = load_provenance(SKILL_ROOT)
    except (OSError, json.JSONDecodeError) as exc:
        report.error(f"cannot load provenance ledger: {exc}")
        return report
    report.details["provenance"] = ledger
    if not args.check:
        return report

    checks: dict[str, object] = {}
    fnpack_issues: list[str] = []
    ledger_fnpack = ledger.get("fnpack", {})
    if not isinstance(ledger_fnpack, dict):
        fnpack_issues.append("provenance fnpack record is not an object")
    else:
        if ledger_fnpack.get("version") != FNPACK_VERSION:
            fnpack_issues.append(
                f"version mismatch: ledger={ledger_fnpack.get('version')!r}, "
                f"implementation={FNPACK_VERSION!r}"
            )
        recorded_downloads = ledger_fnpack.get("downloads", {})
        if not isinstance(recorded_downloads, dict):
            fnpack_issues.append("provenance fnpack.downloads is not an object")
            recorded_downloads = {}
        for key, spec in DOWNLOADS.items():
            recorded = recorded_downloads.get(key)
            if not isinstance(recorded, dict):
                fnpack_issues.append(f"missing provenance download record: {key}")
                continue
            expected_url = f"{BASE_URL}/{spec['filename']}"
            if recorded.get("url") != expected_url:
                fnpack_issues.append(f"{key} URL differs from the implementation")
            if recorded.get("sha256") != spec.get("sha256"):
                fnpack_issues.append(f"{key} SHA-256 differs from the implementation")

    template_issues: list[str] = []
    template = SKILL_ROOT / "assets" / "github-actions" / "fpk.yml"
    workflow = SKILL_ROOT / ".github" / "workflows" / "ci.yml"
    for path, keys in (
        (template, ("linux-amd64",)),
        (workflow, ("linux-amd64", "darwin-amd64", "darwin-arm64")),
    ):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            template_issues.append(f"cannot read {path.relative_to(SKILL_ROOT)}: {exc}")
            continue
        for key in keys:
            digest = DOWNLOADS[key].get("sha256")
            if digest and digest not in content:
                template_issues.append(
                    f"{path.relative_to(SKILL_ROOT)} is missing the pinned {key} SHA-256"
                )
    checks["fnpack_ledger"] = {
        "ok": not fnpack_issues,
        "issues": fnpack_issues,
    }
    checks["workflow_hashes"] = {
        "ok": not template_issues,
        "issues": template_issues,
    }
    for issue in [*fnpack_issues, *template_issues]:
        report.error(f"local source drift: {issue}")

    docs = ledger.get("official_docs", {})
    if isinstance(docs, dict):
        local_root_env = docs.get("local_root_env")
        local_root = (
            os.environ.get(str(local_root_env))
            if isinstance(local_root_env, str) and local_root_env
            else None
        )
        if local_root:
            local_root_path = Path(local_root).expanduser()
            exists = local_root_path.is_dir()
            checks["official_docs_local"] = {
                "environment": local_root_env,
                "configured": True,
                "path": str(local_root_path),
                "exists": exists,
            }
            if not exists:
                report.warn(
                    "local official documentation snapshot is unavailable: "
                    f"{local_root_path}"
                )
        elif isinstance(local_root_env, str) and local_root_env:
            checks["official_docs_local"] = {
                "environment": local_root_env,
                "configured": False,
            }
        url = docs.get("url")
        if url:
            try:
                snapshot, status = _fetch_url_snapshot(str(url))
            except (urllib.error.URLError, OSError) as exc:
                checks["official_docs_url"] = {
                    "url": url,
                    "reachable": False,
                    "status": str(exc),
                    "content_verified": False,
                }
                report.warn(f"official documentation content check failed: {exc}")
            else:
                actual_digest = hashlib.sha256(snapshot).hexdigest()
                recorded_digest = docs.get("sha256")
                recorded_bytes = docs.get("observed_full_bytes")
                digest_matches = (
                    isinstance(recorded_digest, str)
                    and actual_digest == recorded_digest
                )
                size_matches = (
                    isinstance(recorded_bytes, int)
                    and len(snapshot) == recorded_bytes
                )
                checks["official_docs_url"] = {
                    "url": url,
                    "reachable": True,
                    "status": status,
                    "bytes": len(snapshot),
                    "sha256": actual_digest,
                    "recorded_sha256": recorded_digest,
                    "digest_matches": digest_matches,
                    "size_matches": size_matches,
                    "content_verified": digest_matches and size_matches,
                }
                if not isinstance(recorded_digest, str):
                    report.error(
                        "official documentation provenance is missing a SHA-256"
                    )
                elif not digest_matches:
                    report.error(
                        "official documentation content SHA-256 has drifted: "
                        f"got {actual_digest}, recorded {recorded_digest}"
                    )
                if not isinstance(recorded_bytes, int):
                    report.error(
                        "official documentation provenance is missing a byte count"
                    )
                elif not size_matches:
                    report.error(
                        "official documentation byte count has drifted: "
                        f"got {len(snapshot)}, recorded {recorded_bytes}"
                    )

    report.details["checks"] = checks
    return report


COMMANDS = {
    "toolchain": command_toolchain,
    "init": command_init,
    "doctor": command_doctor,
    "build": command_build,
    "inspect": command_inspect,
    "sources": command_sources,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = COMMANDS[args.command](args)
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
