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
    initial: bool = False,
) -> Report:
    """Check *target* against the rules of *source*.

    ``config`` is a loaded :class:`Config` or a path to a TOML rules file.

    When ``initial`` is true, only file existence is checked: ``identical`` and
    ``different`` files are not hashed, and directory rules are skipped. This
    is useful when bootstrapping a new target directory.
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
        _check_identical(
            report, target_dir, source_dir, pattern, hashes, initial=initial
        )
    for pattern in config.different:
        _check_different(
            report, target_dir, source_dir, pattern, hashes, initial=initial
        )
    for pattern in config.required_files:
        _check_required_file(report, target_dir, pattern)
    if not initial:
        for directory in config.required_dirs:
            _check_required_dir(report, target_dir, directory)
        for pattern in config.structure_required:
            _check_structure_required(report, target_dir, pattern)
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
    *,
    initial: bool = False,
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
        elif initial:
            report.add(CheckResult(RuleKind.IDENTICAL, relative, Status.PASS, "file exists"))
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
    *,
    initial: bool = False,
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
        elif initial:
            report.add(CheckResult(RuleKind.DIFFERENT, relative, Status.PASS, "file exists"))
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


def _check_required_file(report: Report, target: Path, pattern: str) -> None:
    """A required file is a literal path or a glob with at least one match."""

    if _is_glob(pattern):
        matches = [
            match for match in _glob_matches(target, pattern) if (target / match).is_file()
        ]
        if matches:
            report.add(
                CheckResult(
                    RuleKind.FILE,
                    pattern,
                    Status.PASS,
                    _describe_matches(matches, "file", "files"),
                )
            )
        else:
            report.add(
                CheckResult(RuleKind.FILE, pattern, Status.FAIL, "no files match pattern")
            )
        return

    path = target / pattern
    if path.is_file():
        report.add(CheckResult(RuleKind.FILE, pattern, Status.PASS, "file exists"))
    elif path.exists():
        report.add(CheckResult(RuleKind.FILE, pattern, Status.FAIL, "not a file"))
    else:
        report.add(CheckResult(RuleKind.FILE, pattern, Status.FAIL, "missing file"))


def _check_required_dir(report: Report, target: Path, directory: str) -> None:
    if _is_glob(directory):
        matches = [
            match
            for match in _glob_matches(target, directory)
            if (target / match).is_dir()
        ]
        if matches:
            report.add(
                CheckResult(
                    RuleKind.DIRECTORY,
                    directory,
                    Status.PASS,
                    _describe_matches(matches, "directory", "directories"),
                )
            )
        else:
            report.add(
                CheckResult(
                    RuleKind.DIRECTORY,
                    directory,
                    Status.FAIL,
                    "no directories match pattern",
                )
            )
        return

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


def _check_structure_required(report: Report, target: Path, pattern: str) -> None:
    """Check a flat structure requirement relative to the target root.

    A trailing ``/`` requires a directory. Glob patterns need at least one
    match; without a trailing slash files and directories are both accepted.
    """

    wants_directory = pattern.endswith("/")
    normalized = pattern.rstrip("/") or pattern

    if _is_glob(normalized):
        matches = _glob_matches(target, normalized)
        if wants_directory:
            matches = [match for match in matches if (target / match).is_dir()]
        if matches:
            singular = "directory" if wants_directory else "entry"
            plural = "directories" if wants_directory else "entries"
            report.add(
                CheckResult(
                    RuleKind.DIRECTORY,
                    pattern,
                    Status.PASS,
                    _describe_matches(matches, singular, plural),
                )
            )
        else:
            message = (
                "no directories match pattern"
                if wants_directory
                else "no entries match pattern"
            )
            report.add(CheckResult(RuleKind.DIRECTORY, pattern, Status.FAIL, message))
        return

    path = target / normalized
    if wants_directory:
        if path.is_dir():
            report.add(
                CheckResult(
                    RuleKind.DIRECTORY,
                    pattern,
                    Status.PASS,
                    "directory exists",
                )
            )
        elif path.exists():
            report.add(
                CheckResult(RuleKind.DIRECTORY, pattern, Status.FAIL, "not a directory")
            )
        else:
            report.add(
                CheckResult(RuleKind.DIRECTORY, pattern, Status.FAIL, "missing directory")
            )
        return

    if path.exists():
        report.add(
            CheckResult(
                RuleKind.DIRECTORY,
                pattern,
                Status.PASS,
                "required entry exists",
            )
        )
    else:
        report.add(
            CheckResult(
                RuleKind.DIRECTORY,
                pattern,
                Status.FAIL,
                "missing required entry",
            )
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
        if _is_glob(entry):
            matches = _glob_matches(base, entry)
            if matches:
                display = [f"{directory}/{match}" for match in matches]
                report.add(
                    CheckResult(
                        RuleKind.DIRECTORY,
                        relative,
                        Status.PASS,
                        _describe_matches(display, "entry", "entries"),
                    )
                )
            else:
                report.add(
                    CheckResult(
                        RuleKind.DIRECTORY,
                        relative,
                        Status.FAIL,
                        "no entries match pattern",
                    )
                )
            continue

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


def _is_glob(pattern: str) -> bool:
    return bool(_GLOB_CHARS.intersection(pattern))


def _glob_matches(root: Path, pattern: str) -> list[str]:
    matches = glob.glob(
        pattern,
        root_dir=root,
        recursive=True,
        include_hidden=True,
    )
    return sorted({Path(match).as_posix() for match in matches})


def _describe_matches(matches: list[str], singular: str, plural: str) -> str:
    label = singular if len(matches) == 1 else plural
    preview = ", ".join(matches[:3])
    if len(matches) > 3:
        preview += ", ..."
    return f"{len(matches)} {label} match ({preview})"


def _expected_files(root: Path, pattern: str) -> list[str]:
    """Return relative file paths for a literal path or glob pattern."""

    if not _is_glob(pattern):
        return [Path(pattern).as_posix()]

    return [
        relative
        for relative in _glob_matches(root, pattern)
        if (root / relative).is_file()
    ]
