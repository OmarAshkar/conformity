from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from conformity.cli import app

runner = CliRunner()


def write(root: Path, relative: str, content: str = "same\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def make_pair(
    tmp_path: Path, *, match: bool = True
) -> tuple[Path, Path, Path]:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "LICENSE", "GPL\n" if match else "MIT\n")
    write(source, "LICENSE", "GPL\n")
    rules = write(tmp_path, "rules.toml", '[files]\nidentical = ["LICENSE"]\n')
    return target, source, rules


def test_conforming_plain_output(tmp_path) -> None:
    target, source, rules = make_pair(tmp_path)

    result = runner.invoke(
        app, [str(target), str(source), "--config", str(rules), "--plain"]
    )

    assert result.exit_code == 0
    assert "PASS  identical LICENSE" in result.stdout
    assert "1 passed, 0 failed, 0 errors" in result.stdout


def test_non_conforming_exit_code(tmp_path) -> None:
    target, source, rules = make_pair(tmp_path, match=False)

    result = runner.invoke(
        app, [str(target), str(source), "--config", str(rules), "--plain"]
    )

    assert result.exit_code == 1
    assert "SHA-256 differs from source" in result.stdout


def test_json_output(tmp_path) -> None:
    target, source, rules = make_pair(tmp_path, match=False)

    result = runner.invoke(
        app, [str(target), str(source), "--config", str(rules), "--json"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["summary"]["fail"] == 1
    assert payload["results"][0]["status"] == "fail"


def test_config_option_is_required(tmp_path) -> None:
    target, source, _ = make_pair(tmp_path)

    result = runner.invoke(app, [str(target), str(source)])

    assert result.exit_code == 2
    assert "--config is required" in result.output


def test_missing_config_file_exits_two(tmp_path) -> None:
    target, source, _ = make_pair(tmp_path)

    result = runner.invoke(
        app, [str(target), str(source), "--config", str(tmp_path / "nope.toml")]
    )

    assert result.exit_code == 2
    assert "does not exist" in result.output


def test_only_one_directory_is_an_error(tmp_path) -> None:
    result = runner.invoke(app, [str(tmp_path)])

    assert result.exit_code == 2
    assert "provided together" in result.output


def test_initial_mode_skips_content_and_directory_checks(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "LICENSE", "MIT\n")
    write(source, "LICENSE", "GPL\n")
    rules = write(
        tmp_path,
        "rules.toml",
        '[files]\nidentical = ["LICENSE"]\n[directories]\nrequired = ["src"]\n',
    )

    result = runner.invoke(
        app,
        [
            str(target),
            str(source),
            "--config",
            str(rules),
            "--plain",
            "--initial",
        ],
    )

    assert result.exit_code == 0
    assert "PASS  identical LICENSE" in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.startswith("conformity ")


def test_plain_and_json_conflict(tmp_path) -> None:
    target, source, rules = make_pair(tmp_path)

    result = runner.invoke(
        app,
        [str(target), str(source), "--config", str(rules), "--plain", "--json"],
    )

    assert result.exit_code == 2
    assert "cannot be used together" in result.output
