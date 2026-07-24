"""SSH-based fnOS lifecycle operations with explicit destructive boundaries."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .archive import (
    build_payload_checksum_manifest,
    inspect_fpk,
    read_fpk_manifest,
    read_fpk_manifest_bytes,
    sha256_file,
)
from .manifest import parse_manifest_text
from .report import OperationError, Report, UsageError


APPNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
REMOTE_TEMP_RE = re.compile(r"^/tmp/fn-fpk-builder-[0-9a-f]{12}$")
LOG_SECRET_PATTERNS = (
    re.compile(
        r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
        r"-----END [^-\r\n]*PRIVATE KEY-----",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*\Z",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key)"
        r"\s*[:=]\s*[^\s,;]{4,}"
    ),
)


@dataclass(frozen=True)
class SSHConfig:
    host: str
    port: int = 22
    identity: Path | None = None
    connect_timeout: int = 15

    def __post_init__(self) -> None:
        if (
            not self.host
            or self.host.startswith("-")
            or any(character.isspace() for character in self.host)
            or not re.fullmatch(r"[A-Za-z0-9_.:@\[\]-]+", self.host)
        ):
            raise UsageError(f"unsafe SSH host value: {self.host!r}")
        if self.port < 1 or self.port > 65535:
            raise UsageError("SSH port must be between 1 and 65535")

    def ssh_base(self) -> list[str]:
        args = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-p",
            str(self.port),
        ]
        if self.identity:
            args.extend(["-i", str(self.identity.expanduser().resolve())])
        args.append(self.host)
        return args

    def scp_base(self) -> list[str]:
        args = [
            "scp",
            "-q",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-P",
            str(self.port),
        ]
        if self.identity:
            args.extend(["-i", str(self.identity.expanduser().resolve())])
        return args


@dataclass(frozen=True)
class RemoteResult:
    command: str
    returncode: int
    stdout: str
    stderr: str


def require_local_ssh() -> None:
    if not shutil.which("ssh") or not shutil.which("scp"):
        raise UsageError("ssh and scp are required for fnOS remote operations")


def validate_appname(appname: str) -> None:
    if not APPNAME_RE.fullmatch(appname):
        raise UsageError(f"unsafe or invalid fnOS appname: {appname!r}")


def _appcenter_has_error(result: RemoteResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}".lower()
    return result.returncode != 0 or "[error]" in output


def _status_value(result: RemoteResult) -> str:
    return result.stdout.strip().lower().splitlines()[0] if result.stdout.strip() else ""


def _redact_log_text(value: str) -> tuple[str, int]:
    redacted = value
    count = 0
    for pattern in LOG_SECRET_PATTERNS:
        redacted, replacements = pattern.subn("[REDACTED]", redacted)
        count += replacements
    return redacted, count


def _wait_for_application_status(
    config: SSHConfig,
    appname: str,
    *,
    require_running: bool,
    retries: int,
) -> RemoteResult:
    last_status: RemoteResult | None = None
    attempts = max(1, retries) if require_running else 1
    for attempt in range(attempts):
        last_status = remote_run(config, ["appcenter-cli", "status", appname])
        value = _status_value(last_status)
        healthy = not _appcenter_has_error(last_status) and value != "noinstall"
        if healthy and (not require_running or value == "running"):
            return last_status
        if attempt + 1 < attempts:
            time.sleep(2)
    expectation = "running" if require_running else "an installed state"
    raise OperationError(
        f"application status did not reach {expectation} after {attempts} checks"
    )


def remote_run(
    config: SSHConfig,
    argv: list[str],
    *,
    check: bool = False,
    timeout: int = 120,
) -> RemoteResult:
    require_local_ssh()
    command = shlex.join(argv)
    try:
        process = subprocess.run(
            [*config.ssh_base(), command],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperationError(f"SSH command failed: {exc}") from exc
    result = RemoteResult(
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "no remote error output"
        raise OperationError(f"remote command failed ({command}): {message}")
    return result


def remote_shell(
    config: SSHConfig,
    script: str,
    *,
    check: bool = False,
    timeout: int = 120,
) -> RemoteResult:
    require_local_ssh()
    try:
        process = subprocess.run(
            [*config.ssh_base(), "sh", "-c", shlex.quote(script)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperationError(f"SSH shell command failed: {exc}") from exc
    result = RemoteResult(
        command=script,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "no remote error output"
        raise OperationError(f"remote shell command failed: {message}")
    return result


def remote_copy(config: SSHConfig, local: Path, remote_path: str) -> None:
    require_local_ssh()
    if not local.is_file():
        raise UsageError(f"upload source does not exist: {local}")
    try:
        process = subprocess.run(
            [*config.scp_base(), str(local), f"{config.host}:{remote_path}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperationError(f"SCP upload failed: {exc}") from exc
    if process.returncode != 0:
        raise OperationError(f"SCP upload failed: {process.stderr.strip()}")


def remote_architecture(config: SSHConfig) -> tuple[str, str]:
    result = remote_run(config, ["uname", "-m"], check=True)
    machine = result.stdout.strip()
    architecture = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine)
    if architecture is None:
        raise OperationError(f"unsupported fnOS device architecture: {machine}")
    return architecture, machine


def remote_doctor(config: SSHConfig) -> Report:
    report = Report()
    try:
        architecture, machine = remote_architecture(config)
    except (OperationError, UsageError) as exc:
        report.error(str(exc))
        return report
    report.details["host"] = config.host
    report.details["machine"] = machine
    report.details["architecture"] = architecture
    probes = {
        "kernel": ["uname", "-sr"],
        "os_release": ["sh", "-c", "cat /etc/os-release 2>/dev/null || true"],
        "fnpack_help": ["sh", "-c", "fnpack --help 2>&1 || true"],
        "fnpack_sha256": [
            "sh",
            "-c",
            "p=$(command -v fnpack) && sha256sum \"$p\" 2>/dev/null || true",
        ],
        "appcenter_cli_help": ["sh", "-c", "appcenter-cli --help 2>&1 || true"],
        "appcenter_cli_sha256": [
            "sh",
            "-c",
            "p=$(command -v appcenter-cli) && sha256sum \"$p\" 2>/dev/null || true",
        ],
        "default_volume": [
            "sh",
            "-c",
            "appcenter-cli default-volume 2>/dev/null || true",
        ],
    }
    results: dict[str, object] = {}
    for name, command in probes.items():
        result = remote_run(config, command)
        results[name] = {
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    appcenter = remote_run(config, ["sh", "-c", "command -v appcenter-cli"])
    if appcenter.returncode != 0:
        report.error("appcenter-cli is not available on the fnOS device")
    report.details["probes"] = results
    return report


def app_action(config: SSHConfig, action: str, appname: str) -> Report:
    validate_appname(appname)
    if action not in {"status", "start", "stop", "uninstall"}:
        raise UsageError(f"unsupported appcenter action: {action}")
    result = remote_run(config, ["appcenter-cli", action, appname])
    report = Report()
    report.details.update(
        {
            "host": config.host,
            "appname": appname,
            "action": action,
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    )
    semantic_error = _appcenter_has_error(result)
    if action == "status" and _status_value(result) == "noinstall":
        semantic_error = True
    if semantic_error:
        report.error(
            f"appcenter-cli {action} failed: "
            f"{result.stderr.strip() or result.stdout.strip() or 'no output'}"
        )
    return report


def app_logs(
    config: SSHConfig,
    appname: str,
    *,
    relative_path: str | None = None,
    lines: int = 200,
) -> Report:
    validate_appname(appname)
    if lines < 1 or lines > 10000:
        raise UsageError("--lines must be between 1 and 10000")
    base = f"/var/apps/{appname}/var"
    report = Report()
    resolved_base_result = remote_run(config, ["readlink", "-f", "--", base])
    resolved_base = resolved_base_result.stdout.strip()
    if (
        resolved_base_result.returncode != 0
        or not resolved_base.startswith("/")
        or "\n" in resolved_base
    ):
        report.error(
            f"cannot resolve application log directory: "
            f"{resolved_base_result.stderr.strip() or resolved_base_result.stdout.strip() or 'no output'}"
        )
        return report
    if relative_path:
        candidate = relative_path.strip("/")
        if (
            not candidate
            or PurePosixPath(candidate).is_absolute()
            or ".." in PurePosixPath(candidate).parts
        ):
            raise UsageError("--path must be a relative path below the app var directory")
        target_result = remote_run(
            config,
            ["readlink", "-f", "--", f"{base}/{candidate}"],
        )
        target = target_result.stdout.strip()
        if (
            target_result.returncode != 0
            or not target.startswith(f"{resolved_base}/")
            or "\n" in target
        ):
            report.error("--path resolves outside the application var directory or is unavailable")
            return report
        result = remote_run(config, ["tail", "-n", str(lines), target])
        report.details["path"] = target
    else:
        quoted = shlex.quote(resolved_base)
        discovery = remote_shell(
            config,
            f"find {quoted} -maxdepth 3 -type f -name '*.log' -print 2>/dev/null | "
            "sort | head -n 20",
        )
        files = [
            item
            for item in discovery.stdout.splitlines()
            if item.startswith(f"{resolved_base}/") and "\x00" not in item
        ]
        report.details["discovered"] = files
        if not files:
            report.warn(f"no *.log files found below {base}")
            report.details["logs"] = ""
            return report
        target = files[0]
        result = remote_run(config, ["tail", "-n", str(lines), target])
        report.details["path"] = target
    logs, log_redactions = _redact_log_text(result.stdout)
    stderr, stderr_redactions = _redact_log_text(result.stderr.strip())
    redaction_count = log_redactions + stderr_redactions
    report.details["logs"] = logs
    report.details["stderr"] = stderr
    report.details["redacted_secret_count"] = redaction_count
    if redaction_count:
        report.warn(f"redacted {redaction_count} secret-like value(s) from log output")
    if result.returncode != 0:
        report.error(
            f"cannot read application log: "
            f"{stderr or logs.strip() or 'no output'}"
        )
    return report


def _expand_artifacts(values: list[str | Path]) -> list[Path]:
    artifacts: list[Path] = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            artifacts.extend(sorted(item for item in path.glob("*.fpk") if item.is_file()))
        elif path.is_file():
            artifacts.append(path)
        else:
            raise UsageError(f"artifact path does not exist: {path}")
    unique: list[Path] = []
    for item in artifacts:
        if item not in unique:
            unique.append(item)
    if not unique:
        raise UsageError("no .fpk artifacts were supplied")
    return unique


def select_artifact(
    artifacts: list[str | Path],
    architecture: str,
) -> tuple[Path, dict[str, str], Report]:
    candidates = _expand_artifacts(artifacts)
    validation = Report()
    exact: list[tuple[Path, dict[str, str]]] = []
    neutral: list[tuple[Path, dict[str, str]]] = []
    wanted_platform = {"amd64": "x86", "arm64": "arm"}[architecture]
    for path in candidates:
        inspected = inspect_fpk(path)
        validation.details[path.name] = inspected.as_dict()
        if not inspected.ok:
            validation.error(f"artifact failed validation: {path}")
            continue
        values = read_fpk_manifest(path).values
        if values.get("platform") == wanted_platform:
            exact.append((path, values))
        elif values.get("platform") == "all":
            neutral.append((path, values))
    choices = exact or neutral
    if len(choices) != 1:
        raise UsageError(
            f"expected exactly one compatible {architecture} artifact; found {len(choices)}"
        )
    return choices[0][0], choices[0][1], validation


def _remote_sha256(config: SSHConfig, path: str) -> str:
    result = remote_run(config, ["sha256sum", path], check=True)
    digest = result.stdout.strip().split(maxsplit=1)[0] if result.stdout.strip() else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise OperationError(f"remote sha256sum returned an invalid digest for {path}")
    return digest.lower()


def _verify_installed_manifest(
    config: SSHConfig,
    appname: str,
    expected_version: str | None,
    expected_sha256: str,
) -> dict[str, object]:
    path = f"/var/apps/{appname}/manifest"
    result = remote_run(config, ["cat", path])
    if result.returncode != 0 or _appcenter_has_error(result):
        raise OperationError(
            f"cannot read installed manifest for {appname}: "
            f"{result.stderr.strip() or result.stdout.strip() or 'no output'}"
        )
    if len(result.stdout.encode("utf-8")) > 4 * 1024 * 1024:
        raise OperationError(f"installed manifest for {appname} exceeds the safety limit")
    document = parse_manifest_text(result.stdout)
    if document.errors or document.duplicates:
        issues = [
            *document.errors,
            *[f"duplicate field: {item}" for item in document.duplicates],
        ]
        raise OperationError(f"installed manifest is malformed: {'; '.join(issues)}")
    actual_appname = document.values.get("appname")
    actual_version = document.values.get("version")
    if actual_appname != appname:
        raise OperationError(
            f"installed manifest appname mismatch: expected {appname}, got {actual_appname!r}"
        )
    if expected_version and actual_version != expected_version:
        raise OperationError(
            "installed manifest version mismatch: "
            f"expected {expected_version}, got {actual_version!r}"
        )
    actual_sha256 = _remote_sha256(config, path)
    if actual_sha256 != expected_sha256:
        raise OperationError(
            "installed manifest SHA-256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    return {
        "path": path,
        "appname": actual_appname,
        "version": actual_version or "",
        "sha256": actual_sha256,
        "matches_fpk": True,
    }


def _verify_installed_payload(
    config: SSHConfig,
    appname: str,
    fpk_path: Path,
    remote_dir: str,
) -> dict[str, object]:
    try:
        checksum_data, expected_count = build_payload_checksum_manifest(fpk_path)
    except (OSError, ValueError) as exc:
        raise OperationError(f"cannot prepare installed payload verification: {exc}") from exc
    remote_checks = f"{remote_dir}/payload.sha256"
    with tempfile.NamedTemporaryFile(
        prefix="fn-fpk-payload-",
        suffix=".sha256",
    ) as local_checks:
        local_checks.write(checksum_data)
        local_checks.flush()
        local_path = Path(local_checks.name)
        local_digest = sha256_file(local_path)
        remote_copy(config, local_path, remote_checks)
        remote_run(config, ["chmod", "600", remote_checks], check=True)
        if _remote_sha256(config, remote_checks) != local_digest:
            raise OperationError("uploaded payload checksum manifest does not match locally")

    base = f"/var/apps/{appname}/target"
    quoted_base = shlex.quote(base)
    quoted_checks = shlex.quote(remote_checks)
    verification = remote_shell(
        config,
        "set -eu; "
        f"base={quoted_base}; checks={quoted_checks}; expected={expected_count}; "
        'resolved=$(readlink -f -- "$base"); '
        '[ -n "$resolved" ] && [ -d "$resolved" ] '
        '|| { echo "installed payload directory is missing" >&2; exit 71; }; '
        'actual=$(find "$resolved" -type f -print | wc -l | tr -d " "); '
        '[ "$actual" = "$expected" ] || { '
        'echo "installed payload file count mismatch: expected=$expected actual=$actual" >&2; '
        "exit 72; }; "
        'if [ "$expected" -gt 0 ]; then '
        'cd "$resolved"; sha256sum --check "$checks" >/dev/null; '
        "fi; "
        'printf "verified_regular_files=%s resolved_payload=%s\\n" "$actual" "$resolved"',
        check=True,
        timeout=600,
    )
    return {
        "path": base,
        "expected_regular_files": expected_count,
        "verified_regular_files": expected_count,
        "checksum_manifest_sha256": hashlib.sha256(checksum_data).hexdigest(),
        "remote_output": verification.stdout.strip(),
    }


def _safe_remote_cleanup(config: SSHConfig, directory: str) -> None:
    if not REMOTE_TEMP_RE.fullmatch(directory):
        raise UsageError(f"refusing to clean unsafe remote directory: {directory}")
    remote_run(config, ["rm", "-rf", "--", directory], check=True)


def _install_command(
    remote_fpk: str,
    *,
    env_path: str | None,
    volume: str | None,
) -> list[str]:
    command = ["appcenter-cli", "install-fpk", remote_fpk]
    if env_path:
        command.extend(["--env", env_path])
    if volume:
        command.extend(["--volume", volume])
    return command


def application_installed(config: SSHConfig, appname: str) -> bool:
    result = remote_run(config, ["appcenter-cli", "status", appname])
    if _appcenter_has_error(result):
        raise OperationError(
            f"cannot determine whether {appname} is installed: "
            f"{result.stderr.strip() or result.stdout.strip() or 'no output'}"
        )
    value = result.stdout.strip().lower()
    if value == "noinstall":
        return False
    if value:
        return True
    raise OperationError(f"unexpected appcenter-cli status output for {appname}: {value!r}")


def _resolve_install_volume(
    config: SSHConfig,
    requested: str | None,
) -> tuple[str | None, str]:
    if requested is not None:
        if not requested.isdigit() or int(requested) <= 0:
            raise UsageError("--volume must be a positive fnOS volume index")
        return str(int(requested)), "explicit"

    default = remote_run(config, ["appcenter-cli", "default-volume"])
    value = default.stdout.strip()
    if not _appcenter_has_error(default) and value.isdigit() and int(value) > 0:
        return str(int(value)), "appcenter-default"

    discovered = remote_shell(
        config,
        "for d in /vol[1-9]*/@appcenter; do "
        "[ -d \"$d\" ] || continue; "
        "v=${d#/vol}; v=${v%/@appcenter}; echo \"$v\"; "
        "done",
    )
    candidates = sorted(
        {
            str(int(item.strip()))
            for item in discovered.stdout.splitlines()
            if item.strip().isdigit() and int(item.strip()) > 0
        },
        key=int,
    )
    if len(candidates) == 1:
        return candidates[0], "single-existing-appcenter-volume"
    if not candidates:
        return None, "unavailable"
    raise UsageError(
        "multiple fnOS application volumes are available; select one explicitly with --volume"
    )


def deploy(
    config: SSHConfig,
    artifacts: list[str | Path],
    *,
    clean: bool = False,
    env_file: str | Path | None = None,
    volume: str | None = None,
    rollback_fpk: str | Path | None = None,
    start: bool = True,
    status_retries: int = 10,
) -> Report:
    report = Report()
    architecture, machine = remote_architecture(config)
    selected, manifest, validation = select_artifact(artifacts, architecture)
    report.merge(validation, detail_key="local_validation")
    if not validation.ok:
        report.error("deployment stopped because one or more supplied FPKs failed local validation")
        return report
    appname = manifest.get("appname", "")
    validate_appname(appname)
    local_sha = sha256_file(selected)
    local_manifest_sha = hashlib.sha256(read_fpk_manifest_bytes(selected)).hexdigest()
    remote_dir = f"/tmp/fn-fpk-builder-{uuid.uuid4().hex[:12]}"
    remote_fpk = f"{remote_dir}/package.fpk"
    remote_env: str | None = None
    report.details.update(
        {
            "host": config.host,
            "machine": machine,
            "architecture": architecture,
            "selected_artifact": str(selected),
            "appname": appname,
            "version": manifest.get("version"),
            "fpk_manifest_sha256": local_manifest_sha,
            "clean_install": clean,
        }
    )
    was_installed = application_installed(config, appname)
    effective_volume = volume
    volume_source = "not-required"
    if was_installed and not clean:
        effective_volume = None
        volume_source = "ignored-for-upgrade"
        if volume is not None:
            report.warn("--volume is ignored for an in-place upgrade")
    if clean or not was_installed:
        effective_volume, volume_source = _resolve_install_volume(config, volume)
        if effective_volume is None:
            raise UsageError(
                "new installation requires --volume because no usable default volume was found"
            )
    report.details["previously_installed"] = was_installed
    report.details["install_volume"] = effective_volume
    report.details["install_volume_source"] = volume_source

    rollback_path = Path(rollback_fpk).expanduser().resolve() if rollback_fpk else None
    rollback_remote: str | None = None
    rollback_needed = False
    rollback_manifest_sha: str | None = None
    if rollback_path:
        rollback_report = inspect_fpk(rollback_path, expected_arch=architecture)
        if not rollback_report.ok:
            raise UsageError("rollback FPK failed local validation")
        rollback_manifest = read_fpk_manifest(rollback_path).values
        rollback_manifest_sha = hashlib.sha256(
            read_fpk_manifest_bytes(rollback_path)
        ).hexdigest()
        if rollback_manifest.get("appname") != appname:
            raise UsageError("rollback FPK appname does not match the deployment app")

    try:
        remote_run(config, ["mkdir", "-m", "700", remote_dir], check=True)
        remote_copy(config, selected, remote_fpk)
        remote_sha = _remote_sha256(config, remote_fpk)
        report.details["local_sha256"] = local_sha
        report.details["remote_sha256"] = remote_sha
        if remote_sha != local_sha:
            raise OperationError(
                f"uploaded FPK checksum mismatch: local {local_sha}, remote {remote_sha}"
            )
        if env_file:
            env_path = Path(env_file).expanduser().resolve()
            if not env_path.is_file():
                raise UsageError(f"environment file does not exist: {env_path}")
            remote_env = f"{remote_dir}/install.env"
            remote_copy(config, env_path, remote_env)
            remote_run(config, ["chmod", "600", remote_env], check=True)
            report.details["environment_file_uploaded"] = True

        if rollback_path:
            rollback_candidate = f"{remote_dir}/rollback.fpk"
            remote_copy(config, rollback_path, rollback_candidate)
            if _remote_sha256(config, rollback_candidate) != sha256_file(rollback_path):
                raise OperationError("uploaded rollback FPK checksum mismatch")
            rollback_remote = rollback_candidate

        if clean and was_installed:
            remote_run(config, ["appcenter-cli", "stop", appname])
            removed = remote_run(config, ["appcenter-cli", "uninstall", appname])
            if _appcenter_has_error(removed) and "not installed" not in (
                f"{removed.stdout}\n{removed.stderr}".lower()
            ):
                raise OperationError(
                    "clean deployment could not uninstall the previous application: "
                    f"{removed.stderr.strip() or removed.stdout.strip() or 'no output'}"
                )
            if application_installed(config, appname):
                raise OperationError(
                    "clean deployment stopped because the previous application "
                    "is still installed after uninstall"
                )
            report.details["clean_uninstall_verified"] = True
            rollback_needed = True

        rollback_needed = True
        installed = remote_run(
            config,
            _install_command(remote_fpk, env_path=remote_env, volume=effective_volume),
            timeout=600,
        )
        report.details["install"] = {
            "exit_code": installed.returncode,
            "stdout": installed.stdout.strip(),
            "stderr": installed.stderr.strip(),
        }
        if _appcenter_has_error(installed):
            raise OperationError(
                "appcenter-cli install-fpk failed: "
                f"{installed.stderr.strip() or installed.stdout.strip() or 'no output'}"
            )
        report.details["installed_payload"] = _verify_installed_payload(
            config,
            appname,
            selected,
            remote_dir,
        )
        if start:
            initial_status = remote_run(config, ["appcenter-cli", "status", appname])
            if (
                not _appcenter_has_error(initial_status)
                and _status_value(initial_status) == "running"
            ):
                report.details["start"] = {
                    "skipped": True,
                    "reason": "installation already left the application running",
                }
            else:
                started = remote_run(
                    config,
                    ["appcenter-cli", "start", appname],
                    timeout=180,
                )
                report.details["start"] = {
                    "exit_code": started.returncode,
                    "stdout": started.stdout.strip(),
                    "stderr": started.stderr.strip(),
                }
                if _appcenter_has_error(started):
                    report.warn(
                        "appcenter-cli start reported an error; final status verification "
                        "will determine deployment success"
                    )

        last_status = _wait_for_application_status(
            config,
            appname,
            require_running=start,
            retries=status_retries,
        )
        report.details["status"] = {
            "exit_code": last_status.returncode,
            "stdout": last_status.stdout.strip(),
            "stderr": last_status.stderr.strip(),
        }
        report.details["process_status"] = {
            "source": "appcenter-cli status via the package cmd/main status contract",
            "state": _status_value(last_status),
            "verified_running": start and _status_value(last_status) == "running",
        }
        report.details["installed_manifest"] = _verify_installed_manifest(
            config,
            appname,
            manifest.get("version"),
            local_manifest_sha,
        )
        post_deploy_logs = app_logs(config, appname, lines=100)
        report.merge(post_deploy_logs, detail_key="post_deploy_logs")
        report.artifact(
            selected,
            kind="deployed-fpk",
            sha256=local_sha,
            architecture=architecture,
            appname=appname,
            version=manifest.get("version"),
        )
    except (OperationError, UsageError) as exc:
        report.error(str(exc))
        if rollback_remote and rollback_needed:
            rollback = remote_run(
                config,
                _install_command(
                    rollback_remote,
                    env_path=remote_env,
                    volume=effective_volume,
                ),
                timeout=600,
            )
            rollback_details: dict[str, object] = {
                "attempted": True,
                "exit_code": rollback.returncode,
                "stdout": rollback.stdout.strip(),
                "stderr": rollback.stderr.strip(),
            }
            if _appcenter_has_error(rollback):
                report.error("explicit rollback FPK also failed to install")
            else:
                try:
                    if rollback_manifest_sha is None:
                        raise OperationError("rollback manifest evidence is unavailable")
                    rollback_details["installed_payload"] = _verify_installed_payload(
                        config,
                        appname,
                        rollback_path,
                        remote_dir,
                    )
                    if start:
                        initial = remote_run(
                            config,
                            ["appcenter-cli", "status", appname],
                        )
                        if (
                            _appcenter_has_error(initial)
                            or _status_value(initial) != "running"
                        ):
                            restarted = remote_run(
                                config,
                                ["appcenter-cli", "start", appname],
                                timeout=180,
                            )
                            rollback_details["start"] = {
                                "exit_code": restarted.returncode,
                                "stdout": restarted.stdout.strip(),
                                "stderr": restarted.stderr.strip(),
                            }
                    rollback_status = _wait_for_application_status(
                        config,
                        appname,
                        require_running=start,
                        retries=status_retries,
                    )
                    rollback_details["status"] = {
                        "exit_code": rollback_status.returncode,
                        "stdout": rollback_status.stdout.strip(),
                        "stderr": rollback_status.stderr.strip(),
                    }
                    rollback_details["installed_manifest"] = _verify_installed_manifest(
                        config,
                        appname,
                        rollback_manifest.get("version"),
                        rollback_manifest_sha,
                    )
                    rollback_logs = app_logs(config, appname, lines=100)
                    rollback_details["logs"] = rollback_logs.as_dict()
                    rollback_details["verified"] = True
                except OperationError as rollback_exc:
                    rollback_details["verified"] = False
                    report.error(f"explicit rollback verification failed: {rollback_exc}")
            report.details["rollback"] = rollback_details
    finally:
        try:
            _safe_remote_cleanup(config, remote_dir)
        except (OperationError, UsageError) as exc:
            report.warn(f"could not remove scoped remote temporary directory: {exc}")
    return report


def uninstall(config: SSHConfig, appname: str, *, confirmed: bool) -> Report:
    validate_appname(appname)
    if not confirmed:
        raise UsageError("uninstall requires --yes")
    report = Report()
    stopped = remote_run(config, ["appcenter-cli", "stop", appname])
    removed = remote_run(config, ["appcenter-cli", "uninstall", appname], timeout=300)
    report.details.update(
        {
            "host": config.host,
            "appname": appname,
            "stop_exit_code": stopped.returncode,
            "uninstall_exit_code": removed.returncode,
            "stdout": removed.stdout.strip(),
            "stderr": removed.stderr.strip(),
        }
    )
    if _appcenter_has_error(removed):
        report.error(
            f"appcenter-cli uninstall failed: "
            f"{removed.stderr.strip() or removed.stdout.strip() or 'no output'}"
        )
        report.details["uninstall_verified"] = False
        return report
    try:
        still_installed = application_installed(config, appname)
    except OperationError as exc:
        report.details["uninstall_verified"] = False
        report.error(f"could not verify uninstall postcondition: {exc}")
        return report
    report.details["uninstall_verified"] = not still_installed
    if still_installed:
        report.error(
            "appcenter-cli reported uninstall success but the application is still installed"
        )
    return report
