"""Synchronise a source directory into one or more Git repositories."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from conformity.checks import ConformanceError, pattern_files, run_checks, sha256
from conformity.config import Config, ConfigError, load_config
from conformity.models import Status

DEFAULT_PROTOCOL = "ssh"
DEFAULT_COMMIT_MESSAGE = "autoupdate repo structure"
CACHE_ENV = "CONFORMITY_CACHE_DIR"

_RULES_FILENAMES = ("conformity.toml", "rules.toml", ".conformity.toml")
_SYNC_FILENAMES = (
    "conformity-sync.toml",
    "sync.toml",
    "conformity.sync.toml",
    ".conformity-sync.toml",
)
_PROTOCOLS = {"ssh", "https"}
_GLOB_CHARS = frozenset("*?[")
_URL_RE = re.compile(r"^(?:https?|ssh|git|file)://", re.IGNORECASE)
_SCP_RE = re.compile(r"^[^@/]+@[^:/]+:.+$")


class SyncError(ValueError):
    """Raised when a sync cannot be performed."""


class SyncConfigError(ValueError):
    """Raised when a sync config is missing or malformed."""


@dataclass(frozen=True, slots=True)
class SyncConfig:
    """Where the source directory should be replicated."""

    destinations: tuple[str, ...]
    protocol: str = DEFAULT_PROTOCOL
    branch: str | None = None
    commit_message: str = DEFAULT_COMMIT_MESSAGE
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Outcome of syncing one destination repository."""

    remote: str
    url: str
    status: Status
    message: str
    changed: int = 0


@dataclass(slots=True)
class SyncReport:
    """Outcome of syncing every configured destination."""

    source: Path
    results: list[SyncResult] = field(default_factory=list)

    def add(self, result: SyncResult) -> None:
        self.results.append(result)

    @property
    def ok(self) -> bool:
        return all(result.status is not Status.FAIL for result in self.results)

    def counts(self) -> dict[Status, int]:
        return {
            status: sum(1 for result in self.results if result.status is status)
            for status in Status
        }

    def __str__(self) -> str:
        lines = [
            f"{result.status.value.upper():5} {result.remote} - {result.message}"
            for result in self.results
        ]
        counts = self.counts()
        lines.append("")
        lines.append(
            f"{counts[Status.PASS]} synced, {counts[Status.FAIL]} failed, "
            f"{counts[Status.ERROR]} errors"
        )
        lines.append("Sync complete." if self.ok else "Sync failed for some repositories.")
        return "\n".join(lines)


def load_sync_config(path: str | Path) -> SyncConfig:
    """Read and validate a sync TOML file."""

    config_path = Path(path)
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SyncConfigError(f"sync config not found: {config_path}") from exc
    except IsADirectoryError as exc:
        raise SyncConfigError(
            f"sync config path is a directory: {config_path}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise SyncConfigError(
            f"sync config is not valid UTF-8: {config_path}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise SyncConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    return parse_sync_config(raw, path=config_path)


def parse_sync_config(
    raw: dict[str, Any], *, path: Path | None = None
) -> SyncConfig:
    """Validate an already-parsed sync TOML document."""

    _reject_unknown(
        raw, {"protocol", "branch", "commit_message", "destinations"}, "sync config"
    )

    protocol = raw.get("protocol", DEFAULT_PROTOCOL)
    if not isinstance(protocol, str) or protocol not in _PROTOCOLS:
        allowed = ", ".join(sorted(_PROTOCOLS))
        raise SyncConfigError(f"protocol must be one of: {allowed}")

    branch = raw.get("branch")
    if branch is not None:
        if not isinstance(branch, str) or not branch.strip():
            raise SyncConfigError("branch must be a non-empty string")

    commit_message = raw.get("commit_message", DEFAULT_COMMIT_MESSAGE)
    if not isinstance(commit_message, str) or not commit_message.strip():
        raise SyncConfigError("commit_message must be a non-empty string")

    destinations = raw.get("destinations")
    if not isinstance(destinations, list) or not destinations:
        raise SyncConfigError("destinations must be a non-empty list of repositories")
    seen: set[str] = set()
    for item in destinations:
        if not isinstance(item, str) or not item.strip():
            raise SyncConfigError("destinations must contain only non-empty strings")
        if item in seen:
            raise SyncConfigError(f"duplicate destination: {item!r}")
        seen.add(item)

    return SyncConfig(
        destinations=tuple(destinations),
        protocol=protocol,
        branch=branch.strip() if branch else None,
        commit_message=commit_message,
        path=path,
    )


def _reject_unknown(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise SyncConfigError(f"unknown key(s) in {where}: {names}")


def discover_rules_config(source: str | Path) -> Path | None:
    """Find a rules file in *source* or its parent directory."""

    return _discover(source, _RULES_FILENAMES)


def discover_sync_config(source: str | Path) -> Path | None:
    """Find a sync file in *source* or its parent directory."""

    return _discover(source, _SYNC_FILENAMES)


def _discover(source: str | Path, names: tuple[str, ...]) -> Path | None:
    root = Path(source).expanduser().resolve()
    for directory in (root, root.parent):
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def config_locations(source: str | Path) -> str:
    """Describe where config files are looked up."""

    root = Path(source).expanduser().resolve()
    return f"{root} or {root.parent}"


def complete_remote(name: str, protocol: str = DEFAULT_PROTOCOL) -> str:
    """Complete a repository name into a clone URL.

    ``owner/repo`` (and ``host/owner/repo``) are expanded for ``ssh`` or
    ``https``. Full URLs and scp-like ``git@host:path`` values are kept as-is.
    """

    value = name.strip()
    if not value:
        raise SyncError("destination repository name is empty")
    if protocol not in _PROTOCOLS:
        allowed = ", ".join(sorted(_PROTOCOLS))
        raise SyncError(f"protocol must be one of: {allowed}")
    if _URL_RE.match(value) or _SCP_RE.match(value):
        return value

    host = "github.com"
    repository = value
    parts = value.split("/")
    if len(parts) >= 3 and "." in parts[0]:
        host = parts[0]
        repository = "/".join(parts[1:])
    if not repository.endswith(".git"):
        repository = f"{repository}.git"

    if protocol == "ssh":
        return f"git@{host}:{repository}"
    return f"https://{host}/{repository}"


def default_cache_dir() -> Path:
    """Return the default directory used for cached clones."""

    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "conformity" / "repos"


def repository_path(url: str, cache_dir: str | Path) -> Path:
    """Return the cache path for *url*."""

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", url).strip("-")[:60] or "repo"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return Path(cache_dir) / f"{slug}-{digest}"


def sync(
    source: str | Path,
    config: Config | str | Path,
    sync_config: SyncConfig | str | Path,
    *,
    dry_run: bool = False,
    no_push: bool = False,
    delete: bool = False,
    cache_dir: str | Path | None = None,
) -> SyncReport:
    """Copy the ``identical`` files of *source* into every destination.

    The source directory is used as-is; destination repositories are cloned or
    updated in a cache directory, verified with the existing conformance
    engine, committed with the configured message, and pushed unless
    ``no_push`` is set.
    """

    source_dir = Path(source)
    _require_directory(source_dir, "source")

    if not isinstance(config, Config):
        config = load_config(config)
    if not isinstance(sync_config, SyncConfig):
        sync_config = load_sync_config(sync_config)
    if not config.identical:
        raise SyncError(
            "the rules file defines no [files].identical entries to sync"
        )

    manifest = _manifest(source_dir, config.identical)
    cache = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    report = SyncReport(source=source_dir)

    for name in sync_config.destinations:
        url = complete_remote(name, sync_config.protocol)
        try:
            destination = _prepare_repo(url, cache, sync_config.branch)
            changed = _apply(
                source_dir,
                destination,
                manifest,
                config.identical,
                delete=delete,
                dry_run=dry_run,
            )
            if dry_run:
                message = (
                    f"would update {changed} file(s)" if changed else "up to date"
                )
                report.add(SyncResult(name, url, Status.PASS, message, changed))
                continue

            problem = _verify(destination, source_dir, manifest)
            if problem:
                report.add(
                    SyncResult(
                        name,
                        url,
                        Status.FAIL,
                        f"verification failed: {problem}",
                        changed,
                    )
                )
                continue

            committed = _commit(destination, sync_config.commit_message) if changed else False
            pushed = False
            if committed and not no_push:
                _push(destination, sync_config.branch)
                pushed = True

            message = _summary(changed, committed, pushed, no_push)
            report.add(SyncResult(name, url, Status.PASS, message, changed))
        except (SyncError, OSError, ConformanceError, ConfigError) as exc:
            report.add(
                SyncResult(name, url, Status.FAIL, _sanitize(str(exc)))
            )

    return report


def _require_directory(path: Path, label: str) -> None:
    if not path.exists():
        raise SyncError(f"{label} directory does not exist: {path}")
    if not path.is_dir():
        raise SyncError(f"{label} path is not a directory: {path}")


def _manifest(source: Path, patterns: tuple[str, ...]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for pattern in patterns:
        _reject_git_path(pattern)
        if not _is_glob(pattern) and (source / pattern).is_symlink():
            raise SyncError(f"refusing to sync symlink: {pattern}")
        matched = pattern_files(source, pattern)
        if not matched:
            raise SyncError(f"no source files matched {pattern!r}")
        for relative in matched:
            _reject_git_path(relative)
            _reject_symlink_path(source, relative)
            path = source / relative
            if not path.is_file():
                if not _is_glob(pattern):
                    raise SyncError(
                        f"source path not found or not a file: {pattern}"
                    )
                continue
            files[relative] = path
    if not files:
        raise SyncError("no source files matched the [files].identical rules")
    return files


def _apply(
    source: Path,
    destination: Path,
    manifest: dict[str, Path],
    patterns: tuple[str, ...],
    *,
    delete: bool,
    dry_run: bool,
) -> int:
    changed = 0
    for relative, source_file in manifest.items():
        target = destination / relative
        if _same_file(source_file, target):
            continue
        changed += 1
        if not dry_run:
            _copy_file(source_file, target)

    if delete:
        extras = _extraneous(destination, manifest, patterns)
        changed += len(extras)
        if extras and not dry_run:
            removed = [
                relative
                for relative in extras
                if _remove_file(destination / relative)
            ]
            _prune_empty(destination, removed)

    return changed


def _same_file(source: Path, target: Path) -> bool:
    try:
        if target.is_symlink() or not target.is_file():
            return False
        source_stat = source.stat()
        target_stat = target.stat()
        if source_stat.st_size != target_stat.st_size:
            return False
        source_exec = bool(source_stat.st_mode & 0o111)
        target_exec = bool(target_stat.st_mode & 0o111)
        if source_exec != target_exec:
            return False
        return sha256(source) == sha256(target)
    except OSError:
        return False


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    shutil.copy2(source, target, follow_symlinks=False)


def _remove_file(path: Path) -> bool:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return True
    if path.is_dir():
        shutil.rmtree(path)
        return True
    return False


def _extraneous(
    destination: Path, manifest: dict[str, Path], patterns: tuple[str, ...]
) -> list[str]:
    extras: set[str] = set()
    for pattern in patterns:
        for relative in pattern_files(destination, pattern):
            if relative in manifest:
                continue
            if _has_git_part(relative):
                continue
            path = destination / relative
            if path.is_symlink() or path.is_file():
                extras.add(relative)
    return sorted(extras)


def _prune_empty(destination: Path, removed: list[str]) -> None:
    parents: set[Path] = set()
    for relative in removed:
        parent = PurePosixPath(relative).parent
        while parent.parts and parent != PurePosixPath("."):
            parents.add(Path(*parent.parts))
            parent = parent.parent
    for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        directory = destination / parent
        if directory.is_dir() and not directory.is_symlink():
            try:
                directory.rmdir()
            except OSError:
                pass


def _prepare_repo(url: str, cache_dir: Path, branch: str | None) -> Path:
    path = repository_path(url, cache_dir)
    if (path / ".git").is_dir():
        _git(path, "fetch", "--prune", "--quiet", "origin")
        target = branch or _default_branch(path)
        _git(
            path,
            "checkout",
            "--force",
            "--quiet",
            "-B",
            target,
            f"origin/{target}",
        )
        _git(path, "reset", "--hard", "--quiet", f"origin/{target}")
        _git(path, "clean", "-fdxq")
        return path

    if path.exists():
        raise SyncError(
            f"cache path exists but is not a git repository: {path}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", "--quiet", url, str(path)])
    if branch:
        _git(path, "checkout", "--quiet", branch)
    return path


def _default_branch(path: Path) -> str:
    proc = _git(
        path,
        "symbolic-ref",
        "--short",
        "refs/remotes/origin/HEAD",
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        reference = proc.stdout.strip()
        if reference.startswith("origin/"):
            return reference[len("origin/") :]
    for candidate in ("main", "master"):
        proc = _git(
            path, "rev-parse", "--verify", f"origin/{candidate}", check=False
        )
        if proc.returncode == 0:
            return candidate
    raise SyncError(
        "could not determine the default branch; set branch in the sync config"
    )


def _commit(path: Path, message: str) -> bool:
    _git(path, "add", "--all")
    status = _git(path, "status", "--porcelain")
    if not status.stdout.strip():
        return False
    name, email = _identity(path)
    _git(
        path,
        "-c",
        f"user.name={name}",
        "-c",
        f"user.email={email}",
        "commit",
        "--quiet",
        "-m",
        message,
    )
    return True


def _identity(path: Path) -> tuple[str, str]:
    name = _git(path, "config", "user.name", check=False).stdout.strip()
    email = _git(path, "config", "user.email", check=False).stdout.strip()
    return (
        name or "conformity",
        email or "conformity@users.noreply.github.com",
    )


def _push(path: Path, branch: str | None) -> None:
    target = branch or _default_branch(path)
    _git(path, "push", "--quiet", "origin", f"HEAD:{target}")


def _verify(
    destination: Path, source: Path, manifest: dict[str, Path]
) -> str | None:
    verification = Config(identical=tuple(manifest))
    report = run_checks(destination, source, verification)
    if report.ok:
        return None
    problems = [
        f"{result.path}: {result.message}"
        for result in report.results
        if result.status is not Status.PASS
    ]
    preview = "; ".join(problems[:5])
    if len(problems) > 5:
        preview += "; ..."
    return preview


def _summary(
    changed: int, committed: bool, pushed: bool, no_push: bool
) -> str:
    if not changed and not committed:
        return "up to date"
    parts: list[str] = []
    if changed:
        parts.append(f"updated {changed} file(s)")
    if committed:
        parts.append("committed")
    if pushed:
        parts.append("pushed")
    elif committed and no_push:
        parts.append("push skipped")
    elif committed:
        parts.append("not pushed")
    return ", ".join(parts)


def _git(
    path: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", str(path), *args], check=check)


def _run(
    args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
    except FileNotFoundError as exc:
        raise SyncError("git executable not found") from exc
    if check and proc.returncode != 0:
        detail = _sanitize(proc.stderr.strip() or proc.stdout.strip())
        command = " ".join(args[:3])
        raise SyncError(detail or f"{command} failed")
    return proc


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _sanitize(value: str) -> str:
    return re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1***@", value)


def _is_glob(pattern: str) -> bool:
    return bool(_GLOB_CHARS.intersection(pattern))


def _has_git_part(relative: str) -> bool:
    return ".git" in PurePosixPath(relative).parts


def _reject_git_path(relative: str) -> None:
    if _has_git_part(relative):
        raise SyncError(f"refusing to sync .git paths: {relative!r}")


def _reject_symlink_path(root: Path, relative: str) -> None:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise SyncError(f"refusing to sync symlink: {relative}")
