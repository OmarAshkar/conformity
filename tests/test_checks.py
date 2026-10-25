from __future__ import annotations

from pathlib import Path

import pytest

from conformity.checks import ConformanceError, run_checks
from conformity.config import Config, ConfigError
from conformity.models import RuleKind, Status


def write(root: Path, relative: str, content: str = "same\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def config(**kwargs) -> Config:
    return Config(**kwargs)


def test_identical_files_pass(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "LICENSE", "GPL\n")
    write(source, "LICENSE", "GPL\n")

    report = run_checks(target, source, config(identical=("LICENSE",)))

    assert report.ok
    assert report.results[0].status is Status.PASS
    assert report.results[0].kind is RuleKind.IDENTICAL


def test_identical_file_missing_from_target(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    target.mkdir()
    write(source, "LICENSE")

    report = run_checks(target, source, config(identical=("LICENSE",)))

    assert not report.ok
    assert report.failures[0].message == "missing in target"


def test_identical_file_with_different_content(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "LICENSE", "one\n")
    write(source, "LICENSE", "two\n")

    report = run_checks(target, source, config(identical=("LICENSE",)))

    assert report.failures[0].message == "SHA-256 differs from source"


def test_identical_glob(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "src/a.py", "a\n")
    write(source, "src/a.py", "a\n")
    write(source, "src/b.py", "b\n")

    report = run_checks(target, source, config(identical=("src/**/*.py",)))

    statuses = {result.path: result.status for result in report.results}
    assert statuses["src/a.py"] is Status.PASS
    assert statuses["src/b.py"] is Status.FAIL


def test_identical_glob_without_matches_is_an_error(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    target.mkdir()
    source.mkdir()

    report = run_checks(target, source, config(identical=("src/**/*.py",)))

    assert report.errors[0].message == "no files matched in source"


def test_different_files_pass(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, ".env", "TOKEN=real\n")
    write(source, ".env", "TOKEN=placeholder\n")

    report = run_checks(target, source, config(different=(".env",)))

    assert report.ok
    assert report.results[0].status is Status.PASS


def test_different_file_must_not_match(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, ".env", "TOKEN=placeholder\n")
    write(source, ".env", "TOKEN=placeholder\n")

    report = run_checks(target, source, config(different=(".env",)))

    assert report.failures[0].message == "content is identical to source but must differ"


def test_different_file_must_exist(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    target.mkdir()
    write(source, ".env")

    report = run_checks(target, source, config(different=(".env",)))

    assert report.failures[0].message == "missing in target"


def test_required_file_glob_needs_at_least_one_match(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "src/a.py")
    write(target, "src/notes.txt")
    source.mkdir()

    report = run_checks(target, source, config(required_files=("src/*.py",)))

    assert report.ok
    assert report.results[0].kind is RuleKind.FILE
    assert report.results[0].message == "1 file match (src/a.py)"


def test_required_file_glob_without_matches_fails(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    (target / "src" / "not_a_file.py").mkdir(parents=True)
    source.mkdir()

    report = run_checks(target, source, config(required_files=("src/*.py",)))

    assert report.failures[0].message == "no files match pattern"


def test_required_literal_file(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "README.md")
    source.mkdir()

    report = run_checks(target, source, config(required_files=("README.md", "LICENSE")))

    statuses = {result.path: result.status for result in report.results}
    assert statuses["README.md"] is Status.PASS
    assert statuses["LICENSE"] is Status.FAIL


def test_required_directory_glob_needs_at_least_one_match(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    (target / "packages" / "one").mkdir(parents=True)
    write(target, "packages/one/README.md")
    source.mkdir()

    report = run_checks(target, source, config(required_dirs=("packages/*", "plugins/*")))

    statuses = {result.path: result.status for result in report.results}
    assert statuses["packages/*"] is Status.PASS
    assert statuses["plugins/*"] is Status.FAIL
    assert report.results[0].message == "1 directory match (packages/one)"


def test_structure_entries_support_globs(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    (target / "src").mkdir(parents=True)
    write(target, "src/a.py")
    source.mkdir()

    report = run_checks(
        target,
        source,
        config(structure={"src": ("*.py", "*.md")}),
    )

    statuses = {result.path: result.status for result in report.results}
    assert statuses["src/*.py"] is Status.PASS
    assert statuses["src/*.md"] is Status.FAIL


def test_required_directories(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    (target / "src").mkdir(parents=True)
    source.mkdir()

    report = run_checks(
        target, source, config(required_dirs=("src", "tests"))
    )

    statuses = {result.path: result.status for result in report.results}
    assert statuses["src"] is Status.PASS
    assert statuses["tests"] is Status.FAIL


def test_flat_structure_required_supports_paths_and_globs(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "src/__init__.py")
    write(target, "src/core.py")
    (target / "OUTPUTS").mkdir(parents=True)
    source.mkdir()

    report = run_checks(
        target,
        source,
        config(
            structure_required=(
                "src/__init__.py",
                "OUTPUTS/",
                "src/*py",
                "missing/",
                "*.md",
            )
        ),
    )

    statuses = {result.path: result.status for result in report.results}
    assert statuses["src/__init__.py"] is Status.PASS
    assert statuses["OUTPUTS/"] is Status.PASS
    assert statuses["src/*py"] is Status.PASS
    assert statuses["missing/"] is Status.FAIL
    assert statuses["*.md"] is Status.FAIL


def test_flat_structure_glob_with_trailing_slash_requires_directories(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    (target / "packages" / "one").mkdir(parents=True)
    write(target, "packages/file.py")
    source.mkdir()

    report = run_checks(
        target,
        source,
        config(structure_required=("packages/*/",)),
    )

    assert report.ok
    assert report.results[0].message == "1 directory match (packages/one)"


def test_flat_structure_literal_with_trailing_slash_must_be_directory(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "OUTPUTS")
    source.mkdir()

    report = run_checks(target, source, config(structure_required=("OUTPUTS/",)))

    assert report.failures[0].message == "not a directory"


def test_structure_requires_minimal_entries(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    (target / "src").mkdir(parents=True)
    write(target, "src/__init__.py", "")
    source.mkdir()

    report = run_checks(
        target,
        source,
        config(structure={"src": ("__init__.py", "core")}),
    )

    statuses = {result.path: result.status for result in report.results}
    assert statuses["src/__init__.py"] is Status.PASS
    assert statuses["src/core"] is Status.FAIL


def test_structure_missing_directory_reports_once(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    target.mkdir()
    source.mkdir()

    report = run_checks(target, source, config(structure={"src": ("a", "b")}))

    assert len(report.results) == 1
    assert report.results[0].message == "missing directory"


def test_config_from_path(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    target.mkdir()
    source.mkdir()
    path = tmp_path / "rules.toml"
    path.write_text('[directories]\nrequired = ["src"]\n', encoding="utf-8")

    report = run_checks(target, source, path)

    assert report.failures[0].path == "src"


def test_missing_config_file(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    target.mkdir()
    source.mkdir()

    with pytest.raises(ConfigError, match="not found"):
        run_checks(target, source, tmp_path / "rules.toml")


def test_missing_target_directory(tmp_path) -> None:
    with pytest.raises(ConformanceError, match="target directory does not exist"):
        run_checks(tmp_path / "missing", tmp_path, tmp_path / "rules.toml")


def test_missing_source_directory(tmp_path) -> None:
    with pytest.raises(ConformanceError, match="source directory does not exist"):
        run_checks(tmp_path, tmp_path / "missing", tmp_path / "rules.toml")
