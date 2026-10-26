"""Interactive Textual interface for conformity."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static
from rich.text import Text

from conformity.checks import ConformanceError, run_checks
from conformity.config import ConfigError
from conformity.models import Report, Status

_STATUS_COLORS = {
    Status.PASS: "green",
    Status.FAIL: "red",
    Status.ERROR: "yellow",
}


class ConformityApp(App[None]):
    """A small TUI for choosing two directories and reviewing the report."""

    TITLE = "conformity"
    SUB_TITLE = "directory conformance checker"

    CSS = """
    Screen {
        padding: 1 2;
    }

    #form {
        height: auto;
    }

    #form Label {
        margin-top: 1;
        color: $text-muted;
    }

    Input {
        margin-bottom: 1;
    }

    #actions {
        height: auto;
        align: left middle;
        margin-top: 1;
    }

    #run {
        margin-right: 2;
    }

    #summary {
        height: 3;
        content-align: left middle;
    }

    DataTable {
        height: 1fr;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("ctrl+r", "run", "Run checks"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        target: str | Path | None = None,
        source: str | Path | None = None,
        config: str | Path | None = None,
        initial: bool = False,
    ) -> None:
        super().__init__()
        self._initial_target = str(target) if target else str(Path.cwd())
        self._initial_source = str(source) if source else ""
        self._initial_config = str(config) if config else ""
        self._initial_mode = initial
        self._report: Report | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="form"):
            yield Label("Target directory (the directory to check)")
            yield Input(
                value=self._initial_target,
                placeholder="/path/to/dir1",
                id="target",
            )
            yield Label("Source directory (source of truth and rules)")
            yield Input(
                value=self._initial_source,
                placeholder="/path/to/sourcedir",
                id="source",
            )
            yield Label("Rules file (required)")
            yield Input(
                value=self._initial_config,
                placeholder="/path/to/rules.toml",
                id="config",
            )
            with Horizontal(id="actions"):
                yield Button("Run checks", id="run", variant="primary")
                yield Static("", id="summary")
        yield DataTable(id="results")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Status", "Rule", "Path", "Detail")
        table.zebra_stripes = True
        if self._initial_source:
            self.call_after_refresh(self.action_run)
        else:
            self.query_one("#target", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.action_run()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_run()

    def action_run(self) -> None:
        target = self.query_one("#target", Input).value.strip()
        source = self.query_one("#source", Input).value.strip()
        config = self.query_one("#config", Input).value.strip()

        if not target or not source or not config:
            self.notify(
                "Enter a target directory, a source directory, and a rules file.",
                title="Missing paths",
                severity="error",
            )
            return

        try:
            report = run_checks(
                target, source, Path(config), initial=self._initial_mode
            )
        except (ConfigError, ConformanceError) as exc:
            self.notify(str(exc), title="Cannot run checks", severity="error")
            return

        self._report = report
        self._update_table(report)
        self._update_summary(report)

    def _update_table(self, report: Report) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=False)
        for result in report.results:
            status = Text(result.status.value.upper(), style=_STATUS_COLORS[result.status])
            table.add_row(status, result.kind.value, result.path, result.message)

    def _update_summary(self, report: Report) -> None:
        counts = report.counts()
        summary = Text()
        summary.append(f"{counts[Status.PASS]} passed", style="green")
        summary.append("   ")
        summary.append(f"{counts[Status.FAIL]} failed", style="red")
        summary.append("   ")
        summary.append(f"{counts[Status.ERROR]} errors", style="yellow")
        summary.append("   ")
        if report.ok:
            summary.append("Conforms.", style="bold green")
        else:
            summary.append("Does not conform.", style="bold red")
        self.query_one("#summary", Static).update(summary)


def run_tui(
    target: str | Path | None = None,
    source: str | Path | None = None,
    config: str | Path | None = None,
    initial: bool = False,
) -> None:
    """Open the interactive TUI."""

    ConformityApp(
        target=target, source=source, config=config, initial=initial
    ).run()
