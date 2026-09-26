import click
from rich.text import Text

from . import MUTED, SUCCESS, CommandError, console, print_rows
from .sessions import tmux_errors


def _panel_store():
    from .tui.panels import PanelStore

    return PanelStore()


def _complete_panel_names(_ctx, _param, incomplete: str):
    from click.shell_completion import CompletionItem

    try:
        panels = _panel_store().panels
    except Exception:
        return []
    return [CompletionItem(p.name) for p in panels if p.name.lower().startswith(incomplete.lower())]


def _list_panels(store) -> None:
    from ..integrations.tmux.core import _list_sessions

    if not store.panels:
        console.print("No panels yet. Create one in the console: gitdirector console, tab 3")
        return
    with tmux_errors("Couldn't list panels"):
        live = set(_list_sessions())
    rows = []
    for panel in sorted(store.panels, key=lambda p: p.name.lower()):
        sessions = [name for name in panel.panes.values() if name]
        running = sum(name in live for name in sessions)
        rows.append(
            (
                Text(panel.name, style="bold"),
                Text(panel.layout_label, style=MUTED),
                Text(f"{running}/{panel.total_panes} live", style=SUCCESS if running else MUTED),
            )
        )
    print_rows(("PANEL", "LAYOUT", "SESSIONS"), rows)


def register(cli: click.Group):
    @cli.command()
    @click.argument("name", required=False, shell_complete=_complete_panel_names)
    def panel(name: str | None):
        """Open a saved panel, or list them

        A panel shows several sessions side by side in one tmux window;
        create and edit them in the console's Panels tab. prefix 1-9 jumps
        to a pane, prefix d detaches.
        """
        store = _panel_store()
        if name is None:
            _list_panels(store)
            return
        saved = store.get(name)
        if saved is None:
            raise CommandError(f"No panel named '{name}'\nRun 'gitdirector panel' to list panels.")

        from ..integrations.tmux import attach_tmux_session, rebuild_panel_tmux_session
        from ..integrations.tmux.core import (
            _protect_session,
            _session_exists,
            make_panel_session_name,
        )
        from ..launch_context import leave_launch_directory

        leave_launch_directory(["panel", name])
        session_name = make_panel_session_name(saved.name)
        with tmux_errors(f"Couldn't open panel '{saved.name}'"):
            if _session_exists(session_name):
                _protect_session(session_name)
            else:
                session_name = rebuild_panel_tmux_session(
                    saved.name,
                    saved.rows,
                    saved.cols,
                    saved.panes,
                    closed_panes=saved.closed_panes,
                    layout_key=saved.layout.key,
                )
            attach_tmux_session(session_name)
