"""Shared modal screen helpers for session-oriented action menus."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ....agents import AGENTS, AGENTS_BY_KEY, AgentSpec
from ..constants import _MODAL_BINDINGS, _MODAL_CSS
from ..terminal_caps import strip_unsupported_css as _safe_css

_HINT = "↑↓ select    \\[enter] open    \\[esc] close"
_MODE_HINT = "↑↓ select    ⇥ mode    \\[enter] open    \\[esc] close"


def session_action_menu_css(screen_name: str) -> str:
    return _safe_css(
        f"{screen_name} {{"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS + f"{screen_name} #menu-container {{ min-width: 60; max-width: 88; }}"
    )


def _row(left: str, right: Text | str = "") -> Table:
    """A menu row with *right* pinned to the right edge."""
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(justify="right", no_wrap=True, overflow="ellipsis")
    grid.add_row(Text.from_markup(left), right if isinstance(right, Text) else Text(right))
    return grid


def _heading(label: str) -> Option:
    return Option(f"[dim]{label}[/dim]", disabled=True)


def _mode_picker(agent: AgentSpec, selected: str | None, colors: dict[str, str]) -> Text:
    picker = Text()
    for index, mode in enumerate(agent.modes):
        if index:
            picker.append("  ")
        if mode.key == selected:
            color = colors.get("text-error" if mode.dangerous else "text-primary", "")
            picker.append(f"● {mode.label}", style=f"bold {color}".strip())
        else:
            picker.append(f"○ {mode.label}", style="dim")
    return picker


class SessionActionMenuScreen(ModalScreen[str]):
    """Base menu for creating, attaching, and removing tmux sessions."""

    BINDINGS = [
        *_MODAL_BINDINGS,
        Binding("left,h", "cycle_mode(-1)", "←", show=False),
        Binding("right,l", "cycle_mode(1)", "→", show=False),
        Binding("tab", "cycle_mode(1)", "⇥", show=False, priority=True),
        Binding("shift+tab", "cycle_mode(-1)", "⇤", show=False, priority=True),
    ]

    def __init__(self, title: str, path: Path) -> None:
        super().__init__()
        self.title = title
        self.path = path
        self._modes: dict[str, str] = {
            agent.key: agent.default_mode for agent in AGENTS if agent.default_mode
        }

    def _subtitle(self) -> str:
        return ""

    def _primary_options(self) -> list[Option]:
        return [
            Option(_row("+ [bold]Shell[/bold]", Text("tmux", style="dim")), id="new_session"),
            Option(_row("› [bold]VS Code[/bold]", Text("editor", style="dim")), id="vscode"),
        ]

    def _session_options(self, sessions: list[str]) -> list[Option]:
        if not sessions:
            return []
        from ....integrations.tmux.core import _parse_gd_session_name

        items = [Option("", disabled=True), _heading(f"{len(sessions)} active")]
        for session_name in sessions:
            parsed = _parse_gd_session_name(session_name)
            session_label = f"{parsed[1]}/{parsed[2]}" if parsed else session_name
            items.append(
                Option(
                    _row(
                        f"● [bold]{escape(session_label)}[/bold]",
                        Text(session_name, style="dim"),
                    ),
                    id=f"attach:{session_name}",
                )
            )
        return items

    def _agent_prompt(self, agent: AgentSpec) -> Table:
        label = f"◆ [bold]{escape(agent.label)}[/bold]"
        if not agent.modes:
            return _row(label)
        colors = self.app.get_css_variables()
        return _row(label, _mode_picker(agent, self._modes.get(agent.key), colors))

    def _agent_options(self) -> list[Option]:
        items = [Option("", disabled=True), _heading("Agents")]
        items.extend(Option(self._agent_prompt(agent), id=f"agent:{agent.key}") for agent in AGENTS)
        return items

    def _remove_options(self, sessions: list[str]) -> list[Option]:
        if not sessions:
            return []
        return [
            Option("", disabled=True),
            Option("[$text]✕[/] [dim]Remove session…[/dim]", id="remove_session"),
        ]

    def compose(self) -> ComposeResult:
        from ....integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.path)
        items = self._primary_options()
        items.extend(self._agent_options())
        items.extend(self._session_options(sessions))
        items.extend(self._remove_options(sessions))

        with Vertical(id="menu-container"):
            yield Static(f"[bold $text]{escape(self.title)}[/]", id="menu-title")
            yield Static(self._subtitle(), id="menu-branch")
            yield OptionList(*items, id="action-menu")
            yield Static(_HINT, id="menu-hint")

    def on_mount(self) -> None:
        self.query_one("#action-menu", OptionList).focus()

    def _highlighted_agent(self) -> AgentSpec | None:
        menu = self.query_one("#action-menu", OptionList)
        option = menu.highlighted_option
        if option is None or not (option.id or "").startswith("agent:"):
            return None
        return AGENTS_BY_KEY.get(option.id[len("agent:") :])

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        agent = self._highlighted_agent()
        hint = _MODE_HINT if agent is not None and agent.modes else _HINT
        self.query_one("#menu-hint", Static).update(hint)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        agent = AGENTS_BY_KEY.get(action[len("agent:") :]) if action.startswith("agent:") else None
        if agent is not None and agent.key in self._modes:
            action = f"{action}:{self._modes[agent.key]}"
        self.dismiss(action)

    def action_cycle_mode(self, step: int) -> None:
        agent = self._highlighted_agent()
        if agent is None or not agent.modes:
            return
        keys = [mode.key for mode in agent.modes]
        current = self._modes.get(agent.key, keys[0])
        self._modes[agent.key] = keys[(keys.index(current) + step) % len(keys)]
        self.query_one("#action-menu", OptionList).replace_option_prompt(
            f"agent:{agent.key}", self._agent_prompt(agent)
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_up()
