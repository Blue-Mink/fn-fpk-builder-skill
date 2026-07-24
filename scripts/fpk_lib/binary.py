"""Portable native-binary detection without invoking host `file` or readelf."""

from __future__ import annotations

import struct
from dataclasses import dataclass


ELF_MACHINES = {
    3: "x86",
    40: "arm",
    62: "amd64",
    183: "arm64",
}

MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


@dataclass(frozen=True)
class BinaryInfo:
    format: str
    architecture: str | None = None
    machine: int | None = None
    elf_type: int | None = None


def detect_binary(header: bytes) -> BinaryInfo | None:
    if header.startswith(b"\x7fELF"):
        if len(header) < 20:
            return BinaryInfo("elf", "unknown", None)
        elf_class = header[4]
        endian_marker = header[5]
        if elf_class not in {1, 2} or endian_marker not in {1, 2} or header[6] != 1:
            return BinaryInfo("elf", "unknown", None)
        endian = "<" if endian_marker == 1 else ">"
        elf_type = struct.unpack(f"{endian}H", header[16:18])[0]
        machine = struct.unpack(f"{endian}H", header[18:20])[0]
        expected_class = {3: 1, 40: 1, 62: 2, 183: 2}.get(machine)
        # Current fnOS targets are little-endian. A mismatched ELF class or
        # byte order must not be accepted based on e_machine alone.
        if expected_class != elf_class or endian_marker != 1:
            return BinaryInfo("elf", "unknown", machine, elf_type)
        return BinaryInfo(
            "elf",
            ELF_MACHINES.get(machine, "unknown"),
            machine,
            elf_type,
        )
    if header[:4] in MACHO_MAGICS:
        return BinaryInfo("mach-o")
    if header.startswith(b"MZ"):
        return BinaryInfo("pe")
    return None
