"""Mode-preserving, path-safe overlay transport for GitHub Actions artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path, PurePosixPath

from .archive import inspect_overlay


class OverlayTransportError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise OverlayTransportError(f"unsafe overlay metadata path: {value!r}")
    return path


def _regular_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for directory, directories, names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in list(directories):
            path = directory_path / name
            mode = path.lstat().st_mode
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(mode):
                raise OverlayTransportError(
                    f"overlay transport does not allow directory symlinks: {relative}"
                )
            if not stat.S_ISDIR(mode):
                raise OverlayTransportError(
                    f"overlay transport found a special directory entry: {relative}"
                )
        for name in names:
            path = directory_path / name
            mode = path.lstat().st_mode
            relative = path.relative_to(root).as_posix()
            if not stat.S_ISREG(mode):
                raise OverlayTransportError(
                    f"overlay transport accepts only regular files: {relative}"
                )
            if mode & 0o7000:
                raise OverlayTransportError(
                    f"overlay transport refuses setuid/setgid/sticky file mode: {relative}"
                )
            files[relative] = path
    return files


def create_metadata(
    overlay: str | Path,
    metadata: str | Path,
    architecture: str,
) -> None:
    root = Path(overlay).expanduser().resolve()
    report = inspect_overlay(root, expected_arch=architecture)
    if not report.ok:
        raise OverlayTransportError("; ".join(report.errors))
    files = _regular_files(root)
    if not files:
        raise OverlayTransportError("overlay is empty")
    payload = {
        "schema_version": 1,
        "architecture": architecture,
        "files": [
            {
                "path": relative,
                "sha256": _sha256(path),
                "executable": bool(path.stat().st_mode & 0o111),
            }
            for relative, path in sorted(files.items())
        ],
    }
    destination = Path(metadata).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def restore_overlay(
    artifact_root: str | Path,
    destination: str | Path,
    architecture: str,
) -> None:
    root = Path(artifact_root).expanduser().resolve()
    source = root / "overlay"
    metadata_path = root / "overlay-metadata.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OverlayTransportError(f"cannot read overlay metadata: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("architecture") != architecture
        or not isinstance(payload.get("files"), list)
    ):
        raise OverlayTransportError("overlay metadata schema or architecture is invalid")

    records: dict[str, dict[str, object]] = {}
    for item in payload["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise OverlayTransportError("overlay metadata contains an invalid file record")
        relative = _relative_path(item["path"]).as_posix()
        if relative in records:
            raise OverlayTransportError(f"overlay metadata repeats path: {relative}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise OverlayTransportError(f"overlay metadata has an invalid digest: {relative}")
        if not isinstance(item.get("executable"), bool):
            raise OverlayTransportError(f"overlay metadata has an invalid mode: {relative}")
        records[relative] = item

    actual = _regular_files(source)
    if set(actual) != set(records):
        missing = sorted(set(records) - set(actual))
        extra = sorted(set(actual) - set(records))
        raise OverlayTransportError(
            f"overlay file list mismatch; missing={missing}, extra={extra}"
        )

    target = Path(destination).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise OverlayTransportError(f"overlay destination is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    for relative, item in sorted(records.items()):
        source_path = actual[relative]
        if _sha256(source_path) != item["sha256"]:
            raise OverlayTransportError(f"overlay digest mismatch: {relative}")
        target_path = target.joinpath(*PurePosixPath(relative).parts)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
        target_path.chmod(0o755 if item["executable"] else 0o644)

    restored = inspect_overlay(target, expected_arch=architecture)
    if not restored.ok:
        raise OverlayTransportError("; ".join(restored.errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--overlay", required=True)
    create.add_argument("--metadata", required=True)
    create.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    restore = commands.add_parser("restore")
    restore.add_argument("--artifact-root", required=True)
    restore.add_argument("--destination", required=True)
    restore.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            create_metadata(args.overlay, args.metadata, args.arch)
        else:
            restore_overlay(args.artifact_root, args.destination, args.arch)
    except OverlayTransportError as exc:
        parser.exit(1, f"overlay transport failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
