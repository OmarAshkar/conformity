"""Command line entry point for conformity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from conformity import __version__
from conformity.checks import ConformanceError, run_checks
from conformity.config import ConfigError
from conformity.models import Report, Status

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    rich_markup_mode="rich",
    help="Check that a directory conforms to the standards of a source directory.",
)

_STATUS_STYLE = {
    Status.PASS: "green",
    Status.FAIL: "red",
    Status.ERROR: "yellow",
}


@app.command()
def main(
    target: Annotated[
        Path | None,
        typer.Argument(
            metavar="TARGET",
            help="Directory to check. Omit both directories to open the TUI.",
        ),
    ] = None,
    source: Annotated[
        Path | None,
        typer.Argument(
            metavar="SOURCE",
            help="Source-of-truth directory containing the reference files.",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to the TOML rules file (required to run checks).",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
    plain: Annotated[
        bool,
        typer.Option("--plain", help="Print one line per result instead of a table."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the report as JSON."),
    ] = False,
    interactive: Annotated[
        bool,
        typer.Option("--interactive", "-i", help="Open the TUI, pre-filled with any paths."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the version and exit."),
    ] = False,
) -> None:
    """Check TARGET against the rules defined in --config.

    SOURCE contains the reference files used for comparison. The rules file is
    passed explicitly with --config, for example ``--config rules.toml``.

    With no arguments, conformity opens an interactive TUI where the config
    path can be entered too.
    """

    if version:
        typer.echo(f"conformity {__version__}")
        raise typer.Exit()

    if plain and as_json:
        typer.echo("error: --plain and --json cannot be used together", err=True)
        raise typer.Exit(code=2)

    if interactive or (target is None and source is None):
        from conformity.tui import run_tui

        run_tui(target=target, source=source, config=config)
        return

    if target is None or source is None:
        typer.echo("error: TARGET and SOURCE must be provided together", err=True)
        raise typer.Exit(code=2)

    if config is None:
        typer.echo("error: --config is required to run checks", err=True)
        raise typer.Exit(code=2)

    try:
        report = run_checks(target, source, config)
    except (ConfigError, ConformanceError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if as_json:
        typer.echo(json.dumps(_as_dict(report), indent=2))
    elif plain:
        _print_plain(report)
    else:
        _print_table(report)

    raise typer.Exit(code=0 if report.ok else 1)


def _as_dict(report: Report) -> dict[str, object]:
    return {
        "ok": report.ok,
        "target": str(report.target) if report.target else None,
        "source": str(report.source) if report.source else None,
        "config": str(report.config_path) if report.config_path else None,
        "summary": {status.value: count for status, count in report.counts().items()},
        "results": [
            {
                "kind": result.kind.value,
                "path": result.path,
                "status": result.status.value,
                "message": result.message,
            }
            for result in report.results
        ],
    }


def _print_plain(report: Report) -> None:
    typer.echo(str(report))


def _print_table(report: Report) -> None:
    console = Console()
    console.print()
    console.print("[bold]Conformity report[/bold]")
    if report.target:
        console.print(f"  target: [cyan]{report.target}[/cyan]")
    if report.source:
        console.print(f"  source: [cyan]{report.source}[/cyan]")
    if report.config_path:
        console.print(f"  config: [cyan]{report.config_path}[/cyan]")

    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("Status", no_wrap=True, width=8)
    table.add_column("Rule", no_wrap=True, width=10)
    table.add_column("Path", overflow="fold")
    table.add_column("Detail", overflow="fold")

    for result in report.results:
        style = _STATUS_STYLE[result.status]
        table.add_row(
            Text(result.status.value.upper(), style=style),
            result.kind.value,
            result.path,
            result.message,
        )

    console.print()
    console.print(table)

    counts = report.counts()
    summary = Text()
    summary.append(f"{counts[Status.PASS]} passed", style="green")
    summary.append("  ")
    summary.append(f"{counts[Status.FAIL]} failed", style="red")
    summary.append("  ")
    summary.append(f"{counts[Status.ERROR]} errors", style="yellow")
    console.print(summary)

    if report.ok:
        console.print("[bold green]Conforms.[/bold green]")
    else:
        console.print("[bold red]Does not conform.[/bold red]")


if __name__ == "__main__":
    app()
