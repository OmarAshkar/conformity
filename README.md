# conformity

Check that a directory conforms to the standards of a source-of-truth directory.

`conformity` compares a target directory against a source directory and a small
TOML rule file. It can be used as a command line tool or through an interactive
TUI.

## Install

```console
uv sync
uv run conformity --help
```

Or install it as a tool:

```console
uv tool install .
```

## Usage

```console
conformity TARGET SOURCE --config RULES.toml
```

`TARGET` is the directory being checked. `SOURCE` contains the reference files
used for comparison. The rules file is always passed explicitly with
`--config`, so there is no implicit discovery.

```console
# Check ./dir1 against ./sourcedir using ./rules.toml
conformity dir1/ sourcedir/ --config rules.toml

# Show one line per result
conformity dir1/ sourcedir/ --config rules.toml --plain

# Machine-readable output
conformity dir1/ sourcedir/ --config rules.toml --json

# Open the TUI (also opens when no arguments are given)
conformity dir1/ sourcedir/ --config rules.toml --interactive
conformity
```

Exit codes: `0` conforms, `1` one or more checks failed, `2` invalid usage,
missing directory, or invalid config.

## Rules

Rules are read from the file passed to `--config`. Keeping the file in the
source directory is convenient, but it can live anywhere. All paths inside the
file are relative to the target and source directories. Glob patterns are
supported (`*`, `?`, `**`, `[...]`).

```toml
# Files that must exist in both directories with identical contents.
# Compared by SHA-256; the path must match too.
[files]
identical = [
  "LICENSE",
  "pyproject.toml",
  ".github/workflows/**/*.yml",
]

# Files that must exist in both directories but must NOT have the same
# contents. Useful for secrets, local config, or per-project files.
different = [
  ".env",
  "config/local.toml",
]

# Directories that must exist in the target.
[directories]
required = ["src", "tests", "docs"]

# Minimal structure that must exist inside required directories.
# Each entry is checked for existence only.
[directories.structure]
src = ["__init__.py", "conformity"]
tests = ["conftest.py"]
```

### Semantics

- `identical`: every path matched in the source must exist at the same relative
  path in the target and have the same SHA-256 digest.
- `different`: every path matched in the source must exist in the target and
  must have a different SHA-256 digest.
- `required`: each listed directory must exist in the target.
- `structure`: each listed directory must exist and contain the listed entries.
- If a glob matches no files in the source, the rule reports an error instead of
  silently passing.
- Absolute paths, `..`, and backslash separators are rejected in rule paths.

## Library / interactive use

```python
from conformity import load_config, run_checks

# A Config object or a path to a TOML file is accepted.
report = run_checks("dir1", "sourcedir", "rules.toml")
print(report)

config = load_config("rules.toml")
report = run_checks("dir1", "sourcedir", config)

for result in report.results:
    print(result.status.value, result.path, result.message)
```

```console
$ uv run python
>>> from conformity import run_checks
>>> print(run_checks("dir1", "sourcedir", "rules.toml"))
```

## Development

```console
uv sync --extra dev
uv run pytest
```
