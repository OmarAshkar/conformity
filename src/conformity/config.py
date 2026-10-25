"""Load and validate TOML rule files."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

class ConfigError(ValueError):
    """Raised when a conformance config is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Config:
    """Rules that a target directory is checked against."""

    identical: tuple[str, ...] = ()
    different: tuple[str, ...] = ()
    required_files: tuple[str, ...] = ()
    required_dirs: tuple[str, ...] = ()
    structure: dict[str, tuple[str, ...]] = field(default_factory=dict)
    path: Path | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.identical
            or self.different
            or self.required_files
            or self.required_dirs
            or self.structure
        )


def load_config(path: str | Path) -> Config:
    """Read a TOML config file and validate its schema."""

    config_path = Path(path)
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except IsADirectoryError as exc:
        raise ConfigError(f"config path is a directory: {config_path}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"config file is not valid UTF-8: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    return parse_config(raw, path=config_path)


def parse_config(raw: dict[str, Any], *, path: Path | None = None) -> Config:
    """Validate an already-parsed TOML document."""

    _reject_unknown(raw, {"files", "directories"}, "top level")

    files = raw.get("files", {})
    if not isinstance(files, dict):
        raise ConfigError("[files] must be a table")
    _reject_unknown(files, {"identical", "different", "required"}, "[files]")

    directories = raw.get("directories", {})
    if not isinstance(directories, dict):
        raise ConfigError("[directories] must be a table")
    _reject_unknown(directories, {"required", "structure"}, "[directories]")

    identical = _patterns(files.get("identical", []), "[files].identical")
    different = _patterns(files.get("different", []), "[files].different")
    required_files = _patterns(files.get("required", []), "[files].required")
    required_dirs = _patterns(directories.get("required", []), "[directories].required")

    raw_structure = directories.get("structure", {})
    if not isinstance(raw_structure, dict):
        raise ConfigError("[directories.structure] must be a table")
    structure: dict[str, tuple[str, ...]] = {}
    for directory, entries in raw_structure.items():
        if not isinstance(directory, str):
            raise ConfigError("[directories.structure] keys must be strings")
        _validate_pattern(directory, f"[directories.structure].{directory}")
        structure[directory] = _patterns(
            entries, f"[directories.structure].{directory}"
        )

    config = Config(
        identical=identical,
        different=different,
        required_files=required_files,
        required_dirs=required_dirs,
        structure=structure,
        path=path,
    )
    if config.is_empty:
        raise ConfigError(
            f"{path or 'config'} defines no rules; add [files] or [directories]"
        )
    return config


def _reject_unknown(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigError(f"unknown key(s) in {where}: {names}")


def _patterns(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{where} must be a list of relative paths")
    for item in value:
        if not isinstance(item, str):
            raise ConfigError(f"{where} must contain only strings")
        _validate_pattern(item, where)
    return tuple(value)


def _validate_pattern(pattern: str, where: str) -> None:
    if not pattern.strip():
        raise ConfigError(f"{where} contains an empty path")
    if "\\" in pattern:
        raise ConfigError(f"{where} must use '/' as the path separator: {pattern!r}")
    path = PurePosixPath(pattern)
    if path.is_absolute() or pattern.startswith("~"):
        raise ConfigError(f"{where} must use relative paths: {pattern!r}")
    if ".." in path.parts:
        raise ConfigError(f"{where} must not escape the directory: {pattern!r}")
