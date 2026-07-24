"""Stable human and JSON reporting for CLI commands."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Report:
    """Collect one command's machine-readable evidence."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def error(self, message: str) -> None:
        self.ok = False
        self.errors.append(str(message))

    def warn(self, message: str) -> None:
        self.warnings.append(str(message))

    def artifact(
        self,
        path: str | Path,
        *,
        kind: str = "file",
        sha256: str | None = None,
        **metadata: Any,
    ) -> None:
        value: dict[str, Any] = {
            "path": str(Path(path).expanduser().resolve()),
            "kind": kind,
        }
        if sha256:
            value["sha256"] = sha256
        value.update(metadata)
        self.artifacts.append(value)

    def merge(self, other: "Report", *, detail_key: str | None = None) -> None:
        self.ok = self.ok and other.ok
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.artifacts.extend(other.artifacts)
        if detail_key:
            self.details[detail_key] = other.as_dict()
        else:
            self.details.update(other.details)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "artifacts": self.artifacts,
            "details": self.details,
        }

    def render(self, json_mode: bool = False) -> str:
        if json_mode:
            return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)

        lines = ["OK" if self.ok else "FAILED"]
        lines.extend(f"ERROR: {item}" for item in self.errors)
        lines.extend(f"WARNING: {item}" for item in self.warnings)
        for item in self.artifacts:
            digest = f" sha256={item['sha256']}" if item.get("sha256") else ""
            lines.append(f"ARTIFACT: {item['path']}{digest}")
        for key, value in self.details.items():
            if isinstance(value, (dict, list)):
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
            else:
                rendered = str(value)
            lines.append(f"{key}: {rendered}")
        return "\n".join(lines)


class OperationError(RuntimeError):
    """An expected operation failure (exit 1)."""


class UsageError(RuntimeError):
    """An unsupported environment or invalid runtime argument (exit 2)."""
