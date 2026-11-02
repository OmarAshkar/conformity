# conformity

Check that a directory conforms to the standards of a source-of-truth directory.

`conformity` compares a target directory against a source directory and a small
TOML rule file. It can be used as a command line tool or through an interactive
TUI.

## Install

From PyPI:

```console
uv tool install conformity-cli
# or
pip install conformity-cli
```

From conda-forge (once the recipe is merged):

```console
conda install -c conda-forge conformity-cli
```

For development:

```console
uv sync
uv run conformity --help
```

Or install the checkout as a tool:

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

# Lenient check for bootstrapping a new target directory
conformity dir1/ sourcedir/ --config rules.toml --initial

# Open the TUI (also opens when no arguments are given)
conformity dir1/ sourcedir/ --config rules.toml --interactive
conformity
```

In the TUI, click a failed `identical` or `different` result to open a
confirmation prompt. Choose whether to overwrite the target copy with the source
copy, or the source copy with the target copy.

Exit codes: `0` conforms, `1` one or more checks failed, `2` invalid usage,
missing directory, or invalid config.

## Rules

Rules are read from the file passed to `--config`. Keeping the file in the
source directory is convenient, but it can live anywhere. All paths inside the
file are relative to the target and source directories. Glob patterns are
supported (`*`, `?`, `**`, `[...]`).

```toml
[files]
# Files that must exist in both directories with identical contents.
# Compared by SHA-256; the path must match too.
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

# Files required in the target. Literal paths must exist; glob patterns must
# match at least one file. A directory followed by an asterisk (for example
# "src/*") matches every file inside it.
match = [
  "README.md",
  "src/*.py",       # at least one Python file directly in src/
  "docs/**/*.md",   # at least one Markdown file anywhere under docs/
]

[directories]
# Directories that must exist in the target. Literal paths must exist; glob
# patterns must match at least one directory.
match = ["src", "tests", "docs", "packages/*"]
```

### Semantics

- `[files].identical`: every file matched in the source must exist at the same
  relative path in the target and have the same SHA-256 digest. A glob that
  matches a directory (such as `src/*`) includes every file inside it.
- `[files].different`: every file matched in the source must exist in the
  target and must have a different SHA-256 digest. Directory matches are
  expanded the same way as `identical`.
- `[files].match`: literal paths must be files in the target; glob patterns
  must match at least one file. Directories matched by a glob include the files
  inside them.
- `[directories].match`: literal directories must exist in the target; glob
  patterns must match at least one directory.
- If a glob matches no files in the source for `identical`/`different`, the rule
  reports an error instead of silently passing.
- Absolute paths, `..`, and backslash separators are rejected in rule paths.

### Initial mode

Pass `--initial` (or `initial=True` with the Python API) when starting a new
target directory. In this mode conformity is less strict:

- `identical` and `different` files only have to exist; their contents are not
  hashed.
- Directory rules are skipped, so missing directories are not reported yet.
- `[files].match` rules are still checked as usual.

File existence is still enforced, so rules pointing at missing files fail. Once
the directory is populated, run conformity again without `--initial` for the
full conformance check.

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
