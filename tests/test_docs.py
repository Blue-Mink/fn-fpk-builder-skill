"""Documentation invariants: routing coverage, internal links, and hygiene.

These guards exist because the reference set grew past a dozen files and two
regressions became likely: a new ``references/*.md`` that no route in
``SKILL.md`` points to (so no agent ever loads it), and a renamed heading that
breaks ``file.md#中文标题`` anchors (GitHub keeps the leading ``-`` left by a
stripped emoji, which hand-written links routinely get wrong).
"""

from __future__ import annotations

import re
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def github_slug(heading: str) -> str:
    """Approximate GitHub's anchor rule for the headings used here.

    Lowercase, drop punctuation/backticks/emphasis, keep letters (including
    CJK) plus ``-`` and ``_``, turn spaces into ``-``. Emoji are stripped but
    the surrounding spaces survive as a leading ``-`` — links must match that.
    """

    text = re.sub(r"[`*_\[\]()!#]", "", heading.strip().lower())
    out: list[str] = []
    for char in text:
        if char.isalnum() or char in "-_" or unicodedata.category(char).startswith("L"):
            out.append(char)
        elif char in " \t":
            out.append("-")
    return "".join(out)


def markdown_files() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.md") if "evals" not in p.parts)


def headings_of(path: Path) -> set[str]:
    found: set[str] = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = re.match(r"^#{1,6}\s+(.*)$", line)
        if match:
            found.add(github_slug(match.group(1)))
    return found


def code_free_lines(path: Path):
    in_code = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            yield number, line


class DocumentationTests(unittest.TestCase):
    def test_every_reference_document_is_routed_from_skill(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for path in sorted((ROOT / "references").glob("*.md")):
            with self.subTest(reference=path.name):
                self.assertIn(f"references/{path.name}", skill)

    def test_skill_routes_point_to_existing_documents(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        names = set(re.findall(r"references/([A-Za-z0-9._-]+\.md)", skill))
        self.assertTrue(names)
        for name in sorted(names):
            with self.subTest(reference=name):
                self.assertTrue((ROOT / "references" / name).is_file())

    def test_internal_links_and_anchors_resolve(self) -> None:
        files = markdown_files()
        for path in files:
            for number, line in code_free_lines(path):
                for match in re.finditer(r"\]\(([^)\s]+)\)", line):
                    target = match.group(1)
                    if target.startswith(("http://", "https://", "mailto:")):
                        continue
                    file_part, _, fragment = target.partition("#")
                    resolved = (
                        (path.parent / file_part).resolve() if file_part else path
                    )
                    with self.subTest(link=target, location=f"{path.name}:{number}"):
                        if file_part:
                            self.assertTrue(resolved.exists(), "目标文件不存在")
                        if fragment and (not file_part or file_part.endswith(".md")):
                            self.assertIn(
                                fragment.lower(),
                                headings_of(resolved),
                                "锚点与标题不匹配（注意 GitHub 会保留 emoji 删除后的前导 -）",
                            )

    def test_documents_carry_no_device_specific_secrets(self) -> None:
        """Reference documents ship publicly; keep device identity out of them.

        Naming convention for the house style: hosts become ``<NAS_IP>``,
        apps/ports/versions become placeholders, artifact hashes never appear,
        and real applications are described by category instead of by name.
        """

        patterns = [
            ("内网 IPv4", r"\b(?:192\.168|10\.\d|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
            ("意外的公网 IPv4", r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
            ("凭据赋值", r"(?i)\b(?:password|passwd|token|secret)\b\s*[:=]\s*\S"),
            ("口令赋值", r"(?i)\b(?:口令|密码)\b\s*[:=]\s*\S"),
            ("Token 形态", r"\b(?:ghp_|github_pat_|gho_|ghs_|sk-[A-Za-z0-9]{20,})"),
            ("私钥块", r"BEGIN [A-Z ]*PRIVATE KEY"),
            ("私有工作区路径", r"/vol1/@appshare/com\.dustinky"),
            ("64 位构件哈希", r"\b[0-9a-f]{64}\b"),
            ("32 位载荷哈希", r"\b[0-9a-f]{32}\b"),
            # Only literal values are a leak; `wizard_x={port}` / `<port>` / `$VAR`
            # are exactly the placeholder style the reference documents use.
            ("安装向导变量赋真值", r"wizard_[a-z_]+\s*=\s*(?![<{.$[\-])(?!\.\.\.)\S"),
        ]
        # Lines that *teach* the scan must be allowed to contain its patterns.
        allow_marks = ("grep -RInE", "grep -RnoE", "grep -RIn", "命中", "示例：")
        for path in markdown_files():
            for number, line in code_free_lines(path):
                if any(mark in line for mark in allow_marks):
                    continue
                for name, pattern in patterns:
                    with self.subTest(issue=name, location=f"{path.name}:{number}"):
                        self.assertIsNone(
                            re.search(pattern, line),
                            f"{name}: {line.strip()[:120]}",
                        )


if __name__ == "__main__":
    unittest.main()
