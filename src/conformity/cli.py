"""Command line entry point for conformity."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from conformity import __version__
from conformity.checks import ConformanceError, run_checks
from conformity.config import Config, ConfigError, load_config
from conformity.models import Report, Status
from conformity.sync import (
    SyncConfigError,
    SyncError,
    SyncReport,
    complete_remote,
    config_locations,
    discover_rules_config,
    discover_sync_config,
    load_sync_config,
    sync,
)


class _ConformityApp(typer.Typer):
    """Dispatch ``conformity sync`` while keeping the check command positional.

    The check command takes TARGET and SOURCE positionally, so registering sync
    as a Typer subcommand would make the first positional argument ambiguous.
    Dispatching on the first raw argument keeps the existing CLI unchanged.
    """

    def __call__(self, *args: object, **kwargs: object) -> None:
        argv = sys.argv[1:]
        if argv and argv[0] == "sync":
            sync_app(args=argv[1:])
            return
        super().__call__(*args, **kwargs)


app = _ConformityApp(
    add_completion=False,
    no_args_is_help=False,
    rich_markup_mode="rich",
    help=(
        "Check that a directory conforms to the standards of a source directory. "
        "Run 'conformity sync' to replicate a source directory into Git repositories."
    ),
)

sync_app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    rich_markup_mode="rich",
    help="Replicate a source directory into one or more Git repositories.",
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
    initial: Annotated[
        bool,
        typer.Option(
            "--initial",
            help="Only check that listed files exist; skip content and directory checks.",
        ),
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

        run_tui(target=target, source=source, config=config, initial=initial)
        return

    if target is None or source is None:
        typer.echo("error: TARGET and SOURCE must be provided together", err=True)
        raise typer.Exit(code=2)

    if config is None:
        typer.echo("error: --config is required to run checks", err=True)
        raise typer.Exit(code=2)

    try:
        report = run_checks(target, source, config, initial=initial)
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


@sync_app.command("sync")
def sync_command(
    source: Annotated[
        Path,
        typer.Argument(
            metavar="SOURCE",
            help="Source-of-truth directory (default: the current directory).",
        ),
    ] = Path("."),
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Rules TOML file. Discovered in SOURCE or its parent when omitted.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
    sync_config_file: Annotated[
        Path | None,
        typer.Option(
            "--sync-config",
            "-s",
            help="Sync TOML file. Discovered in SOURCE or its parent when omitted.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
    protocol: Annotated[
        str | None,
        typer.Option("--protocol", help="Override the sync protocol (ssh or https)."),
    ] = None,
    branch: Annotated[
        str | None,
        typer.Option("--branch", help="Override the destination branch."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change without writing or pushing."),
    ] = False,
    no_push: Annotated[
        bool,
        typer.Option("--no-push", help="Commit changes locally but do not push."),
    ] = False,
    delete: Annotated[
        bool,
        typer.Option(
            "--delete",
            help="Remove destination files matched by identical rules but absent from SOURCE.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the report as JSON."),
    ] = False,
    plain: Annotated[
        bool,
        typer.Option("--plain", help="Print one line per destination instead of a table."),
    ] = False,
) -> None:
    """Replicate identical files from SOURCE into the configured repositories.

    The source directory is read as-is. Destinations are cloned or updated in a
    cache directory, verified with the conformance engine, committed with
    "autoupdate repo structure", and pushed. Rules and sync config are
    discovered in SOURCE or its parent when not passed explicitly.
    """

    if plain and as_json:
        typer.echo("error: --plain and --json cannot be used together", err=True)
        raise typer.Exit(code=2)

    if not source.exists() or not source.is_dir():
        typer.echo(f"error: source directory does not exist: {source}", err=True)
        raise typer.Exit(code=2)

    rules_path = config or discover_rules_config(source)
    sync_path = sync_config_file or discover_sync_config(source)

    if rules_path is None:
        typer.echo(
            f"error: no rules file found in {config_locations(source)}; pass --config",
            err=True,
        )
        raise typer.Exit(code=2)
    if sync_path is None:
        typer.echo(
            f"error: no sync file found in {config_locations(source)}; pass --sync-config",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        rules = load_config(rules_path)
        sync_cfg = load_sync_config(sync_path)
        if protocol:
            sync_cfg = replace(sync_cfg, protocol=protocol)
        if branch:
            sync_cfg = replace(sync_cfg, branch=branch)
        if not rules.identical:
            raise SyncError(
                "the rules file defines no [files].identical entries to sync"
            )
        urls = [
            complete_remote(name, sync_cfg.protocol)
            for name in sync_cfg.destinations
        ]
    except (ConfigError, SyncConfigError, SyncError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if not dry_run and not yes:
        typer.echo(f"source: {source}")
        typer.echo(f"rules:  {rules_path}")
        typer.echo(f"sync:   {sync_path}")
        for url in urls:
            typer.echo(f"  -> {url}")
        if delete:
            typer.echo(
                "delete: destination files matched by identical rules but absent "
                "from source will be removed"
            )
        typer.confirm("Sync these repositories?", abort=True)

    try:
        report = sync(
            source,
            rules,
            sync_cfg,
            dry_run=dry_run,
            no_push=no_push,
            delete=delete,
        )
    except (SyncError, ConformanceError, ConfigError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if as_json:
        typer.echo(json.dumps(_sync_as_dict(report), indent=2))
    elif plain:
        typer.echo(str(report))
    else:
        _print_sync_table(report)

    raise typer.Exit(code=0 if report.ok else 1)


def _sync_as_dict(report: SyncReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "source": str(report.source),
        "summary": {
            status.value: count for status, count in report.counts().items()
        },
        "results": [
            {
                "remote": result.remote,
                "url": result.url,
                "status": result.status.value,
                "message": result.message,
                "changed": result.changed,
            }
            for result in report.results
        ],
    }


def _print_sync_table(report: SyncReport) -> None:
    console = Console()
    console.print()
    console.print("[bold]Conformity sync[/bold]")
    console.print(f"  source: [cyan]{report.source}[/cyan]")

    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("Status", no_wrap=True, width=8)
    table.add_column("Remote", overflow="fold")
    table.add_column("Changed", no_wrap=True, width=8, justify="right")
    table.add_column("Detail", overflow="fold")

    for result in report.results:
        style = _STATUS_STYLE[result.status]
        table.add_row(
            Text(result.status.value.upper(), style=style),
            result.remote,
            str(result.changed),
            result.message,
        )

    console.print()
    console.print(table)

    counts = report.counts()
    summary = Text()
    summary.append(f"{counts[Status.PASS]} synced", style="green")
    summary.append("  ")
    summary.append(f"{counts[Status.FAIL]} failed", style="red")
    summary.append("  ")
    summary.append(f"{counts[Status.ERROR]} errors", style="yellow")
    console.print(summary)

    if report.ok:
        console.print("[bold green]Sync complete.[/bold green]")
    else:
        console.print("[bold red]Sync failed for some repositories.[/bold red]")


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
