from __future__ import annotations

import asyncio

from textual.widgets import DataTable

from conformity.tui import ConformityApp


def write(root, relative: str, content: str = "same\n") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_tui_runs_checks(tmp_path) -> None:
    target, source = tmp_path / "target", tmp_path / "source"
    write(target, "LICENSE", "GPL\n")
    write(source, "LICENSE", "GPL\n")
    rules = tmp_path / "rules.toml"
    rules.write_text('[files]\nidentical = ["LICENSE"]\n', encoding="utf-8")

    async def scenario() -> None:
        app = ConformityApp(target=target, source=source, config=rules)
        async with app.run_test(size=(100, 30)) as pilot:
            app.action_run()
            await pilot.pause()
            table = app.query_one(DataTable)
            assert table.row_count == 1

    asyncio.run(scenario())


def test_tui_reports_missing_paths(tmp_path) -> None:
    async def scenario() -> None:
        app = ConformityApp(target=tmp_path, source="")
        async with app.run_test(size=(100, 30)) as pilot:
            app.action_run()
            await pilot.pause()
            table = app.query_one(DataTable)
            assert table.row_count == 0

    asyncio.run(scenario())
