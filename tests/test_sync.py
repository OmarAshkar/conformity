from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conformity.cli import app, sync_app
from conformity.config import Config
from conformity.sync import (
    SyncConfig,
    SyncConfigError,
    SyncError,
    complete_remote,
    discover_rules_config,
    discover_sync_config,
    load_sync_config,
    parse_sync_config,
    repository_path,
    sync,
)

runner = CliRunner()


def write(root: Path, relative: str, content: str = "same\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def make_remote(
    tmp_path: Path, name: str = "remote", files: dict[str, str] | None = None
) -> Path:
    bare = tmp_path / "remotes" / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("init", "--bare", "--quiet", "--initial-branch=main", str(bare), cwd=tmp_path)

    seed = tmp_path / "seed" / name
    seed.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "--quiet", str(bare), str(seed), cwd=tmp_path)
    for relative, content in (files or {"README.md": "dest\n"}).items():
        write(seed, relative, content)
    git("add", "-A", cwd=seed)
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--quiet",
        "-m",
        "init",
        cwd=seed,
    )
    git("push", "--quiet", "origin", "HEAD:main", cwd=seed)
    return bare


def show(bare: Path, relative: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--git-dir", str(bare), "show", f"HEAD:{relative}"],
        cwd=bare.parent,
        capture_output=True,
        text=True,
    )


def last_subject(bare: Path) -> str:
    return git(
        "--git-dir", str(bare), "log", "-1", "--format=%s", cwd=bare.parent
    ).stdout.strip()


def test_complete_remote_ssh_by_default() -> None:
    assert complete_remote("owner/repo") == "git@github.com:owner/repo.git"
    assert complete_remote("owner/repo.git") == "git@github.com:owner/repo.git"
    assert complete_remote("github.com/owner/repo") == "git@github.com:owner/repo.git"


def test_complete_remote_https() -> None:
    assert (
        complete_remote("owner/repo", "https")
        == "https://github.com/owner/repo.git"
    )


def test_complete_remote_keeps_full_urls() -> None:
    url = "https://example.com/a/b.git"
    assert complete_remote(url, "ssh") == url
    assert complete_remote("git@example.com:a/b.git", "https") == "git@example.com:a/b.git"


def test_parse_sync_config() -> None:
    config = parse_sync_config(
        {
            "protocol": "https",
            "branch": "main",
            "commit_message": "msg",
            "destinations": ["owner/one", "owner/two"],
        }
    )

    assert config.protocol == "https"
    assert config.branch == "main"
    assert config.commit_message == "msg"
    assert config.destinations == ("owner/one", "owner/two")


@pytest.mark.parametrize(
    "raw, message",
    [
        ({}, "destinations"),
        ({"destinations": []}, "destinations"),
        ({"destinations": ["a"], "protocol": "ftp"}, "protocol"),
        ({"destinations": ["a", "a"]}, "duplicate"),
        ({"destinations": ["a"], "extra": 1}, "unknown key"),
    ],
)
def test_parse_sync_config_errors(raw, message) -> None:
    with pytest.raises(SyncConfigError, match=message):
        parse_sync_config(raw)


def test_load_sync_config_sets_path(tmp_path) -> None:
    path = write(tmp_path, "conformity-sync.toml", 'destinations = ["owner/repo"]\n')

    config = load_sync_config(path)

    assert config.path == path
    assert config.destinations == ("owner/repo",)


def test_discovery_searches_source_then_parent(tmp_path) -> None:
    parent = tmp_path
    source = parent / "source"
    source.mkdir()
    write(parent, "rules.toml", '[files]\nidentical = ["LICENSE"]\n')
    write(parent, "sync.toml", 'destinations = ["owner/repo"]\n')

    assert discover_rules_config(source) == parent / "rules.toml"
    assert discover_sync_config(source) == parent / "sync.toml"


def test_discovery_prefers_source(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write(source, "conformity.toml", '[files]\nidentical = ["LICENSE"]\n')
    write(tmp_path, "conformity.toml", '[files]\nidentical = ["README.md"]\n')

    assert discover_rules_config(source) == source / "conformity.toml"


def test_sync_copies_commits_and_pushes(tmp_path) -> None:
    bare = make_remote(tmp_path)
    source = tmp_path / "source"
    write(source, "LICENSE", "GPL\n")
    write(source, "docs/guide.md", "guide\n")
    rules = Config(identical=("LICENSE", "docs/*.md"))
    config = SyncConfig(destinations=(bare.as_uri(),))

    report = sync(source, rules, config, cache_dir=tmp_path / "cache")

    assert report.ok
    assert report.results[0].changed == 2
    assert show(bare, "LICENSE").stdout == "GPL\n"
    assert show(bare, "docs/guide.md").stdout == "guide\n"
    assert last_subject(bare) == "autoupdate repo structure"


def test_sync_is_idempotent(tmp_path) -> None:
    bare = make_remote(tmp_path)
    source = tmp_path / "source"
    write(source, "LICENSE", "GPL\n")
    rules = Config(identical=("LICENSE",))
    config = SyncConfig(destinations=(bare.as_uri(),))

    first = sync(source, rules, config, cache_dir=tmp_path / "cache")
    second = sync(source, rules, config, cache_dir=tmp_path / "cache")

    assert first.results[0].changed == 1
    assert second.results[0].changed == 0
    assert second.results[0].message == "up to date"


def test_sync_dry_run_does_not_write_or_push(tmp_path) -> None:
    bare = make_remote(tmp_path)
    source = tmp_path / "source"
    write(source, "LICENSE", "GPL\n")
    rules = Config(identical=("LICENSE",))
    config = SyncConfig(destinations=(bare.as_uri(),))

    report = sync(source, rules, config, dry_run=True, cache_dir=tmp_path / "cache")

    assert report.ok
    assert report.results[0].changed == 1
    assert "would update" in report.results[0].message
    assert show(bare, "LICENSE").returncode != 0


def test_sync_no_push_keeps_remote_unchanged(tmp_path) -> None:
    bare = make_remote(tmp_path)
    source = tmp_path / "source"
    write(source, "LICENSE", "GPL\n")
    rules = Config(identical=("LICENSE",))
    config = SyncConfig(destinations=(bare.as_uri(),))
    cache = tmp_path / "cache"

    report = sync(source, rules, config, no_push=True, cache_dir=cache)

    assert report.ok
    assert "push skipped" in report.results[0].message
    assert show(bare, "LICENSE").returncode != 0
    cached = repository_path(bare.as_uri(), cache)
    assert (cached / "LICENSE").read_text(encoding="utf-8") == "GPL\n"


def test_sync_delete_removes_extraneous_files_only_when_asked(tmp_path) -> None:
    bare = make_remote(
        tmp_path,
        files={"README.md": "dest\n", "docs/old.md": "old\n"},
    )
    source = tmp_path / "source"
    write(source, "docs/new.md", "new\n")
    rules = Config(identical=("docs/*.md",))
    config = SyncConfig(destinations=(bare.as_uri(),))

    without_delete = sync(
        source, rules, config, cache_dir=tmp_path / "cache-one"
    )
    assert without_delete.ok
    assert show(bare, "docs/old.md").returncode == 0

    with_delete = sync(
        source,
        rules,
        config,
        delete=True,
        cache_dir=tmp_path / "cache-two",
    )
    assert with_delete.ok
    assert with_delete.results[0].changed == 1
    assert show(bare, "docs/old.md").returncode != 0
    assert show(bare, "docs/new.md").stdout == "new\n"


def test_sync_literal_directory(tmp_path) -> None:
    bare = make_remote(tmp_path)
    source = tmp_path / "source"
    write(source, "src/a.py", "a\n")
    write(source, "src/nested/b.py", "b\n")
    rules = Config(identical=("src",))
    config = SyncConfig(destinations=(bare.as_uri(),))

    report = sync(source, rules, config, cache_dir=tmp_path / "cache")

    assert report.ok
    assert show(bare, "src/a.py").stdout == "a\n"
    assert show(bare, "src/nested/b.py").stdout == "b\n"


def test_sync_rejects_nested_symlinks(tmp_path) -> None:
    source = tmp_path / "source"
    write(source, "src/a.py", "a\n")
    outside = write(tmp_path, "outside.txt", "secret\n")
    (source / "src" / "link").symlink_to(outside)
    rules = Config(identical=("src",))
    config = SyncConfig(destinations=("owner/repo",))

    with pytest.raises(SyncError, match="symlink"):
        sync(source, rules, config, cache_dir=tmp_path / "cache")


def test_sync_rejects_symlinks(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = write(tmp_path, "outside.txt", "secret\n")
    (source / "LICENSE").symlink_to(outside)
    rules = Config(identical=("LICENSE",))
    config = SyncConfig(destinations=("owner/repo",))

    with pytest.raises(SyncError, match="symlink"):
        sync(source, rules, config, cache_dir=tmp_path / "cache")


def test_sync_rejects_git_paths(tmp_path) -> None:
    source = tmp_path / "source"
    write(source, ".git/config", "x\n")
    rules = Config(identical=(".git/config",))
    config = SyncConfig(destinations=("owner/repo",))

    with pytest.raises(SyncError, match=".git"):
        sync(source, rules, config, cache_dir=tmp_path / "cache")


def test_sync_requires_identical_rules(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    config = SyncConfig(destinations=("owner/repo",))

    with pytest.raises(SyncError, match="identical"):
        sync(source, Config(match_files=("LICENSE",)), config)


def test_sync_continues_after_destination_failure(tmp_path) -> None:
    bare = make_remote(tmp_path)
    source = tmp_path / "source"
    write(source, "LICENSE", "GPL\n")
    rules = Config(identical=("LICENSE",))
    config = SyncConfig(
        destinations=("file:///does/not/exist.git", bare.as_uri())
    )

    report = sync(source, rules, config, cache_dir=tmp_path / "cache")

    assert not report.ok
    assert report.results[0].status.value == "fail"
    assert report.results[1].status.value == "pass"
    assert show(bare, "LICENSE").stdout == "GPL\n"


def test_cli_sync_with_discovered_configs(tmp_path, monkeypatch) -> None:
    bare = make_remote(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    write(source, "LICENSE", "GPL\n")
    write(tmp_path, "rules.toml", '[files]\nidentical = ["LICENSE"]\n')
    write(
        tmp_path,
        "conformity-sync.toml",
        f'destinations = ["{bare.as_uri()}"]\n',
    )
    monkeypatch.setenv("CONFORMITY_CACHE_DIR", str(tmp_path / "cache"))

    result = runner.invoke(sync_app, [str(source), "--yes"])

    assert result.exit_code == 0, result.output
    assert "Sync complete" in result.stdout
    assert show(bare, "LICENSE").stdout == "GPL\n"


def test_app_dispatches_sync(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["conformity", "sync", "--help"])

    with pytest.raises(SystemExit) as exc:
        app()

    assert exc.value.code == 0
    assert "Replicate identical files" in capsys.readouterr().out


def test_cli_sync_missing_configs(tmp_path) -> None:
    result = runner.invoke(sync_app, [str(tmp_path)])

    assert result.exit_code == 2
    assert "no rules file found" in result.output
