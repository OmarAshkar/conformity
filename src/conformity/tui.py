"""Interactive Textual interface for conformity."""

from __future__ import annotations

import shutil
from pathlib import Path

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static
from rich.text import Text

from conformity.checks import ConformanceError, run_checks
from conformity.config import ConfigError
from conformity.models import Report, RuleKind, Status

_GLOB_CHARS = frozenset("*?[")

_STATUS_COLORS = {
    Status.PASS: "green",
    Status.FAIL: "red",
    Status.ERROR: "yellow",
}


class UpdatePrompt(ModalScreen[str | None]):
    """Ask which copy of a file should be overwritten."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    UpdatePrompt {
        align: center middle;
    }

    #dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: thick $background 80%;
        background: $surface;
    }

    #dialog Label {
        margin-bottom: 1;
    }

    #dialog Horizontal {
        height: auto;
        align: center middle;
    }

    #dialog Button {
        margin: 0 1;
    }
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Update {self._path}?")
            yield Label("Choose which copy should be overwritten.")
            with Horizontal():
                yield Button("Yes, update target", id="target", variant="warning")
                yield Button("Yes, update source", id="source", variant="warning")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in {"target", "source"}:
            self.dismiss(event.button.id)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ResultsTable(DataTable):
    """A results table that opens a row on the first click."""

    async def _on_click(self, event: events.Click) -> None:
        meta = event.style.meta
        if (
            self.cursor_type == "row"
            and "row" in meta
            and "column" in meta
            and meta["row"] >= 0
        ):
            row_index = meta["row"]
            column_index = max(meta["column"], 0)
            self.move_cursor(row=row_index, column=column_index, animate=True)
            row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
            self.post_message(DataTable.RowSelected(self, row_index, row_key))
            event.stop()
            return
        await super()._on_click(event)


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

    #hint {
        height: 1;
        margin-top: 1;
        color: $text-muted;
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
        yield ResultsTable(id="results", cursor_type="row")
        yield Static(
            "Click a failed result to update the target or source copy.",
            id="hint",
        )
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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self._report is None:
            return
        index = event.cursor_row
        if not 0 <= index < len(self._report.results):
            return
        result = self._report.results[index]
        if result.status is not Status.FAIL:
            self.notify(
                "Only failed checks can be updated.",
                title="Nothing to update",
                severity="warning",
            )
            return
        if result.kind not in {
            RuleKind.IDENTICAL,
            RuleKind.DIFFERENT,
            RuleKind.FILE,
        }:
            self.notify(
                "This result does not point to a single file.",
                title="Cannot update",
                severity="warning",
            )
            return
        if _GLOB_CHARS.intersection(result.path):
            self.notify(
                "Select a concrete file, not a glob pattern.",
                title="Cannot update",
                severity="warning",
            )
            return
        self.push_screen(
            UpdatePrompt(result.path),
            lambda choice: self._apply_update(result.path, choice),
        )

    def _apply_update(self, relative: str, choice: str | None) -> None:
        if choice not in {"target", "source"}:
            return
        report = self._report
        if report is None or report.target is None or report.source is None:
            return

        source_file = report.source / relative
        target_file = report.target / relative
        if choice == "target":
            origin, destination = source_file, target_file
        else:
            origin, destination = target_file, source_file

        if not origin.is_file():
            self.notify(
                f"Cannot update: {origin} is not a file.",
                title="Update failed",
                severity="error",
            )
            return
        if destination.exists() and not destination.is_file():
            self.notify(
                f"Cannot update: {destination} is not a file.",
                title="Update failed",
                severity="error",
            )
            return

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
        except OSError as exc:
            self.notify(
                f"Could not update {destination}: {exc}",
                title="Update failed",
                severity="error",
            )
            return

        self.notify(
            f"Updated {destination}.",
            title="Updated",
            severity="information",
        )
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
