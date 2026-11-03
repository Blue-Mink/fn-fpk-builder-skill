"""Contract + blind-spot checks for the fnOS FPK builder skill.

``test_docs.py`` covers routing, anchors and secret hygiene. This file covers
*content truth* — the commands and field names an agent will copy — and it
self-tests the checker against mutated copies, so a docs regression cannot
silently pass. Every item below traces back to real device/tool evidence in
``references/official-contract.md``.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "fnpack build",
    "fnpack create",
    "install-fpk",
    "--env",
    "MD5",
    "10500",
    "10150",
    "11000",
    "190",
    "checkport",
    "api-scope",
    "TRIM_API_TOKEN",
    "port-config",
    "systemd-unit",
    "usr-local-linker",
    "docker-project",
    "desktop_applaunchname",
    "ICON.PNG",
    "icon_0.png",
    "wizard_delete_data",
    "TRIM_OLD_APPVER",
    "gatewayPrefix",
    "gatewaySocket",
    "/cgi/ThirdParty/",
    "TRIM_APPDEST",
    "X-Trim-Isadmin",
    "--arch arm64",
    "--arch both",
    "-fnos-arm64.fpk",
    "EM_AARCH64",
    "install_callback",
    "uninstall_callback",
    "config_init",
    "config_callback",
    "upgrade_init",
    "upgrade_callback",
    "≥512",
]

# 允许“讲旧命令为什么不能用”的上下文，避免把 deprecation 说明误判成用法。
ALLOW = ("没有", "不存在", "已废弃", "不要用", "旧命令", "禁止", "不是")

FORBIDDEN = [
    (r"\bfnpack\s+pack\b", "fnpack 只有 build/create，没有 pack"),
    (r"\bfnpack\s+crc32\b", "fnpack 没有 crc32 子命令"),
    (r"\bCRC32\b", "manifest.checksum 是 MD5，不是 CRC32"),
    (r"appcenter-cli\s+install\s+\S*\.fpk", "本地 FPK 用 install-fpk，不是 install"),
    (r"(?<!appcenter-cli )\binstall-fpk\s+\S+\.fpk", "install-fpk 必须带 appcenter-cli 前缀"),
    (r"根本不请求", "槽位取用已按 2026-11-02 实测订正：size>=512 回落 icon_64"),
    (r"(?i)platform\s*=\s*\[", "manifest.platform 是字符串，不是列表"),
    (r"--arch\s+x86\b", "--arch 只接受 amd64/arm64/all/both，x86 不是合法值"),
    (r"--arch\s+arm\b(?!64)", "--arch 只接受 amd64/arm64/all/both，arm 不是合法值"),
]


def markdown(root: Path):
    return sorted(p for p in root.rglob("*.md") if ".git" not in p.parts)


def check_contract(root: Path) -> list[str]:
    errors: list[str] = []
    skill_path = root / "SKILL.md"
    if not skill_path.exists():
        return ["缺少 SKILL.md"]
    skill = skill_path.read_text(encoding="utf-8")
    blob = "\n".join(p.read_text(encoding="utf-8") for p in markdown(root))

    for needle in REQUIRED:
        if needle not in blob:
            errors.append(f"缺少必需内容: {needle}")

    for ref in sorted((root / "references").glob("*.md")):
        if f"references/{ref.name}" not in skill:
            errors.append(f"引用文件未在 SKILL.md 路由: {ref.name}")

    for path in markdown(root):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(token in line for token in ALLOW):
                continue
            for pattern, why in FORBIDDEN:
                if re.search(pattern, line):
                    errors.append(f"{path.relative_to(root)}:{number}: {why}: {line.strip()[:100]}")
            # checksum 口径：同一行出现 checksum 与 SHA-256 却没有 MD5 = 写错了
            if re.search(r"(?i)checksum", line) and "SHA-256" in line and "MD5" not in line:
                errors.append(f"{path.relative_to(root)}:{number}: checksum 是 app.tgz 的 MD5，不是整包 SHA-256")
    return errors

def _copy(destination: Path) -> Path:
    copy = destination / "skill"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return copy


def _append_to_skill(copy: Path, text: str) -> None:
    path = copy / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n\n" + text + "\n", encoding="utf-8")


class ContractTests(unittest.TestCase):
    def test_current_docs_satisfy_contract(self) -> None:
        errors = check_contract(ROOT)
        self.assertEqual([], errors, "\n".join(errors))

    def test_detects_stale_pack_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = _copy(Path(td))
            _append_to_skill(copy, "fnpack pack .")
            errors = check_contract(copy)
            self.assertTrue(any("没有 pack" in e for e in errors), errors)

    def test_detects_wrong_install_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = _copy(Path(td))
            _append_to_skill(copy, "appcenter-cli install app.fpk")
            errors = check_contract(copy)
            self.assertTrue(any("本地 FPK 用 install-fpk" in e for e in errors), errors)

    def test_detects_missing_api_scope_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = _copy(Path(td))
            for path in markdown(copy):
                path.write_text(
                    path.read_text(encoding="utf-8").replace("api-scope", ""), encoding="utf-8"
                )
            errors = check_contract(copy)
            self.assertTrue(any("api-scope" in e for e in errors), errors)

    def test_detects_missing_gateway_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = _copy(Path(td))
            for path in markdown(copy):
                path.write_text(
                    path.read_text(encoding="utf-8").replace("gatewayPrefix", ""), encoding="utf-8"
                )
            errors = check_contract(copy)
            self.assertTrue(any("gatewayPrefix" in e for e in errors), errors)

    def test_detects_missing_arm_build_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = _copy(Path(td))
            for path in markdown(copy):
                path.write_text(
                    path.read_text(encoding="utf-8").replace("--arch arm64", ""), encoding="utf-8"
                )
            errors = check_contract(copy)
            self.assertTrue(any("--arch arm64" in e for e in errors), errors)

    def test_detects_wrong_checksum_wording(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = _copy(Path(td))
            _append_to_skill(copy, "manifest.checksum 是 FPK 整包的 SHA-256")
            errors = check_contract(copy)
            self.assertTrue(any("checksum 是 app.tgz 的 MD5" in e for e in errors), errors)

    def test_detects_reverted_icon_slot_claim(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = _copy(Path(td))
            _append_to_skill(copy, "icon_64 / icon_128 平台根本不请求。")
            errors = check_contract(copy)
            self.assertTrue(any("2026-11-02" in e or "根本不请求" in e for e in errors), errors)

def _pins(root: Path) -> tuple[str, dict[str, str]]:
    toolchain = (root / "scripts/fpk_lib/toolchain.py").read_text(encoding="utf-8")
    version = re.search(r'FNPACK_VERSION\s*=\s*"([^"]+)"', toolchain)
    if not version:
        raise AssertionError("toolchain.py 缺少 FNPACK_VERSION")
    pins: dict[str, str] = {}
    for host, body in re.findall(r'"([a-z0-9-]+)":\s*\{(.*?)\n\s*\}', toolchain, re.S):
        match = re.search(r'"sha256":\s*(None|"([0-9a-f]{64})")', body)
        if match and match.group(2):
            pins[host] = match.group(2)
    return version.group(1), pins


def _ci_pins(root: Path) -> tuple[str, dict[str, str]]:
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    version = re.search(r'FNPACK_VERSION:\s*"([^"]+)"', ci)
    if not version:
        raise AssertionError("ci.yml 缺少 FNPACK_VERSION")
    shas = {
        host.replace("_", "-").lower(): sha
        for host, sha in re.findall(r'FNPACK_([A-Z0-9_]+)_SHA256:\s*"([0-9a-f]{64})"', ci)
    }
    return version.group(1), shas


class PinTests(unittest.TestCase):
    """A silently diverging tool pin is invisible until a runner downloads the wrong binary."""

    def test_toolchain_and_ci_agree(self) -> None:
        version, pins = _pins(ROOT)
        ci_version, ci_shas = _ci_pins(ROOT)
        self.assertEqual(version, ci_version, "ci.yml 与 toolchain.py 的 fnpack 版本不一致")
        self.assertTrue(ci_shas, "ci.yml 没有解析到任何 fnpack SHA-256")
        for host, sha in ci_shas.items():
            self.assertEqual(pins.get(host), sha, f"{host} 的 SHA-256 在 ci.yml 与 toolchain.py 不一致")
        self.assertIn("linux-amd64", ci_shas)

    def test_provenance_matches_toolchain(self) -> None:
        version, pins = _pins(ROOT)
        data = json.loads((ROOT / "references/provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(version, data["fnpack"]["version"], "provenance.json 的 fnpack 版本不一致")
        for host, sha in pins.items():
            self.assertEqual(
                sha, data["fnpack"]["downloads"][host]["sha256"], f"provenance.json 的 {host} 哈希不一致"
            )

    def test_docs_mention_the_pinned_version(self) -> None:
        version, _ = _pins(ROOT)
        for name in ("README.md", "SKILL.md", "references/ci-release.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(version, text, f"{name} 没跟上 fnpack 版本 {version}")

    def test_pin_drift_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = _copy(Path(td))
            ci = copy / ".github/workflows/ci.yml"
            ci.write_text(
                ci.read_text(encoding="utf-8").replace('FNPACK_VERSION: "1.2.3"', 'FNPACK_VERSION: "1.2.9"'),
                encoding="utf-8",
            )
            with self.assertRaises(AssertionError):
                version, pins = _pins(copy)
                ci_version, ci_shas = _ci_pins(copy)
                if version != ci_version:
                    raise AssertionError("drift")


if __name__ == "__main__":
    unittest.main()


