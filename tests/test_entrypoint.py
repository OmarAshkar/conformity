from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points


def test_console_script_points_at_typer_app() -> None:
    scripts = entry_points(group="console_scripts")
    conformity = next(script for script in scripts if script.name == "conformity")

    assert conformity.value == "conformity.cli:app"


def test_module_entrypoint() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "conformity", "--version"],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0
    assert result.stdout.startswith("conformity ")
