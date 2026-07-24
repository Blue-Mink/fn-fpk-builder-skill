"""Verified fnpack discovery and installation."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .report import OperationError, Report, UsageError


FNPACK_VERSION = "1.2.3"
BASE_URL = "https://static2.fnnas.com/fnpack"
DOWNLOADS = {
    "windows-amd64": {
        "filename": "fnpack-1.2.3-windows-amd64",
        "sha256": "d7af4bd716b009c58f5bcd931615f39db121e7d4b75dc759e575c4fb2879b6ee",
    },
    "linux-amd64": {
        "filename": "fnpack-1.2.3-linux-amd64",
        "sha256": "54b97fa7b70968c4d05c79840f5daeff508957d0bb2062fdb0376d00d9615c93",
    },
    "linux-arm64": {
        "filename": "fnpack-1.2.3-linux-arm64",
        "sha256": None,
    },
    "darwin-amd64": {
        "filename": "fnpack-1.2.3-darwin-amd64",
        "sha256": "30a9f50a35e8d8d425b687881761478c3c778e9c0da3a1b59f298b666dd7a268",
    },
    "darwin-arm64": {
        "filename": "fnpack-1.2.3-darwin-arm64",
        "sha256": "d40cb00896cb2a5d211357d255750ed0cbe7f2d141df671c2b717afb4e74bf77",
    },
}
VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")


@dataclass(frozen=True)
class FnpackInfo:
    path: Path
    version: str | None
    sha256: str
    verified: bool
    host: str


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def host_key() -> str:
    system = platform.system().lower()
    if system == "macos":
        system = "darwin"
    machine = platform.machine().lower()
    arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)
    return f"{system}-{arch}"


def default_cache_dir() -> Path:
    override = os.environ.get("FNPACK_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "fn-fpk-builder-skill" / "fnpack"


def cached_fnpack_path(cache_dir: str | Path | None = None) -> Path:
    base = Path(cache_dir).expanduser() if cache_dir else default_cache_dir()
    return base / FNPACK_VERSION / host_key() / "fnpack"


def _run_version(path: Path) -> str | None:
    for args in ([str(path), "--version"], [str(path), "version"]):
        try:
            result = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = f"{result.stdout}\n{result.stderr}"
        match = VERSION_RE.search(output)
        if match:
            return match.group(1)
    return None


def inspect_fnpack(path: str | Path) -> FnpackInfo:
    binary = Path(path).expanduser().resolve()
    if not binary.is_file():
        raise UsageError(f"fnpack binary does not exist: {binary}")
    digest = file_sha256(binary)
    key = host_key()
    expected = DOWNLOADS.get(key, {}).get("sha256")
    verified = bool(expected and digest == expected)
    return FnpackInfo(
        path=binary,
        # fnpack 1.2.3 exposes no version command. Its pinned official digest is
        # therefore the authoritative version identity.
        version=FNPACK_VERSION if verified else _run_version(binary),
        sha256=digest,
        verified=verified,
        host=key,
    )


def resolve_fnpack(
    explicit: str | Path | None = None,
    *,
    cache_dir: str | Path | None = None,
    allow_unverified: bool = False,
) -> FnpackInfo:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    elif os.environ.get("FNPACK_BIN"):
        candidates.append(Path(os.environ["FNPACK_BIN"]))
    else:
        candidates.append(cached_fnpack_path(cache_dir))
        located = shutil.which("fnpack")
        if located:
            candidates.append(Path(located))

    existing = next((item for item in candidates if item.expanduser().is_file()), None)
    if existing is None:
        raise UsageError(
            "verified fnpack was not found; run `fpk.py toolchain --install` "
            "or pass --fnpack/FNPACK_BIN"
        )
    info = inspect_fnpack(existing)
    if info.version != FNPACK_VERSION and not allow_unverified:
        raise UsageError(
            f"fnpack {FNPACK_VERSION} is required, got {info.version or 'unknown'} "
            f"at {info.path}; use --allow-unverified-fnpack only for an explicitly trusted tool"
        )
    if not info.verified and not allow_unverified:
        expected = DOWNLOADS.get(info.host, {}).get("sha256")
        if expected:
            raise UsageError(
                f"fnpack SHA-256 mismatch for {info.host}: got {info.sha256}, expected {expected}"
            )
        raise UsageError(
            f"no embedded official checksum is available for {info.host}; "
            "pass an explicitly trusted binary with --allow-unverified-fnpack"
        )
    return info


def install_fnpack(cache_dir: str | Path | None = None) -> FnpackInfo:
    key = host_key()
    if key.startswith("windows-"):
        raise UsageError("Windows is documentation/diagnostics-only for this skill")
    spec = DOWNLOADS.get(key)
    if spec is None:
        raise UsageError(f"fnpack {FNPACK_VERSION} is not supported on host {key}")
    expected = spec.get("sha256")
    if not expected:
        raise UsageError(
            f"the documented {key} fnpack download has no verified available artifact; "
            "provide an explicitly trusted binary instead"
        )
    destination = cached_fnpack_path(cache_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE_URL}/{spec['filename']}"

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            with tempfile.NamedTemporaryFile(
                prefix="fnpack-download-",
                dir=destination.parent,
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                shutil.copyfileobj(response, stream)
    except (OSError, urllib.error.URLError) as exc:
        raise OperationError(f"failed to download {url}: {exc}") from exc

    try:
        actual = file_sha256(temporary)
        if actual != expected:
            raise OperationError(
                f"downloaded fnpack checksum mismatch: got {actual}, expected {expected}"
            )
        temporary.chmod(
            temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return inspect_fnpack(destination)


def toolchain_report(
    *,
    explicit: str | Path | None = None,
    cache_dir: str | Path | None = None,
    install: bool = False,
    allow_unverified: bool = False,
) -> Report:
    report = Report()
    report.details["host"] = host_key()
    report.details["required_version"] = FNPACK_VERSION
    report.details["cache_path"] = str(cached_fnpack_path(cache_dir))
    try:
        info = (
            install_fnpack(cache_dir)
            if install
            else resolve_fnpack(
                explicit,
                cache_dir=cache_dir,
                allow_unverified=allow_unverified,
            )
        )
    except (OperationError, UsageError) as exc:
        report.error(str(exc))
        return report
    report.details["fnpack"] = {
        "path": str(info.path),
        "version": info.version,
        "sha256": info.sha256,
        "verified": info.verified,
    }
    report.artifact(info.path, kind="tool", sha256=info.sha256, version=info.version)
    if not info.verified:
        report.warn("fnpack was accepted explicitly but does not match an embedded official hash")
    return report


def load_provenance(skill_root: str | Path) -> dict[str, object]:
    path = Path(skill_root) / "references" / "provenance.json"
    return json.loads(path.read_text(encoding="utf-8"))
