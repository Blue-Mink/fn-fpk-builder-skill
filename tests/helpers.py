"""Reusable FPK fixtures built only with the Python standard library."""

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import tarfile
import zlib
from pathlib import Path


def make_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


PNG_64 = make_png(64, 64)
PNG_256 = make_png(256, 256)


def elf_header(machine: int) -> bytes:
    data = bytearray(64)
    data[:4] = b"\x7fELF"
    data[4] = 2
    data[5] = 1
    data[6] = 1
    struct.pack_into("<H", data, 16, 2)
    struct.pack_into("<H", data, 18, machine)
    return bytes(data)


def create_project(
    root: Path,
    *,
    appname: str = "fixture-app",
    platform: str = "all",
    native_machine: int | None = None,
) -> Path:
    project = root / appname
    for relative in ("app", "cmd", "config", "wizard"):
        (project / relative).mkdir(parents=True, exist_ok=True)
    (project / "manifest").write_text(
        "\n".join(
            [
                f"appname={appname}",
                "version=1.2.3",
                "display_name=Fixture",
                "desc=Fixture application",
                "maintainer=Tests",
                "source=thirdparty",
                f"platform={platform}",
                "ctl_stop=true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (project / "config" / "privilege").write_text(
        json.dumps(
            {
                "defaults": {"run-as": "package"},
                "username": "fixture",
                "groupname": "fixture",
            }
        ),
        encoding="utf-8",
    )
    (project / "config" / "resource").write_text("{}\n", encoding="utf-8")
    (project / "wizard" / "install").write_text("[]\n", encoding="utf-8")
    (project / "app" / "marker.txt").write_text("fixture\n", encoding="utf-8")
    if native_machine is not None:
        binary = project / "app" / "bin" / "server"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(elf_header(native_machine))
        binary.chmod(0o755)
    main = project / "cmd" / "main"
    main.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    main.chmod(0o755)
    for name in (
        "install_init",
        "install_callback",
        "upgrade_init",
        "upgrade_callback",
        "uninstall_init",
        "uninstall_callback",
        "config_init",
        "config_callback",
    ):
        script = project / "cmd" / name
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
    (project / "ICON.PNG").write_bytes(PNG_64)
    (project / "ICON_256.PNG").write_bytes(PNG_256)
    return project


def _tar_bytes(entries: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for info, data in entries:
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return output.getvalue()


def file_info(name: str, data: bytes, mode: int = 0o644) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    return info


def create_fpk(
    path: Path,
    *,
    platform: str = "all",
    binaries: list[tuple[str, bytes]] | None = None,
    lifecycle_main: bytes | None = None,
    checksum_override: str | None = None,
    inner_extra: list[tuple[tarfile.TarInfo, bytes | None]] | None = None,
    outer_extra: list[tuple[tarfile.TarInfo, bytes | None]] | None = None,
) -> Path:
    inner_entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    marker = b"fixture\n"
    inner_entries.append((file_info("marker.txt", marker), marker))
    for name, data in binaries or []:
        inner_entries.append((file_info(name, data, 0o755), data))
    inner_entries.extend(inner_extra or [])
    app_tgz = _tar_bytes(inner_entries)
    checksum = checksum_override or hashlib.md5(app_tgz).hexdigest()
    manifest = (
        "\n".join(
            [
                "appname=fixture-app",
                "version=1.2.3",
                "display_name=Fixture",
                "desc=Fixture application",
                "maintainer=Tests",
                "source=thirdparty",
                f"platform={platform}",
                f"checksum={checksum}",
                "",
            ]
        )
    ).encode()
    privilege = b'{"defaults":{"run-as":"package"},"username":"fixture","groupname":"fixture"}\n'
    resource = b"{}\n"
    main = lifecycle_main or b"#!/bin/sh\nexit 0\n"
    wizard = b"[]\n"
    entries = [
        (file_info("manifest", manifest), manifest),
        (file_info("app.tgz", app_tgz), app_tgz),
        (file_info("config/privilege", privilege), privilege),
        (file_info("config/resource", resource), resource),
        (file_info("cmd/main", main, 0o755), main),
        (file_info("wizard/install", wizard), wizard),
        (file_info("ICON.PNG", PNG_64), PNG_64),
        (file_info("ICON_256.PNG", PNG_256), PNG_256),
    ]
    for name in (
        "install_init",
        "install_callback",
        "upgrade_init",
        "upgrade_callback",
        "uninstall_init",
        "uninstall_callback",
        "config_init",
        "config_callback",
    ):
        entries.append((file_info(f"cmd/{name}", main, 0o755), main))
    entries.extend(outer_extra or [])
    path.write_bytes(_tar_bytes(entries))
    return path


FAKE_FNPACK = r"""#!/usr/bin/env python3
import hashlib
import io
import os
import pathlib
import re
import sys
import tarfile

if "--version" in sys.argv or (len(sys.argv) > 1 and sys.argv[1] == "version"):
    print("fnpack version 1.2.3")
    raise SystemExit(0)

if len(sys.argv) < 4 or sys.argv[1] != "build" or sys.argv[2] not in {"-d", "--directory"}:
    print("unsupported fake fnpack invocation", file=sys.stderr)
    raise SystemExit(2)

project = pathlib.Path(sys.argv[3]).resolve()

def manifest_values(text):
    result = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(("#", ";")):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result

payload = io.BytesIO()
with tarfile.open(fileobj=payload, mode="w:gz") as inner:
    for candidate in sorted((project / "app").rglob("*")):
        inner.add(
            candidate,
            arcname=candidate.relative_to(project / "app").as_posix(),
            recursive=False,
        )
app_tgz = payload.getvalue()
manifest_path = project / "manifest"
manifest = manifest_path.read_text(encoding="utf-8")
checksum = hashlib.md5(app_tgz).hexdigest()
if re.search(r"(?m)^checksum=", manifest):
    manifest = re.sub(r"(?m)^checksum=.*$", f"checksum={checksum}", manifest)
else:
    manifest += f"checksum={checksum}\n"
values = manifest_values(manifest)

destination = project / f"{values['appname']}.fpk"
with tarfile.open(destination, mode="w:gz") as outer:
    manifest_bytes = manifest.encode()
    info = tarfile.TarInfo("manifest")
    info.size = len(manifest_bytes)
    outer.addfile(info, io.BytesIO(manifest_bytes))
    info = tarfile.TarInfo("app.tgz")
    info.size = len(app_tgz)
    outer.addfile(info, io.BytesIO(app_tgz))
    for name in ("config", "cmd", "wizard"):
        outer.add(project / name, arcname=name)
    outer.add(project / "ICON.PNG", arcname="ICON.PNG")
    outer.add(project / "ICON_256.PNG", arcname="ICON_256.PNG")
"""


def create_fake_fnpack(path: Path) -> Path:
    path.write_text(FAKE_FNPACK, encoding="utf-8")
    path.chmod(0o755)
    return path
