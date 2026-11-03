#!/usr/bin/env python3
"""Standalone sanitization gate for the published skill tree.

``test_docs.py`` already guards the markdown and shipped JSON. This gate adds
the files that never reached it — ``scripts/``, ``install.sh``/``install.ps1``,
workflow YAML — so a hardcoded address or token cannot ship through a code
sample. It intentionally skips the 64-hex artifact-hash rule, which
``test_docs.py`` owns with a provenance allowlist.

Run: python3 tests/sanitize.py
Exit: 0 clean, 1 hit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "tests", "__pycache__", "assets", "evals"}
SUFFIXES = {".md", ".json", ".yml", ".yaml", ".sh", ".ps1", ".py"}

PATTERNS = [
    (r"\b192\.168\.\d{1,3}\.\d{1,3}\b", "内网 IPv4"),
    (r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "内网 IPv4"),
    (r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b", "内网 IPv4"),
    (r"\bgitea\.linuxst\.cn\b", "内网域名"),
    (r"\bbluemink\b", "真实账号/仓库名"),
    (r"\b\d{6,}@qq\.com\b", "个人邮箱"),
    (r"\b(?:ghp_|gho_|ghs_)[A-Za-z0-9]{20,}\b", "GitHub token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "GitHub token"),
    (r"\bsk-[A-Za-z0-9]{20,}\b", "API key"),
    (r"BEGIN [A-Z ]*PRIVATE KEY", "私钥块"),
    (r"/vol1/@appshare|com\.dustinky", "私有工作区路径"),
]

# Lines that *teach* the scan are allowed to contain its patterns.
ALLOW = ("192.0.2.", "127.0.0.1", "0.0.0.0", "example.com", "grep -RIn", "命中", "示例：", "PLACEHOLDER")


def scan(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if any(token in line for token in ALLOW):
                continue
            for pattern, label in PATTERNS:
                if re.search(pattern, line):
                    errors.append(f"{path.relative_to(root)}:{number}: {label}: {line.strip()[:120]}")
    return errors


def main() -> int:
    errors = scan(ROOT)
    if errors:
        print("SANITIZE FAIL")
        for error in errors:
            print("  " + error)
        return 1
    print(f"SANITIZE PASS ({len(list(ROOT.rglob('*')))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
