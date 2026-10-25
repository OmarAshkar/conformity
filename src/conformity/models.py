"""Result types shared by the CLI, the TUI, and the check engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RuleKind(str, Enum):
    """The kind of conformance rule a result came from."""

    IDENTICAL = "identical"
    DIFFERENT = "different"
    DIRECTORY = "directory"


class Status(str, Enum):
    """Outcome of a single check."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of checking one path against one rule."""

    kind: RuleKind
    path: str
    status: Status
    message: str


@dataclass(slots=True)
class Report:
    """All results for one target/source comparison."""

    results: list[CheckResult] = field(default_factory=list)
    target: Path | None = None
    source: Path | None = None
    config_path: Path | None = None

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.FAIL]

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.ERROR]

    @property
    def passed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.PASS]

    @property
    def ok(self) -> bool:
        return not self.failures and not self.errors

    def counts(self) -> dict[Status, int]:
        return {status: sum(1 for r in self.results if r.status is status) for status in Status}

    def __str__(self) -> str:
        lines = [
            f"{result.status.value.upper():5} {result.kind.value:9} "
            f"{result.path} - {result.message}"
            for result in self.results
        ]
        counts = self.counts()
        lines.append("")
        lines.append(
            f"{counts[Status.PASS]} passed, {counts[Status.FAIL]} failed, "
            f"{counts[Status.ERROR]} errors"
        )
        lines.append("Conforms." if self.ok else "Does not conform.")
        return "\n".join(lines)
