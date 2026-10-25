from __future__ import annotations

import tomllib

import pytest

from conformity.config import ConfigError, load_config, parse_config


def test_parse_full_config() -> None:
    raw = tomllib.loads(
        """
        [files]
        identical = ["LICENSE", "src/**/*.py"]
        different = [".env"]
        required = ["src/*.py", "docs/**/*.md"]

        [directories]
        required = ["src", "tests", "packages/*"]

        [directories.structure]
        src = ["__init__.py", "*.py"]
        """
    )

    config = parse_config(raw)

    assert config.identical == ("LICENSE", "src/**/*.py")
    assert config.different == (".env",)
    assert config.required_files == ("src/*.py", "docs/**/*.md")
    assert config.required_dirs == ("src", "tests", "packages/*")
    assert config.structure == {"src": ("__init__.py", "*.py")}
    assert not config.is_empty


def test_load_config_sets_path(tmp_path) -> None:
    path = tmp_path / "conformity.toml"
    path.write_text('[files]\nidentical = ["README.md"]\n', encoding="utf-8")

    config = load_config(path)

    assert config.path == path
    assert config.identical == ("README.md",)


def test_empty_config_is_rejected() -> None:
    with pytest.raises(ConfigError, match="defines no rules"):
        parse_config({})


def test_unknown_top_level_key() -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config({"files": {}, "extra": []})


def test_missing_config_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_invalid_toml(tmp_path) -> None:
    path = tmp_path / "conformity.toml"
    path.write_text("[files\nidentical = 1", encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)


@pytest.mark.parametrize("pattern", ["/etc/passwd", "../outside", "", "dir\\file"])
def test_unsafe_patterns_are_rejected(pattern: str) -> None:
    with pytest.raises(ConfigError):
        parse_config({"files": {"identical": [pattern]}})
    with pytest.raises(ConfigError):
        parse_config({"files": {"required": [pattern]}})


def test_wrong_value_types_are_rejected() -> None:
    with pytest.raises(ConfigError, match="list"):
        parse_config({"files": {"identical": "README.md"}})
    with pytest.raises(ConfigError, match="strings"):
        parse_config({"files": {"identical": [1]}})
    with pytest.raises(ConfigError, match="table"):
        parse_config({"directories": []})
