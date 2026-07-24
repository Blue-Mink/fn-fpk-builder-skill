"""Isolated, architecture-aware fnpack build orchestration."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .archive import inspect_fpk, inspect_overlay, inspect_project, sha256_file
from .manifest import parse_manifest, update_manifest_file
from .report import OperationError, Report, UsageError
from .toolchain import FnpackInfo


ARCH_TO_PLATFORM = {
    "amd64": "x86",
    "arm64": "arm",
    "all": "all",
}
IGNORED_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".DS_Store",
    "__MACOSX",
    "__pycache__",
}


@dataclass(frozen=True)
class BuildOptions:
    project: Path
    output: Path
    architectures: tuple[str, ...]
    fnpack: FnpackInfo
    overlay_amd64: Path | None = None
    overlay_arm64: Path | None = None
    version: str | None = None
    keep_staging: bool = False


def _ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in IGNORED_NAMES}
    ignored.update(name for name in names if name.endswith(".fpk") or name.endswith(".fpk.sha256"))
    return ignored


def _merge_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise UsageError(f"overlay directory does not exist: {source}")
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        symlinks=True,
        ignore=_ignore,
    )


def _stage_project(project: Path, stage: Path, overlay: Path | None) -> None:
    shutil.copytree(project, stage, symlinks=True, ignore=_ignore)
    if overlay:
        _merge_tree(overlay, stage)


def _find_built_fpk(stage: Path, before: set[Path]) -> Path:
    candidates = {
        path.resolve()
        for path in stage.glob("*.fpk")
        if path.is_file() and path.resolve() not in before
    }
    if len(candidates) != 1:
        names = ", ".join(str(item) for item in sorted(candidates))
        raise OperationError(
            f"fnpack must produce exactly one new FPK in staging; found {len(candidates)}: {names}"
        )
    return next(iter(candidates))


def _write_sha_file(path: Path, digest: str) -> Path:
    sidecar = path.with_name(f"{path.name}.sha256")
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{sidecar.name}.",
        suffix=".tmp",
        dir=sidecar.parent,
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(f"{digest}  {path.name}\n")
    os.replace(temporary, sidecar)
    return sidecar


def _build_one(options: BuildOptions, architecture: str, parent: Path) -> Report:
    report = Report()
    stage = parent / f"stage-{architecture}"
    overlay = {
        "amd64": options.overlay_amd64,
        "arm64": options.overlay_arm64,
        "all": None,
    }[architecture]
    _stage_project(options.project, stage, overlay)

    manifest_path = stage / "manifest"
    if not manifest_path.is_file():
        report.error("staged project is missing manifest")
        return report
    updates = {"platform": ARCH_TO_PLATFORM[architecture]}
    if options.version:
        updates["version"] = options.version
    try:
        update_manifest_file(manifest_path, updates)
    except (OSError, ValueError) as exc:
        report.error(f"cannot update staged manifest: {exc}")
        return report

    staged = inspect_project(stage, expected_arch=architecture)
    report.merge(staged, detail_key="staged_validation")
    if not staged.ok:
        return report

    before = {path.resolve() for path in stage.glob("*.fpk")}
    command = [str(options.fnpack.path), "build", "--directory", str(stage)]
    process = subprocess.run(
        command,
        cwd=stage,
        check=False,
        capture_output=True,
        text=True,
    )
    report.details["fnpack_command"] = command
    report.details["fnpack_exit_code"] = process.returncode
    report.details["fnpack_stdout"] = process.stdout[-12000:]
    report.details["fnpack_stderr"] = process.stderr[-12000:]
    if process.returncode != 0:
        report.error(f"fnpack build failed with exit code {process.returncode}")
        return report
    try:
        built = _find_built_fpk(stage, before)
    except OperationError as exc:
        report.error(str(exc))
        return report

    document = parse_manifest(manifest_path)
    appname = document.values.get("appname", "application")
    version = document.values.get("version", "0.0.0")
    output_name = f"{appname}-{version}-fnos-{architecture}.fpk"
    options.output.mkdir(parents=True, exist_ok=True)
    destination = options.output / output_name
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_name}.",
        suffix=".tmp",
        dir=options.output,
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    shutil.copy2(built, temporary)

    inspected = inspect_fpk(temporary, expected_arch=architecture)
    report.details["artifact_validation"] = inspected.as_dict()
    report.ok = report.ok and inspected.ok
    report.errors.extend(inspected.errors)
    report.warnings.extend(inspected.warnings)
    if not inspected.ok:
        temporary.unlink(missing_ok=True)
        return report
    os.replace(temporary, destination)
    digest = sha256_file(destination)
    sidecar = _write_sha_file(destination, digest)
    report.artifact(
        destination,
        kind="fpk",
        sha256=digest,
        architecture=architecture,
        platform=ARCH_TO_PLATFORM[architecture],
        appname=appname,
        version=version,
    )
    report.artifact(sidecar, kind="checksum", sha256=sha256_file(sidecar))
    return report


def build_packages(options: BuildOptions) -> Report:
    report = Report()
    project = options.project.expanduser().resolve()
    output = options.output.expanduser().resolve()
    if not project.is_dir():
        raise UsageError(f"project directory does not exist: {project}")
    source_validation = inspect_project(project)
    report.details["source_validation"] = source_validation.as_dict()
    report.warnings.extend(source_validation.warnings)
    if not source_validation.ok:
        report.ok = False
        report.errors.extend(source_validation.errors)
        return report
    overlays = {
        "amd64": options.overlay_amd64,
        "arm64": options.overlay_arm64,
    }
    for architecture, overlay in overlays.items():
        if overlay is None:
            continue
        if architecture not in options.architectures:
            report.warn(f"{architecture} overlay is unused by the requested targets")
            continue
        overlay_validation = inspect_overlay(overlay, expected_arch=architecture)
        report.details[f"overlay_{architecture}_validation"] = overlay_validation.as_dict()
        report.warnings.extend(overlay_validation.warnings)
        if not overlay_validation.ok:
            report.ok = False
            report.errors.extend(overlay_validation.errors)
            return report
    if output == project or project in output.parents:
        report.warn("output is inside the project; generated FPK files are excluded from staging")

    temp_context = tempfile.TemporaryDirectory(prefix="fn-fpk-build-")
    parent = Path(temp_context.name)
    if options.keep_staging:
        parent = Path(tempfile.mkdtemp(prefix="fn-fpk-build-kept-"))
        temp_context.cleanup()
        report.warn(f"staging was retained for diagnostics: {parent}")
        report.details["staging"] = str(parent)

    for architecture in options.architectures:
        child = _build_one(
            BuildOptions(
                project=project,
                output=output,
                architectures=options.architectures,
                fnpack=options.fnpack,
                overlay_amd64=(
                    options.overlay_amd64.expanduser().resolve()
                    if options.overlay_amd64
                    else None
                ),
                overlay_arm64=(
                    options.overlay_arm64.expanduser().resolve()
                    if options.overlay_arm64
                    else None
                ),
                version=options.version,
                keep_staging=options.keep_staging,
            ),
            architecture,
            parent,
        )
        report.merge(child, detail_key=f"build_{architecture}")
        if not child.ok:
            break
    if not options.keep_staging:
        temp_context.cleanup()
    report.details["architectures"] = list(options.architectures)
    report.details["fnpack"] = {
        "path": str(options.fnpack.path),
        "version": options.fnpack.version,
        "sha256": options.fnpack.sha256,
        "verified": options.fnpack.verified,
    }
    return report
