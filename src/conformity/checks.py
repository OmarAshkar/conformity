"""The conformance check engine."""

from __future__ import annotations

import glob
import hashlib
from pathlib import Path

from conformity.config import Config, load_config
from conformity.models import CheckResult, Report, RuleKind, Status

_GLOB_CHARS = frozenset("*?[")


class ConformanceError(ValueError):
    """Raised when the target or source cannot be checked."""


def sha256(path: Path, cache: dict[Path, str] | None = None) -> str:
    """Return the SHA-256 hex digest of *path*."""

    key = path.absolute()
    if cache is not None and key in cache:
        return cache[key]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    if cache is not None:
        cache[key] = value
    return value


def run_checks(
    target: str | Path,
    source: str | Path,
    config: Config | str | Path,
) -> Report:
    """Check *target* against the rules of *source*.

    ``config`` is a loaded :class:`Config` or a path to a TOML rules file.
    """

    target_dir = Path(target)
    source_dir = Path(source)
    _require_directory(target_dir, "target")
    _require_directory(source_dir, "source")

    if not isinstance(config, Config):
        config = load_config(config)

    report = Report(target=target_dir, source=source_dir, config_path=config.path)
    hashes: dict[Path, str] = {}

    for pattern in config.identical:
        _check_identical(report, target_dir, source_dir, pattern, hashes)
    for pattern in config.different:
        _check_different(report, target_dir, source_dir, pattern, hashes)
    for directory in config.required_dirs:
        _check_required_dir(report, target_dir, directory)
    for directory, entries in config.structure.items():
        _check_structure(report, target_dir, directory, entries)

    return report


def _require_directory(path: Path, label: str) -> None:
    if not path.exists():
        raise ConformanceError(f"{label} directory does not exist: {path}")
    if not path.is_dir():
        raise ConformanceError(f"{label} path is not a directory: {path}")


def _check_identical(
    report: Report,
    target: Path,
    source: Path,
    pattern: str,
    hashes: dict[Path, str],
) -> None:
    expected = _expected_files(source, pattern)
    if not expected:
        report.add(
            CheckResult(
                RuleKind.IDENTICAL,
                pattern,
                Status.ERROR,
                "no files matched in source",
            )
        )
        return

    for relative in expected:
        source_file = source / relative
        target_file = target / relative
        if not source_file.is_file():
            report.add(
                CheckResult(
                    RuleKind.IDENTICAL,
                    relative,
                    Status.ERROR,
                    "missing in source",
                )
            )
        elif not target_file.exists():
            report.add(
                CheckResult(RuleKind.IDENTICAL, relative, Status.FAIL, "missing in target")
            )
        elif not target_file.is_file():
            report.add(
                CheckResult(RuleKind.IDENTICAL, relative, Status.FAIL, "not a file in target")
            )
        elif sha256(source_file, hashes) != sha256(target_file, hashes):
            report.add(
                CheckResult(
                    RuleKind.IDENTICAL,
                    relative,
                    Status.FAIL,
                    "SHA-256 differs from source",
                )
            )
        else:
            report.add(
                CheckResult(RuleKind.IDENTICAL, relative, Status.PASS, "content matches")
            )


def _check_different(
    report: Report,
    target: Path,
    source: Path,
    pattern: str,
    hashes: dict[Path, str],
) -> None:
    expected = _expected_files(source, pattern)
    if not expected:
        report.add(
            CheckResult(
                RuleKind.DIFFERENT,
                pattern,
                Status.ERROR,
                "no files matched in source",
            )
        )
        return

    for relative in expected:
        source_file = source / relative
        target_file = target / relative
        if not source_file.is_file():
            report.add(
                CheckResult(
                    RuleKind.DIFFERENT,
                    relative,
                    Status.ERROR,
                    "missing in source",
                )
            )
        elif not target_file.exists():
            report.add(
                CheckResult(RuleKind.DIFFERENT, relative, Status.FAIL, "missing in target")
            )
        elif not target_file.is_file():
            report.add(
                CheckResult(RuleKind.DIFFERENT, relative, Status.FAIL, "not a file in target")
            )
        elif sha256(source_file, hashes) == sha256(target_file, hashes):
            report.add(
                CheckResult(
                    RuleKind.DIFFERENT,
                    relative,
                    Status.FAIL,
                    "content is identical to source but must differ",
                )
            )
        else:
            report.add(
                CheckResult(
                    RuleKind.DIFFERENT,
                    relative,
                    Status.PASS,
                    "exists and differs from source",
                )
            )


def _check_required_dir(report: Report, target: Path, directory: str) -> None:
    path = target / directory
    if path.is_dir():
        report.add(
            CheckResult(RuleKind.DIRECTORY, directory, Status.PASS, "directory exists")
        )
    elif path.exists():
        report.add(
            CheckResult(RuleKind.DIRECTORY, directory, Status.FAIL, "not a directory")
        )
    else:
        report.add(
            CheckResult(RuleKind.DIRECTORY, directory, Status.FAIL, "missing directory")
        )


def _check_structure(
    report: Report, target: Path, directory: str, entries: tuple[str, ...]
) -> None:
    base = target / directory
    if not base.is_dir():
        report.add(
            CheckResult(RuleKind.DIRECTORY, directory, Status.FAIL, "missing directory")
        )
        return

    for entry in entries:
        relative = f"{directory}/{entry}"
        path = target / relative
        if path.exists():
            report.add(
                CheckResult(
                    RuleKind.DIRECTORY,
                    relative,
                    Status.PASS,
                    "required entry exists",
                )
            )
        else:
            report.add(
                CheckResult(
                    RuleKind.DIRECTORY,
                    relative,
                    Status.FAIL,
                    "missing required entry",
                )
            )


def _expected_files(root: Path, pattern: str) -> list[str]:
    """Return relative file paths for a literal path or glob pattern."""

    if not _GLOB_CHARS.intersection(pattern):
        return [Path(pattern).as_posix()]

    matches = glob.glob(
        pattern,
        root_dir=root,
        recursive=True,
        include_hidden=True,
    )
    files = []
    for match in matches:
        relative = Path(match).as_posix()
        if (root / relative).is_file():
            files.append(relative)
    return sorted(set(files))
