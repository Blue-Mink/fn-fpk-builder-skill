"""Project and final FPK validation with archive and architecture defenses."""

from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import re
import stat
import struct
import tarfile
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

from .binary import BinaryInfo, detect_binary
from .manifest import ManifestDocument, parse_manifest, parse_manifest_text, validate_manifest
from .report import Report


MAX_METADATA_SIZE = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_DECLARED_UNPACKED_SIZE = 64 * 1024 * 1024 * 1024
MAX_APP_TGZ_SIZE = 16 * 1024 * 1024 * 1024
MAX_SINGLE_MEMBER_SIZE = 16 * 1024 * 1024 * 1024
SECRET_SCAN_CHUNK_SIZE = 64 * 1024
SECRET_SCAN_OVERLAP = 256
FORBIDDEN_NAMES = {
    ".ds_store",
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "secrets.json",
    "thumbs.db",
}
FORBIDDEN_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".mobileprovision",
}
VCS_PARTS = {".git", ".hg", ".svn", "__macosx"}
SECRET_PATTERNS = (
    (
        "private-key material",
        re.compile(
            rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"
        ),
    ),
    ("AWS access key", re.compile(rb"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])")),
    (
        "GitHub token",
        re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"),
    ),
    (
        "registry authentication token",
        re.compile(rb"(?im)^\s*(?:_authToken|npmAuthToken)\s*[:=]\s*[^\s#]{12,}"),
    ),
)
JSON_OUTER_PATHS = {
    "config/privilege",
    "config/resource",
    "wizard/install",
    "wizard/upgrade",
    "wizard/uninstall",
    "wizard/config",
}
JSON_INNER_PATHS = {"ui/config"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}
MAX_ICON_SIZE = 1024 * 1024


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_archive_name(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return posixpath.normpath(name)


def _path_issue(name: str) -> str | None:
    if "\x00" in name:
        return "contains NUL"
    if "\\" in name:
        return "contains a backslash instead of a POSIX path separator"
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        return "contains a control character"
    path = PurePosixPath(name)
    if path.is_absolute() or name.startswith("/"):
        return "is absolute"
    if any(part == ".." for part in path.parts):
        return "contains parent traversal"
    normalized = _clean_archive_name(name)
    if normalized in {"", ".", ".."}:
        return None
    if normalized.startswith("../"):
        return "escapes the archive root"
    return None


def _link_issue(name: str, target: str) -> str | None:
    if not target:
        return "has an empty link target"
    if PurePosixPath(target).is_absolute():
        return "targets an absolute path"
    parent = posixpath.dirname(_clean_archive_name(name))
    resolved = posixpath.normpath(posixpath.join(parent, target))
    if resolved == ".." or resolved.startswith("../"):
        return "escapes the archive root"
    return None


def _hardlink_issue(target: str) -> str | None:
    if not target:
        return "has an empty link target"
    issue = _path_issue(target)
    if issue:
        return issue
    normalized = _clean_archive_name(target)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        return "escapes the archive root"
    return None


def _sensitive_issue(path: str) -> str | None:
    parts = [item.lower() for item in PurePosixPath(path).parts]
    if any(item in VCS_PARTS for item in parts):
        return "contains VCS or host metadata"
    basename = parts[-1] if parts else ""
    if basename in FORBIDDEN_NAMES:
        return "contains forbidden local or credential metadata"
    if (
        basename.startswith(".env.")
        and basename not in {".env.example", ".env.sample", ".env.template"}
    ):
        return "contains a local environment file"
    if any(basename.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return "contains a private credential file"
    if basename.endswith((".swp", ".swo", ".core")) or basename == "core":
        return "contains editor or crash metadata"
    return None


def _scan_secret_patterns(
    path: str,
    data: bytes,
    report: Report,
    reported: set[str] | None = None,
) -> None:
    for label, pattern in SECRET_PATTERNS:
        if (reported is None or label not in reported) and pattern.search(data):
            report.error(f"{path}: detected {label}; content was not displayed")
            if reported is not None:
                reported.add(label)


def _scan_secret_stream(path: str, stream: BinaryIO, report: Report) -> bytes:
    """Scan a complete stream while retaining only a small overlap and binary header."""
    header = bytearray()
    overlap = b""
    reported: set[str] = set()
    while True:
        chunk = stream.read(SECRET_SCAN_CHUNK_SIZE)
        if not chunk:
            break
        if len(header) < 4096:
            header.extend(chunk[: 4096 - len(header)])
        window = overlap + chunk
        _scan_secret_patterns(path, window, report, reported)
        overlap = window[-SECRET_SCAN_OVERLAP:]
    return bytes(header)


def _decode_json(data: bytes, label: str, report: Report) -> object | None:
    if len(data) > MAX_METADATA_SIZE:
        report.error(f"{label} exceeds the {MAX_METADATA_SIZE}-byte metadata limit")
        return None
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.error(f"{label} is not valid UTF-8 JSON: {exc}")
        return None


def _read_limited(stream: BinaryIO, label: str, report: Report) -> bytes | None:
    data = stream.read(MAX_METADATA_SIZE + 1)
    if len(data) > MAX_METADATA_SIZE:
        report.error(f"{label} exceeds the {MAX_METADATA_SIZE}-byte metadata limit")
        return None
    return data


def _record_binary(
    report: Report,
    path: str,
    header: bytes,
    found: list[dict[str, object]],
    *,
    executable: bool | None = None,
) -> None:
    info = detect_binary(header)
    if info is None:
        return
    item: dict[str, object] = {"path": path, "format": info.format}
    if info.architecture:
        item["architecture"] = info.architecture
    if info.machine is not None:
        item["machine"] = info.machine
    if info.elf_type is not None:
        item["elf_type"] = info.elf_type
    if executable is not None:
        item["executable"] = executable
    found.append(item)
    if info.format in {"mach-o", "pe"}:
        report.error(f"{path}: {info.format} binary cannot run on fnOS")
    elif info.format == "elf" and info.architecture == "unknown":
        report.error(f"{path}: unsupported ELF e_machine={info.machine}")
    elif (
        info.format == "elf"
        and executable is False
        and "bin" in PurePosixPath(path).parts
    ):
        report.error(f"{path}: ELF file below a bin directory is not executable")


def _validate_architecture(
    report: Report,
    document: ManifestDocument,
    binaries: list[dict[str, object]],
    expected_arch: str | None,
) -> None:
    values = document.values
    platform = values.get("platform")
    elf_arches = {
        str(item["architecture"])
        for item in binaries
        if item.get("format") == "elf" and item.get("architecture")
    }
    if platform == "all" and elf_arches:
        report.error(
            "manifest platform=all is invalid because the payload contains native ELF files"
        )
    if len(elf_arches) > 1:
        report.error(f"payload mixes ELF architectures: {', '.join(sorted(elf_arches))}")

    manifest_expected = {"x86": "amd64", "arm": "arm64"}.get(platform or "")
    effective_expected = expected_arch or manifest_expected
    if expected_arch == "all" and elf_arches:
        report.error("target architecture all cannot contain native ELF files")
    if expected_arch == "amd64" and platform not in {"x86", "all", None}:
        report.error(f"target amd64 requires manifest platform=x86 or all, got {platform}")
    if expected_arch == "arm64" and platform not in {"arm", "all", None}:
        report.error(f"target arm64 requires manifest platform=arm or all, got {platform}")
    if expected_arch == "all" and platform not in {"all", None}:
        report.error(f"target all requires manifest platform=all, got {platform}")

    if effective_expected in {"amd64", "arm64"}:
        wrong = sorted(elf_arches - {effective_expected})
        if wrong:
            report.error(
                f"payload ELF architecture {', '.join(wrong)} does not match {effective_expected}"
            )
    if "x86" in elf_arches:
        report.error("32-bit x86 ELF is not accepted for an amd64 FPK")
    if "arm" in elf_arches:
        report.error("32-bit ARM ELF is not accepted for an arm64 FPK")

    report.details["native_binaries"] = binaries
    report.details["detected_elf_architectures"] = sorted(elf_arches)


def _validate_privilege(data: object, report: Report) -> None:
    if not isinstance(data, dict):
        report.error("config/privilege must contain a JSON object")
        return
    defaults = data.get("defaults")
    if not isinstance(defaults, dict):
        report.error("config/privilege.defaults must be an object")
        return
    run_as = defaults.get("run-as")
    if run_as not in {"root", "package"}:
        report.error("config/privilege defaults.run-as must be root or package")
    if run_as == "root":
        report.warn("application requests root execution; review the least-privilege justification")
    if run_as == "package":
        if not data.get("username") or not data.get("groupname"):
            report.error("package privilege requires non-empty username and groupname")


def _validate_json_by_path(path: str, data: bytes, report: Report) -> None:
    parsed = _decode_json(data, path, report)
    if parsed is None:
        return
    if path == "config/privilege":
        _validate_privilege(parsed, report)
    elif path == "config/resource" and not isinstance(parsed, dict):
        report.error("config/resource must contain a JSON object")
    elif path.startswith("wizard/") and not isinstance(parsed, list):
        report.error(f"{path} must contain a JSON array")
    elif path.startswith("app/") and path.endswith("/config") and not isinstance(parsed, dict):
        report.error(f"{path} must contain a JSON object")


def _image_dimensions(data: bytes) -> tuple[str, int, int] | None:
    if data.startswith(PNG_SIGNATURE):
        offset = len(PNG_SIGNATURE)
        dimensions: tuple[int, int] | None = None
        first_chunk = True
        while offset + 12 <= len(data):
            length = struct.unpack(">I", data[offset : offset + 4])[0]
            kind = data[offset + 4 : offset + 8]
            end = offset + 12 + length
            if end > len(data):
                return None
            payload = data[offset + 8 : offset + 8 + length]
            expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
            if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
                return None
            if first_chunk:
                if kind != b"IHDR" or length != 13:
                    return None
                width, height = struct.unpack(">II", payload[:8])
                if not width or not height:
                    return None
                dimensions = (width, height)
                first_chunk = False
            if kind == b"IEND":
                if length != 0 or dimensions is None or end != len(data):
                    return None
                return "PNG", dimensions[0], dimensions[1]
            offset = end
        return None
    if not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in JPEG_SOF_MARKERS and segment_length >= 7:
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return "JPEG", width, height
        offset += segment_length
    return None


def _validate_icon(name: str, data: bytes, report: Report) -> None:
    if len(data) > MAX_ICON_SIZE:
        report.error(f"{name} exceeds the {MAX_ICON_SIZE}-byte icon limit")
    dimensions = _image_dimensions(data)
    if dimensions is None:
        report.error(f"{name} is not a readable PNG or JPEG image")
        return
    image_format, width, height = dimensions
    expected = 256 if name == "ICON_256.PNG" else 64
    if (width, height) != (expected, expected):
        report.error(
            f"{name} must be {expected}x{expected}, got {width}x{height} {image_format}"
        )


def _validate_project_paths(project: Path, report: Report) -> None:
    required_files = (
        "manifest",
        "config/privilege",
        "config/resource",
        "cmd/main",
        "cmd/install_init",
        "cmd/install_callback",
        "cmd/upgrade_init",
        "cmd/upgrade_callback",
        "cmd/uninstall_init",
        "cmd/uninstall_callback",
        "cmd/config_init",
        "cmd/config_callback",
        "ICON.PNG",
        "ICON_256.PNG",
    )
    required_dirs = ("app", "cmd", "wizard")
    for relative in required_files:
        if not (project / relative).is_file():
            report.error(f"missing required file: {relative}")
    for relative in required_dirs:
        if not (project / relative).is_dir():
            report.error(f"missing required directory: {relative}/")


def _walk_project(project: Path, report: Report) -> list[dict[str, object]]:
    binaries: list[dict[str, object]] = []
    for root, directories, files in os.walk(project, followlinks=False):
        root_path = Path(root)
        ignored_directories = [
            name for name in directories if name.lower() in VCS_PARTS or name == "__pycache__"
        ]
        for name in ignored_directories:
            report.warn(
                f"{(root_path / name).relative_to(project).as_posix()}/ will be excluded from staging"
            )
            directories.remove(name)
        for name in list(directories) + files:
            path = root_path / name
            relative = path.relative_to(project).as_posix()
            issue = _sensitive_issue(relative)
            if issue:
                if path.name.lower() == ".ds_store":
                    report.warn(f"{relative}: {issue}; it will be excluded from staging")
                else:
                    report.error(f"{relative}: {issue}")
            if path.is_symlink():
                target = os.readlink(path)
                issue = _link_issue(relative, target)
                if issue:
                    report.error(f"{relative}: symlink {issue}")
                continue
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                report.error(f"cannot inspect {relative}: {exc}")
                continue
            if not stat.S_ISREG(mode) and not stat.S_ISDIR(mode):
                report.error(f"{relative}: project contains a forbidden special filesystem node")
                continue
            if not stat.S_ISREG(mode):
                continue
            try:
                with path.open("rb") as stream:
                    header = _scan_secret_stream(relative, stream, report)
            except OSError as exc:
                report.error(f"cannot scan {relative}: {exc}")
                continue
            if relative in JSON_OUTER_PATHS or relative == "app/ui/config":
                try:
                    _validate_json_by_path(relative, path.read_bytes(), report)
                except OSError as exc:
                    report.error(f"cannot read {relative}: {exc}")
            if relative in {"ICON.PNG", "ICON_256.PNG"}:
                try:
                    _validate_icon(relative, path.read_bytes(), report)
                except OSError as exc:
                    report.error(f"cannot read {relative}: {exc}")
            if relative.startswith("cmd/") and path.stat().st_mode & 0o111 == 0:
                report.error(f"{relative} is not executable")
            _record_binary(
                report,
                relative,
                header[:4096],
                binaries,
                executable=bool(mode & 0o111),
            )
    return binaries


def inspect_project(
    project_path: str | Path,
    *,
    expected_arch: str | None = None,
) -> Report:
    project = Path(project_path).expanduser().resolve()
    report = Report()
    report.details["kind"] = "project"
    report.details["project"] = str(project)
    if not project.is_dir():
        report.error(f"project directory does not exist: {project}")
        return report

    _validate_project_paths(project, report)
    manifest_path = project / "manifest"
    if not manifest_path.is_file():
        return report
    document = parse_manifest(manifest_path)
    errors, warnings = validate_manifest(document)
    for item in errors:
        report.error(item)
    for item in warnings:
        report.warn(item)
    report.details["manifest"] = document.values

    ui_dir = document.values.get("desktop_uidir") or "ui"
    if document.values.get("desktop_applaunchname") and not (project / "app" / ui_dir).is_dir():
        report.error(f"manifest desktop_uidir does not exist under app/: {ui_dir}")
    ui_config = project / "app" / ui_dir / "config"
    if ui_config.is_file():
        try:
            _validate_json_by_path(
                f"app/{ui_dir}/config",
                ui_config.read_bytes(),
                report,
            )
        except OSError as exc:
            report.error(f"cannot read app/{ui_dir}/config: {exc}")

    binaries = _walk_project(project, report)
    _validate_architecture(report, document, binaries, expected_arch)
    return report


def inspect_overlay(
    overlay_path: str | Path,
    *,
    expected_arch: str,
) -> Report:
    overlay = Path(overlay_path).expanduser().resolve()
    report = Report()
    report.details["kind"] = "overlay"
    report.details["overlay"] = str(overlay)
    if expected_arch not in {"amd64", "arm64"}:
        report.error(f"overlay target must be amd64 or arm64, got {expected_arch}")
        return report
    if not overlay.is_dir():
        report.error(f"overlay directory does not exist: {overlay}")
        return report
    binaries = _walk_project(overlay, report)
    platform = {"amd64": "x86", "arm64": "arm"}[expected_arch]
    document = parse_manifest_text(f"platform={platform}\n")
    _validate_architecture(report, document, binaries, expected_arch)
    return report


def _audit_tar_members(
    archive: tarfile.TarFile,
    report: Report,
    *,
    inner: bool,
) -> tuple[dict[str, tarfile.TarInfo], list[dict[str, object]]]:
    members: dict[str, tarfile.TarInfo] = {}
    binaries: list[dict[str, object]] = []
    declared_size = 0
    for index, member in enumerate(archive):
        if index >= MAX_ARCHIVE_MEMBERS:
            report.error(
                f"archive exceeds the {MAX_ARCHIVE_MEMBERS}-member safety limit"
            )
            break
        declared_size += max(0, member.size)
        if member.size > MAX_SINGLE_MEMBER_SIZE:
            report.error(
                f"{member.name}: declared size exceeds the "
                f"{MAX_SINGLE_MEMBER_SIZE}-byte per-member safety limit"
            )
        if declared_size > MAX_DECLARED_UNPACKED_SIZE:
            report.error(
                "archive declared content exceeds the "
                f"{MAX_DECLARED_UNPACKED_SIZE}-byte safety limit"
            )
            break
        original = member.name
        issue = _path_issue(original)
        if issue:
            report.error(f"archive member {original!r} {issue}")
            continue
        name = _clean_archive_name(original)
        if name in members:
            report.error(f"archive contains duplicate member: {name}")
        members[name] = member
        sensitive = _sensitive_issue(name)
        if sensitive:
            report.error(f"{name}: {sensitive}")
        if member.issym():
            issue = _link_issue(name, member.linkname)
            if issue:
                report.error(f"{name}: archive link {issue}")
        elif member.islnk():
            issue = _hardlink_issue(member.linkname)
            if issue:
                report.error(f"{name}: archive hardlink {issue}")
        if not (
            member.isfile()
            or member.isdir()
            or member.issym()
            or member.islnk()
        ):
            report.error(
                f"{name}: archive contains a forbidden special filesystem node "
                f"(type={member.type!r})"
            )
        if not member.isfile():
            continue
        stream = archive.extractfile(member)
        if stream is None:
            report.error(f"cannot read archive member: {name}")
            continue
        check_json = name in (JSON_INNER_PATHS if inner else JSON_OUTER_PATHS)
        check_png = not inner and name in {"ICON.PNG", "ICON_256.PNG"}
        if check_json or check_png:
            data = _read_limited(stream, name, report)
            if data is None:
                continue
            _scan_secret_patterns(f"app/{name}" if inner else name, data, report)
            _record_binary(
                report,
                f"app/{name}" if inner else name,
                data[:4096],
                binaries,
                executable=bool(member.mode & 0o111),
            )
            if check_json:
                _validate_json_by_path(
                    f"app/{name}" if inner and name == "ui/config" else name,
                    data,
                    report,
                )
            if check_png:
                _validate_icon(name, data, report)
        elif inner:
            header = _scan_secret_stream(f"app/{name}", stream, report)
            _record_binary(
                report,
                f"app/{name}",
                header,
                binaries,
                executable=bool(member.mode & 0o111),
            )
        elif name != "app.tgz":
            header = _scan_secret_stream(name, stream, report)
            _record_binary(
                report,
                name,
                header,
                binaries,
                executable=bool(member.mode & 0o111),
            )
        if not inner and name.startswith("cmd/") and member.mode & 0o111 == 0:
            report.error(f"{name} is not executable")
    for name, member in members.items():
        if not member.islnk() or _hardlink_issue(member.linkname):
            continue
        target = _clean_archive_name(member.linkname)
        target_member = members.get(target)
        if target_member is None:
            report.error(f"{name}: archive hardlink target does not exist: {target}")
        elif not target_member.isfile():
            report.error(f"{name}: archive hardlink target is not a regular file: {target}")
    return members, binaries


def inspect_fpk(
    fpk_path: str | Path,
    *,
    expected_arch: str | None = None,
) -> Report:
    path = Path(fpk_path).expanduser().resolve()
    report = Report()
    report.details["kind"] = "fpk"
    report.details["fpk"] = str(path)
    if not path.is_file():
        report.error(f"FPK does not exist: {path}")
        return report

    digest = sha256_file(path)
    report.artifact(path, kind="fpk", sha256=digest)
    try:
        outer = tarfile.open(path, mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        report.error(f"cannot open FPK tar archive: {exc}")
        return report

    with outer:
        members, outer_binaries = _audit_tar_members(outer, report, inner=False)
        report.details["outer_member_count"] = len(members)
        for required in (
            "manifest",
            "app.tgz",
            "config/privilege",
            "config/resource",
            "cmd/main",
            "cmd/install_init",
            "cmd/install_callback",
            "cmd/upgrade_init",
            "cmd/upgrade_callback",
            "cmd/uninstall_init",
            "cmd/uninstall_callback",
            "cmd/config_init",
            "cmd/config_callback",
            "ICON.PNG",
            "ICON_256.PNG",
        ):
            if required not in members:
                report.error(f"FPK missing required member: {required}")
        manifest_member = members.get("manifest")
        app_member = members.get("app.tgz")
        if not manifest_member or not manifest_member.isfile():
            return report
        manifest_stream = outer.extractfile(manifest_member)
        if manifest_stream is None:
            report.error("cannot read FPK manifest")
            return report
        manifest_data = _read_limited(manifest_stream, "manifest", report)
        if manifest_data is None:
            return report
        try:
            document = parse_manifest_text(manifest_data.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            report.error(f"manifest is not UTF-8: {exc}")
            return report
        errors, warnings = validate_manifest(document)
        for item in errors:
            report.error(item)
        for item in warnings:
            report.warn(item)
        report.details["manifest"] = document.values

        if not app_member or not app_member.isfile():
            return report
        if app_member.size > MAX_APP_TGZ_SIZE:
            report.error(
                f"app.tgz exceeds the {MAX_APP_TGZ_SIZE}-byte safety limit"
            )
            return report
        app_stream = outer.extractfile(app_member)
        if app_stream is None:
            report.error("cannot read app.tgz")
            return report
        md5 = hashlib.md5()  # nosec B324 - fnOS manifest format requires MD5.
        with tempfile.NamedTemporaryFile(prefix="fn-fpk-app-", suffix=".tgz") as temp:
            for chunk in iter(lambda: app_stream.read(1024 * 1024), b""):
                md5.update(chunk)
                temp.write(chunk)
            temp.flush()
            actual_md5 = md5.hexdigest()
            report.details["app_tgz_md5"] = actual_md5
            expected_md5 = document.values.get("checksum")
            if not expected_md5:
                report.error("manifest missing fnpack-generated checksum")
            elif expected_md5.lower() != actual_md5:
                report.error(
                    f"manifest checksum mismatch: expected {expected_md5}, got {actual_md5}"
                )
            try:
                with tarfile.open(temp.name, mode="r:*") as inner_tar:
                    inner_members, inner_binaries = _audit_tar_members(
                        inner_tar,
                        report,
                        inner=True,
                    )
                    report.details["inner_member_count"] = len(inner_members)
                    ui_dir = document.values.get("desktop_uidir") or "ui"
                    ui_path = f"{ui_dir}/config"
                    ui_member = inner_members.get(ui_path)
                    if ui_member and ui_member.isfile() and ui_path not in JSON_INNER_PATHS:
                        ui_stream = inner_tar.extractfile(ui_member)
                        if ui_stream is not None:
                            ui_data = _read_limited(ui_stream, f"app/{ui_path}", report)
                            if ui_data is not None:
                                _validate_json_by_path(
                                    f"app/{ui_path}",
                                    ui_data,
                                    report,
                                )
            except tarfile.TarError as exc:
                report.error(f"app.tgz is not a valid tar archive: {exc}")
                inner_binaries = []
        _validate_architecture(
            report,
            document,
            [*outer_binaries, *inner_binaries],
            expected_arch,
        )
    return report


def read_fpk_manifest(fpk_path: str | Path) -> ManifestDocument:
    """Read only a final FPK manifest for artifact selection."""
    return parse_manifest_text(read_fpk_manifest_bytes(fpk_path).decode("utf-8-sig"))


def read_fpk_manifest_bytes(fpk_path: str | Path) -> bytes:
    """Read exact manifest bytes after enforcing one safe, bounded member."""
    with tarfile.open(Path(fpk_path), mode="r:*") as archive:
        members: list[tarfile.TarInfo] = []
        for index, item in enumerate(archive):
            if index >= MAX_ARCHIVE_MEMBERS:
                raise ValueError("FPK exceeds the archive member safety limit")
            if _path_issue(item.name) is None and _clean_archive_name(item.name) == "manifest":
                members.append(item)
        if len(members) != 1:
            raise ValueError(f"FPK must contain exactly one manifest, found {len(members)}")
        member = members[0]
        if not member.isfile() or member.size > MAX_METADATA_SIZE:
            raise ValueError("FPK has no readable manifest")
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError("FPK manifest cannot be read")
        return stream.read()


def build_payload_checksum_manifest(fpk_path: str | Path) -> tuple[bytes, int]:
    """Build a GNU sha256sum-compatible list for every installed regular payload file."""
    path = Path(fpk_path).expanduser().resolve()
    inspected = inspect_fpk(path)
    if not inspected.ok:
        raise ValueError("cannot build payload checksums for an invalid FPK")
    with tarfile.open(path, mode="r:*") as outer:
        app_members = [
            item
            for item in outer
            if _path_issue(item.name) is None
            and _clean_archive_name(item.name) == "app.tgz"
        ]
        if len(app_members) != 1 or not app_members[0].isfile():
            raise ValueError("FPK must contain exactly one regular app.tgz")
        app_stream = outer.extractfile(app_members[0])
        if app_stream is None:
            raise ValueError("FPK app.tgz cannot be read")
        with tempfile.NamedTemporaryFile(prefix="fn-fpk-checks-", suffix=".tgz") as temp:
            for chunk in iter(lambda: app_stream.read(1024 * 1024), b""):
                temp.write(chunk)
            temp.flush()
            with tarfile.open(temp.name, mode="r:*") as inner:
                report = Report()
                members, _ = _audit_tar_members(inner, report, inner=True)
                if not report.ok:
                    raise ValueError("; ".join(report.errors))
                hashes: dict[str, str] = {}
                for name, member in members.items():
                    if not member.isfile():
                        continue
                    stream = inner.extractfile(member)
                    if stream is None:
                        raise ValueError(f"cannot read payload member: {name}")
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                    hashes[name] = digest.hexdigest()
                for name, member in members.items():
                    if member.islnk():
                        target = _clean_archive_name(member.linkname)
                        if target not in hashes:
                            raise ValueError(
                                f"payload hardlink target has no regular-file digest: {target}"
                            )
                        hashes[name] = hashes[target]
    lines = [f"{digest}  ./{name}\n" for name, digest in sorted(hashes.items())]
    return "".join(lines).encode("utf-8"), len(lines)
