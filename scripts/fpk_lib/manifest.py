"""Parser and conservative editor for fnOS manifest files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
APPNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9A-Za-z-]+)+(?:[+._-][0-9A-Za-z.-]+)?$")
DEPENDENCY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BOOLEAN_FIELDS = (
    "ctl_stop",
    "checkport",
    "disable_authorization_path",
)

REQUIRED_FIELDS = (
    "appname",
    "version",
    "display_name",
    "desc",
    "maintainer",
    "platform",
)


@dataclass(frozen=True)
class ManifestEntry:
    key: str
    value: str
    start_line: int
    end_line: int


@dataclass
class ManifestDocument:
    text: str
    entries: list[ManifestEntry]
    errors: list[str]

    @property
    def values(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for entry in self.entries:
            result[entry.key] = entry.value
        return result

    @property
    def duplicates(self) -> list[str]:
        seen: set[str] = set()
        duplicate: set[str] = set()
        for entry in self.entries:
            if entry.key in seen:
                duplicate.add(entry.key)
            seen.add(entry.key)
        return sorted(duplicate)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 6 and value[:3] in {'"""', "'''"} and value[-3:] == value[:3]:
        return value[3:-3]
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        return value[1:-1]
    return value


def parse_manifest_text(text: str) -> ManifestDocument:
    lines = text.splitlines()
    entries: list[ManifestEntry] = []
    errors: list[str] = []
    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            index += 1
            continue
        match = ASSIGN_RE.match(raw)
        if not match:
            errors.append(f"line {index + 1}: expected key=value")
            index += 1
            continue
        key, raw_value = match.groups()
        start = index
        value = raw_value.strip()
        if value.startswith(('"""', "'''")):
            quote = value[:3]
            remainder = value[3:]
            chunks: list[str] = []
            if quote in remainder:
                before, _, after = remainder.partition(quote)
                if after.strip():
                    errors.append(f"line {index + 1}: content follows closing triple quote")
                chunks.append(before)
            else:
                chunks.append(remainder)
                index += 1
                closed = False
                while index < len(lines):
                    current = lines[index]
                    if quote in current:
                        before, _, after = current.partition(quote)
                        chunks.append(before)
                        if after.strip():
                            errors.append(
                                f"line {index + 1}: content follows closing triple quote"
                            )
                        closed = True
                        break
                    chunks.append(current)
                    index += 1
                if not closed:
                    errors.append(f"line {start + 1}: unterminated triple-quoted value")
            value = "\n".join(chunks)
        else:
            value = _unquote(value)
        entries.append(ManifestEntry(key, value, start, index))
        index += 1
    return ManifestDocument(text=text, entries=entries, errors=errors)


def parse_manifest(path: str | Path) -> ManifestDocument:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = source.read_text(encoding="utf-8-sig")
    return parse_manifest_text(text)


def validate_manifest(document: ManifestDocument) -> tuple[list[str], list[str]]:
    errors = list(document.errors)
    warnings: list[str] = []
    values = document.values

    for key in document.duplicates:
        errors.append(f"manifest contains duplicate field: {key}")
    for key in REQUIRED_FIELDS:
        if not values.get(key, "").strip():
            errors.append(f"manifest missing required field: {key}")

    appname = values.get("appname", "")
    if appname and not APPNAME_RE.fullmatch(appname):
        errors.append(
            "manifest appname must be 3-32 safe identifier characters "
            "(observed appcenter constraint)"
        )
    version = values.get("version", "")
    if version and not VERSION_RE.fullmatch(version):
        errors.append(f"manifest version has an unsupported format: {version}")
    platform = values.get("platform")
    if platform and platform not in {"x86", "arm", "all"}:
        errors.append(f"manifest platform must be x86, arm, or all (got {platform})")

    for field in ("os_min_version", "os_max_version"):
        value = values.get(field)
        if value and not VERSION_RE.fullmatch(value):
            errors.append(f"manifest {field} has an unsupported version format: {value}")
    minimum = values.get("os_min_version")
    maximum = values.get("os_max_version")
    if minimum and maximum and VERSION_RE.fullmatch(minimum) and VERSION_RE.fullmatch(maximum):
        minimum_core = _numeric_version_core(minimum)
        maximum_core = _numeric_version_core(maximum)
        if (
            minimum_core is not None
            and maximum_core is not None
            and _compare_numeric_versions(minimum_core, maximum_core) > 0
        ):
            errors.append(
                "manifest os_min_version must not be greater than os_max_version"
            )

    dependencies = values.get("install_dep_apps")
    if dependencies:
        for dependency in dependencies.split(":"):
            if not dependency:
                errors.append(
                    "manifest install_dep_apps contains an empty dependency entry"
                )
                continue
            if dependency.count(">") > 1:
                errors.append(
                    f"manifest install_dep_apps has an invalid dependency: {dependency}"
                )
                continue
            name, separator, required_version = dependency.partition(">")
            if not DEPENDENCY_NAME_RE.fullmatch(name):
                errors.append(
                    f"manifest install_dep_apps has an invalid application name: {name!r}"
                )
            if separator and (
                not required_version or not VERSION_RE.fullmatch(required_version)
            ):
                errors.append(
                    "manifest install_dep_apps has an invalid minimum version "
                    f"for {name!r}: {required_version!r}"
                )

    service_port = values.get("service_port")
    if service_port and (
        not service_port.isdigit() or not 1 <= int(service_port) <= 65535
    ):
        errors.append("manifest service_port must be an integer between 1 and 65535")
    for field in BOOLEAN_FIELDS:
        value = values.get(field)
        if value and value.lower() not in {"true", "false"}:
            errors.append(f"manifest {field} must be true or false")

    install_type = values.get("install_type")
    if install_type not in {None, "", "root"}:
        errors.append("manifest install_type must be empty or root")
    ui_dir = values.get("desktop_uidir")
    if ui_dir:
        ui_path = PurePosixPath(ui_dir)
        if (
            ui_path.is_absolute()
            or "\\" in ui_dir
            or any(part in {"", ".", ".."} for part in ui_path.parts)
        ):
            errors.append(
                "manifest desktop_uidir must be a safe relative path below app/"
            )
    if values.get("arch"):
        warnings.append("manifest arch is deprecated; use platform instead")

    if values.get("source") not in {None, "", "thirdparty"}:
        warnings.append(f"manifest source is unusual: {values['source']}")
    if not values.get("source"):
        warnings.append("manifest source is not declared; third-party packages normally use thirdparty")
    if install_type == "root":
        warnings.append("manifest install_type=root uses the system partition")
    if values.get("disable_authorization_path", "").lower() == "true":
        warnings.append("manifest hides authorization-path controls")
    return errors, warnings


def _numeric_version_core(value: str) -> tuple[int, ...] | None:
    match = re.match(r"^([0-9]+(?:\.[0-9]+)+)", value)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _compare_numeric_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    normalized_left = left + (0,) * (width - len(left))
    normalized_right = right + (0,) * (width - len(right))
    return (normalized_left > normalized_right) - (normalized_left < normalized_right)


def _format_value(value: str) -> str:
    if "\n" in value:
        return f'"""{value}"""'
    if not value or value != value.strip() or any(char in value for char in "\r\n"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def update_manifest_text(text: str, updates: dict[str, str]) -> str:
    document = parse_manifest_text(text)
    if document.errors:
        raise ValueError("; ".join(document.errors))
    if document.duplicates:
        raise ValueError(f"cannot update duplicate fields: {', '.join(document.duplicates)}")

    lines = text.splitlines(keepends=True)
    positions = {entry.key: entry for entry in document.entries}
    newline = "\r\n" if "\r\n" in text else "\n"

    replacements: list[tuple[int, int, str]] = []
    missing: list[tuple[str, str]] = []
    for key, value in updates.items():
        if not KEY_RE.fullmatch(key):
            raise ValueError(f"invalid manifest key: {key}")
        replacement = f"{key}={_format_value(value)}{newline}"
        if key in positions:
            entry = positions[key]
            replacements.append((entry.start_line, entry.end_line, replacement))
        else:
            missing.append((key, value))

    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start : end + 1] = [replacement]
    if missing:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        for key, value in missing:
            lines.append(f"{key}={_format_value(value)}{newline}")
    return "".join(lines)


def update_manifest_file(path: str | Path, updates: dict[str, str]) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    target.write_text(update_manifest_text(text, updates), encoding="utf-8")
