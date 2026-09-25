"""The launcher: start an agent, a shell or the editor in a repository, or rejoin a session."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ....agents import AGENTS, AGENTS_BY_KEY, AgentSpec
from ..constants import _MODAL_BINDINGS
from .card import (
    ShortcutKeys,
    card_css,
    card_palette,
    heading,
    key_hints,
    menu_row,
    spacer,
)

# One key per launcher line; j/k/h/l stay free for moving and picking modes.
_AGENT_KEYS = {"claude": "c", "opencode": "o", "codex": "x", "copilot": "p", "pi": "i"}
_SHELL_KEY = "s"
_EDITOR_KEY = "v"
_SESSION_KEYS = "123456789"

_HINT = key_hints(("↑↓", "select"), ("⏎", "open"), ("key", "jump"), ("esc", "close"))
_MODE_HINT = key_hints(("↑↓", "select"), ("⇥", "mode"), ("⏎", "launch"), ("esc", "close"))


def session_action_menu_css(screen_name: str) -> str:
    return card_css(screen_name, width=70)


def _mode_picker(agent: AgentSpec, selected: str | None, success: str, danger: str) -> Text:
    picker = Text()
    for index, mode in enumerate(agent.modes):
        if index:
            picker.append("  ")
        if mode.key == selected:
            # The console's "live" green; red when it drops the permission prompts.
            color = danger if mode.dangerous else success
            picker.append(f"● {mode.label}", style=f"bold {color}".strip())
        else:
            picker.append(f"○ {mode.label}", style="dim")
    return picker


class SessionActionMenuScreen(ShortcutKeys, ModalScreen[str]):
    """Launch an agent, a shell or VS Code, or rejoin one of the running sessions."""

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
        self._shortcuts: dict[str, str] = {}

    def _subtitle(self) -> str:
        return ""

    def _meta(self) -> Text:
        return Text()

    def _palette(self):
        return card_palette(self.app)

    def _agent_prompt(self, agent: AgentSpec):
        label = Text.assemble(("◆ ", "dim"), (agent.label, "bold"))
        detail = Text()
        if agent.modes:
            palette = self._palette()
            selected = self._modes.get(agent.key)
            detail = _mode_picker(agent, selected, palette.success, palette.danger)
        return menu_row(label, detail, _AGENT_KEYS.get(agent.key, ""))

    def _agent_options(self) -> list[Option]:
        items = [heading("Start an agent")]
        items.extend(Option(self._agent_prompt(agent), id=f"agent:{agent.key}") for agent in AGENTS)
        return items

    def _primary_options(self) -> list[Option]:
        return [
            spacer(),
            heading("Open"),
            Option(
                menu_row(
                    Text.assemble(("› ", "dim"), ("Shell", "bold")),
                    Text("new tmux session", style="dim"),
                    _SHELL_KEY,
                ),
                id="new_session",
            ),
            Option(
                menu_row(
                    Text.assemble(("› ", "dim"), ("VS Code", "bold")),
                    Text("editor", style="dim"),
                    _EDITOR_KEY,
                ),
                id="vscode",
            ),
        ]

    def _session_statuses(self) -> dict[str, str]:
        entries = getattr(self.app, "_sessions_entries", None) or []
        return {entry["session_name"]: entry.get("status", "") for entry in entries}

    def _session_options(self, sessions: list[str]) -> list[Option]:
        if not sessions:
            return []
        from ....integrations.tmux.core import _parse_gd_session_name

        palette = self._palette()
        styles = {
            "waiting": ("●", f"bold {palette.yellow}"),
            "running": ("●", palette.success),
            "idle": ("○", palette.muted),
        }
        statuses = self._session_statuses()
        items = [spacer(), heading(f"Running · {len(sessions)}")]
        for index, session_name in enumerate(sessions):
            parsed = _parse_gd_session_name(session_name)
            label = f"{parsed[1]}/{parsed[2]}" if parsed else session_name
            status = statuses.get(session_name, "")
            dot, style = styles.get(status, ("●", "dim"))
            key = _SESSION_KEYS[index] if index < len(_SESSION_KEYS) else ""
            items.append(
                Option(
                    menu_row(
                        Text.assemble((f"{dot} ", style), (label, "bold")),
                        Text(status, style=style),
                        key,
                    ),
                    id=f"attach:{session_name}",
                )
            )
        return items

    def _remove_options(self, sessions: list[str]) -> list[Option]:
        if not sessions:
            return []
        return [
            Option(
                menu_row(Text.assemble(("✕ ", "dim"), ("Remove a session…", "dim"))),
                id="remove_session",
            )
        ]

    def compose(self) -> ComposeResult:
        from ....integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.path)
        items = self._agent_options()
        items.extend(self._primary_options())
        items.extend(self._session_options(sessions))
        items.extend(self._remove_options(sessions))

        self._shortcuts = {key: f"agent:{agent}" for agent, key in _AGENT_KEYS.items()}
        self._shortcuts[_SHELL_KEY] = "new_session"
        self._shortcuts[_EDITOR_KEY] = "vscode"
        for key, session_name in zip(_SESSION_KEYS, sessions):
            self._shortcuts[key] = f"attach:{session_name}"

        with Vertical(id="menu-container"):
            with Horizontal(id="menu-header"):
                yield Static(escape(self.title), id="menu-title")
                yield Static(self._meta(), id="menu-meta")
            yield Static(self._subtitle(), id="menu-branch")
            yield OptionList(*items, id="action-menu")
            yield Static(_MODE_HINT, id="menu-hint")

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

    def _choose(self, action: str) -> None:
        agent = AGENTS_BY_KEY.get(action[len("agent:") :]) if action.startswith("agent:") else None
        if agent is not None and agent.key in self._modes:
            action = f"{action}:{self._modes[agent.key]}"
        self.dismiss(action)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._choose(event.option.id)

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
