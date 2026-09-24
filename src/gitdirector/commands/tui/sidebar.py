"""The session sidebar: every session, beside the one open in a deck.

Runs in the left pane of a deck (see :mod:`gitdirector.integrations.tmux.deck`)
as ``python -m gitdirector.commands.tui.sidebar <deck>``. It lists every
repository session grouped by repository, with the same live statuses as the
console's Sessions tab, and owns the deck: it switches the main pane between
sessions and puts a message there when the shown session ends.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Click, Resize
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...config import Config
from ...integrations.tmux import deck as deck_api
from ...integrations.tmux.core import (
    GD_DEFAULT_DESCRIPTION,
    _parse_gd_session_name,
    list_all_gd_sessions,
)
from ...integrations.tmux.monitor import TmuxMonitor
from .constants import TablePalette, resolve_table_palette

logger = logging.getLogger(__name__)

_TICK_SECS = 0.3
# The first reveal waits this long at most for the monitor's first sample.
_FIRST_SAMPLE_WAIT_SECS = 2.0
_THEME_CHECK_SECS = 2.0
_FLASH_SECS = 4.0
# Lets the hidden frame reach tmux before the pane is resized.
_RESIZE_AFTER_SECS = 0.03
# The content comes back even if no resize arrives.
_RESIZE_TIMEOUT_SECS = 0.6
# Narrower than this, the sidebar is a rail of status dots.
RAIL_BELOW = 14
_GROUP_PREFIX = "group:"
_MARKER = "▌"
_BACK = "«"
# A panel beside the content: the sidebar itself.
_TOGGLE = "◧"


@dataclass(frozen=True)
class SidebarEntry:
    session_name: str
    repo: str
    #: Unique per repository; two repositories can share a label.
    repo_slug: str
    purpose: str
    sequence: int
    description: str
    #: The monitor's verdict, or None before it has sampled the session.
    status: str | None


def build_entries(
    raw_entries: Iterable[Mapping[str, str]],
    statuses: Mapping[str, str],
    live_sessions: Iterable[str] | None = None,
) -> list[SidebarEntry]:
    """Sessions in sidebar order: by repository, then purpose, then number.

    *live_sessions*, when given, drops sessions tmux no longer has, which
    the monitor's last sample can still list for up to a second.
    """
    live = set(live_sessions) if live_sessions is not None else None
    entries = []
    for raw in raw_entries:
        name = raw["session_name"]
        parsed = _parse_gd_session_name(name)
        if parsed is None or (live is not None and name not in live):
            continue
        description = (raw.get("description") or "").strip()
        entries.append(
            SidebarEntry(
                session_name=name,
                repo=raw.get("repo") or parsed[0],
                repo_slug=parsed[0],
                purpose=raw.get("purpose") or parsed[1],
                sequence=int(parsed[2]),
                description="" if description == GD_DEFAULT_DESCRIPTION else description,
                status=statuses.get(name),
            )
        )
    entries.sort(key=lambda e: (e.repo.casefold(), e.repo_slug, e.purpose, e.sequence))
    return entries


def nearest_survivor(previous_order: list[str], gone: str, survivors: list[str]) -> str | None:
    """Where the cursor lands when *gone* disappears: the next session, else the previous."""
    if not survivors:
        return None
    if gone in previous_order:
        index = previous_order.index(gone)
        for name in previous_order[index + 1 :] + previous_order[:index][::-1]:
            if name in survivors:
                return name
    return survivors[0]


def _status_parts(status: str | None, palette: TablePalette) -> tuple[str, str, str]:
    """``(dot, word, style)`` for a session status."""
    if status is None:
        return "·", "checking", palette.muted
    label, style = palette.session_status(status)
    dot, _, word = label.partition(" ")
    return dot, word, style


def _fit(lines: list[Text], width: int) -> Text:
    """Join *lines*, each cut to *width* with an ellipsis: OptionList would wrap them."""
    for line in lines:
        line.truncate(width, overflow="ellipsis")
    return Text("\n").join(lines)


def render_session(entry: SidebarEntry, palette: TablePalette, *, shown: bool, width: int) -> Text:
    dot, word, style = _status_parts(entry.status, palette)
    marker = (_MARKER, palette.primary) if shown else (" ", "")
    title = Text()
    title.append(*marker)
    title.append(f" {dot} ", style=style)
    # The number that ends the tmux session name: gd/<repo>/claude/2 is 2/claude.
    title.append(f"{entry.sequence}/", style=palette.muted)
    title.append(entry.purpose, style="bold" if shown else "")
    detail = Text()
    detail.append(*marker)
    detail.append("   ")
    detail.append(word, style=style)
    if entry.description:
        detail.append(" · ", style=palette.muted)
        detail.append(entry.description, style=f"italic {palette.muted}")
    return _fit([title, detail], width)


def render_rail_session(
    entry: SidebarEntry, palette: TablePalette, *, shown: bool, width: int
) -> Text:
    dot, _word, style = _status_parts(entry.status, palette)
    text = Text()
    text.append(_MARKER if shown else " ", style=palette.primary)
    text.append(f" {dot}", style=style)
    return _fit([text], width)


def render_group(repo: str, palette: TablePalette, *, first: bool, width: int) -> Text:
    label = Text(f" {repo}", style=f"bold {palette.yellow}")
    return _fit([Text(), label] if not first else [label], width)


def render_rail_group(palette: TablePalette, width: int) -> Text:
    return Text(" " + "─" * max(width - 2, 1), style=palette.muted)


def build_options(
    entries: list[SidebarEntry],
    palette: TablePalette,
    *,
    shown: str | None,
    rail: bool,
    width: int,
) -> list[Option]:
    options: list[Option] = []
    repo_slug: str | None = None
    for entry in entries:
        if entry.repo_slug != repo_slug:
            first = repo_slug is None
            repo_slug = entry.repo_slug
            if rail:
                if not first:
                    options.append(
                        Option(
                            render_rail_group(palette, width),
                            id=f"{_GROUP_PREFIX}{repo_slug}",
                            disabled=True,
                        )
                    )
            else:
                options.append(
                    Option(
                        render_group(entry.repo, palette, first=first, width=width),
                        id=f"{_GROUP_PREFIX}{repo_slug}",
                        disabled=True,
                    )
                )
        is_shown = entry.session_name == shown
        prompt = (
            render_rail_session(entry, palette, shown=is_shown, width=width)
            if rail
            else render_session(entry, palette, shown=is_shown, width=width)
        )
        options.append(Option(prompt, id=entry.session_name))
    return options


class CollapseToggle(Static):
    def on_click(self, event: Click) -> None:
        event.stop()
        self.app.action_toggle_collapse()


class BackToConsole(Static):
    def on_click(self, event: Click) -> None:
        event.stop()
        self.app.action_leave_deck()


class SessionSidebar(App):
    CSS = """
    Screen {
        background: $surface;
        overflow: hidden;
    }
    #bar {
        height: 3;
        background: $panel;
    }
    #title {
        width: 1fr;
        height: 100%;
        padding: 0 0 0 1;
        content-align: left middle;
        color: $text;
        text-style: bold;
    }
    #title.-error {
        color: $error;
        text-style: none;
    }
    #toggle {
        width: 3;
        height: 100%;
        content-align: center middle;
        color: $text-muted;
    }
    #toggle:hover {
        background: $primary 30%;
        color: $text;
    }
    #back {
        width: 3;
        height: 100%;
        content-align: center middle;
        background: $panel-lighten-1;
        color: $text-muted;
    }
    #back:hover {
        background: $primary 30%;
        color: $text;
    }
    #sessions {
        height: 1fr;
        max-height: 100%;
        border: none;
        padding: 1 0 0 0;
        background: $surface;
        scrollbar-size-vertical: 1;
    }
    #sessions:focus {
        border: none;
        background-tint: $foreground 0%;
    }
    /* The console's highlight: a tint of the primary colour that keeps the
       status colours readable, instead of a solid block cursor. */
    #sessions > .option-list--option-highlighted,
    #sessions:focus > .option-list--option-highlighted {
        background: $primary 30%;
        color: $text;
        text-style: none;
    }
    #sessions > .option-list--option-hover {
        background: $primary 12%;
    }
    #sessions > .option-list--option-disabled {
        color: $text;
    }
    Screen.-blurred #sessions > .option-list--option-highlighted {
        background: $primary 12%;
    }
    #empty {
        display: none;
        height: 1fr;
        color: $text-muted;
        padding: 1 1;
    }
    Screen.-empty #empty {
        display: block;
    }
    Screen.-empty #sessions {
        display: none;
    }
    Screen.-rail #back,
    Screen.-rail #title {
        display: none;
    }
    Screen.-rail #toggle {
        width: 100%;
    }
    /* Nothing shows until the first sample is in, so the sidebar appears
       once, complete, over a pane tmux already painted in $surface. */
    .-pending {
        visibility: hidden;
    }
    /* While tmux resizes the pane it redraws the old frame clipped or
       padded to the new width until the app draws again: only the
       backgrounds show through that frame. */
    Screen.-resizing #bar > *,
    Screen.-resizing #sessions,
    Screen.-resizing #empty {
        visibility: hidden;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("tab,right,l,escape", "focus_session", show=False, priority=True),
        Binding("b", "toggle_collapse", show=False),
    ]

    def __init__(self, deck: str, pane_id: str | None) -> None:
        super().__init__()
        self.deck = deck
        self.pane_id = pane_id
        self._config = Config()
        if self._config.theme in self.available_themes:
            self.theme = self._config.theme
        self._palette: TablePalette | None = None
        self._monitor = TmuxMonitor()
        self._state: deck_api.DeckState | None = None
        self._entries: list[SidebarEntry] = []
        self._shown: str | None = None
        self._option_ids: list[str] = []
        self._rendered: dict[str, Text] = {}
        self._rail = False
        # From the last Resize: App.size lags behind it inside the handler.
        self._width = 0
        self._collapsed = False
        self._prefix = "C-b"
        self._placed_cursor = False
        self._tick_running = False
        self._flash_timer = None
        self._reconcile_blocked = False
        self._revealed = False

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # The deck's keys are on the tmux status line, so the sidebar is all list.
        with Horizontal(id="bar", classes="-pending"):
            yield BackToConsole(_BACK, id="back")
            yield Static("Sessions", id="title")
            yield CollapseToggle(_TOGGLE, id="toggle")
        yield OptionList(id="sessions", classes="-pending")
        yield Static("no sessions", id="empty", classes="-pending")

    def on_mount(self) -> None:
        self._palette = resolve_table_palette(self.get_css_variables())
        # A deck opens with the session focused.
        self.screen.add_class("-blurred")
        self._monitor.start()
        self._start()
        self.set_interval(_TICK_SECS, self._tick)
        self.set_interval(_THEME_CHECK_SECS, self._follow_theme)
        # Never stay hidden, whatever went wrong on the way.
        self.set_timer(_FIRST_SAMPLE_WAIT_SECS + 1, self._reveal)

    def on_unmount(self) -> None:
        self._monitor.stop(wait=False)

    def on_resize(self, event: Resize) -> None:
        self._width = event.size.width
        rail = self._width < RAIL_BELOW
        if rail != self._rail:
            self._rail = rail
            self.screen.set_class(rail, "-rail")
        self._render_list(force=True)
        if self.screen.has_class("-resizing"):
            self.call_after_refresh(self._end_resize)

    def _follow_theme(self) -> None:
        try:
            if not self._config.reload_if_changed():
                return
        except Exception:
            logger.debug("could not reload the config", exc_info=True)
            return
        if self._config.theme in self.available_themes and self._config.theme != self.theme:
            self.theme = self._config.theme
            self._palette = resolve_table_palette(self.get_css_variables())
            self._render_title()
            self._render_list(force=True)

    # -- state -----------------------------------------------------------------

    @work(thread=True, group="setup")
    def _start(self) -> None:
        """Gather everything the first frame shows, then reveal it at once."""
        self._tick_running = True
        try:
            if self.pane_id:
                deck_api.register_sidebar(self.deck, self.pane_id)
            collapsed = deck_api.sidebar_collapsed()
            prefix = deck_api.prefix_key_label()
            self.call_from_thread(self._apply_setup, collapsed, prefix)
            # Statuses from the monitor's first pass, not "checking" first.
            deadline = time.monotonic() + _FIRST_SAMPLE_WAIT_SECS
            while self._monitor.entries() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self._sample_once()
        except Exception:
            logger.warning("sidebar setup failed", exc_info=True)
        finally:
            self._tick_running = False
            self.call_from_thread(self._reveal)

    def _apply_setup(self, collapsed: bool, prefix: str) -> None:
        self._collapsed = collapsed
        self._prefix = prefix
        self._render_title()

    def _reveal(self) -> None:
        if self._revealed:
            return
        self._revealed = True
        for widget in self.query(".-pending"):
            widget.remove_class("-pending")
        # A hidden widget cannot take focus, so it is given it only now.
        self.query_one(OptionList).focus()

    def _tick(self) -> None:
        if self._tick_running or not self._revealed:
            return
        self._tick_running = True
        self._sample()

    @work(thread=True, group="tick")
    def _sample(self) -> None:
        try:
            self._sample_once()
        except Exception:
            logger.warning("sidebar sample failed", exc_info=True)
        finally:
            self._tick_running = False

    def _sample_once(self) -> None:
        """Read and repair the deck, then hand the result to the UI; on a worker thread."""
        state = deck_api.read_deck_state(self.deck)
        if state is None:
            self.call_from_thread(self.exit)
            return
        if not self._reconcile_blocked:
            state = self._reconcile(state) or state
        raw = self._monitor.entries()
        if raw is None:
            raw = list_all_gd_sessions()
        entries = build_entries(raw, self._monitor.statuses(), state.live_sessions)
        self.call_from_thread(self._apply_sample, state, entries)

    def _reconcile(self, state: deck_api.DeckState) -> deck_api.DeckState | None:
        """Repair the deck after anything that happened behind the sidebar's back.

        Runs on the sampling thread; returns a fresh state when it changed
        the deck.
        """
        if not any(_parse_gd_session_name(name) for name in state.live_sessions):
            # Nothing left to show: back to the console.
            deck_api.close_deck(self.deck)
            return None
        main = state.main
        if main is None:
            if state.sidebar is None:
                return None
            deck_api.recreate_main_pane(self.deck, state.sidebar.pane_id)
            refreshed = deck_api.read_deck_state(self.deck)
            if refreshed is None or refreshed.main is None:
                return refreshed
            if state.target in state.live_sessions:
                deck_api.show_session(self.deck, state.target, focus=False)
            else:
                self._show_message(refreshed.main.pane_id, "no session open", "")
            return deck_api.read_deck_state(self.deck)
        if state.target and state.target not in state.live_sessions:
            self._show_message(main.pane_id, "session ended", deck_api.session_label(state.target))
            self.call_from_thread(self._after_session_ended, state.target)
            return deck_api.read_deck_state(self.deck)
        if (
            not main.placeholder
            and not state.main_attached
            and main.command == deck_api.ATTACH_ENDED_COMMAND
        ):
            label = deck_api.session_label(state.target) if state.target else ""
            self._show_message(main.pane_id, "session closed here", label)
            return deck_api.read_deck_state(self.deck)
        return None

    def _show_message(self, main_pane: str, title: str, detail: str) -> None:
        hint = f"choose a session on the left  ·  {self._prefix} Tab"
        deck_api.show_placeholder(self.deck, main_pane, title, detail, hint)

    def _after_session_ended(self, gone: str) -> None:
        previous = [entry.session_name for entry in self._entries]
        survivors = [name for name in previous if name != gone]
        landing = nearest_survivor(previous, gone, survivors)
        if landing is not None:
            self._move_cursor(landing)

    def _apply_sample(self, state: deck_api.DeckState, entries: list[SidebarEntry]) -> None:
        self._state = state
        self.screen.set_class(not state.sidebar_focused, "-blurred")
        shown = state.target if state.main is not None and not state.main.placeholder else None
        changed = shown != self._shown or entries != self._entries
        self._shown = shown
        self._entries = entries
        self.screen.set_class(not entries, "-empty")
        if changed:
            self._render_list()
        if not self._placed_cursor and entries:
            self._placed_cursor = True
            self._move_cursor(shown or entries[0].session_name)

    # -- rendering -------------------------------------------------------------

    def _render_title(self) -> None:
        if self._palette is None or self._flash_timer is not None:
            return
        title = self.query_one("#title", Static)
        title.remove_class("-error")
        title.update(
            Text.assemble(("Sessions", "bold"), (f"  {len(self._entries)}", self._palette.muted))
        )

    def _render_list(self, *, force: bool = False) -> None:
        if self._palette is None:
            return
        option_list = self.query_one(OptionList)
        self._render_title()
        options = build_options(
            self._entries,
            self._palette,
            shown=self._shown,
            rail=self._rail,
            # The scrollbar keeps a column.
            width=max(self._width - 1, 1),
        )
        ids = [option.id or "" for option in options]
        highlighted = option_list.highlighted_option
        highlighted_id = highlighted.id if highlighted is not None else None
        if ids != self._option_ids or force:
            option_list.clear_options()
            option_list.add_options(options)
            self._option_ids = ids
            self._rendered = {option.id or "": option.prompt for option in options}
            if highlighted_id in ids:
                option_list.highlighted = ids.index(highlighted_id)
            return
        for option in options:
            option_id = option.id or ""
            if self._rendered.get(option_id) != option.prompt:
                option_list.replace_option_prompt(option_id, option.prompt)
                self._rendered[option_id] = option.prompt

    def _move_cursor(self, session_name: str) -> None:
        if session_name in self._option_ids:
            option_list = self.query_one(OptionList)
            option_list.highlighted = self._option_ids.index(session_name)
            option_list.scroll_to_highlight()

    def _flash(self, message: str) -> None:
        title = self.query_one("#title", Static)
        title.add_class("-error")
        # Less the two buttons and the title's padding.
        title.update(_fit([Text(message)], max(self._width - 7, 1)))
        if self._flash_timer is not None:
            self._flash_timer.stop()

        def clear() -> None:
            self._flash_timer = None
            self._render_title()

        self._flash_timer = self.set_timer(_FLASH_SECS, clear)

    # -- actions ---------------------------------------------------------------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id and not event.option.disabled:
            self._open(event.option.id)

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_focus_session(self) -> None:
        state = self._state
        if state is not None and state.main is not None:
            self._select_pane(state.main.pane_id)

    def action_toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self._render_title()
        self.screen.add_class("-resizing")
        collapsed = self._collapsed
        self.set_timer(_RESIZE_AFTER_SECS, lambda: self._set_collapsed(collapsed))
        self.set_timer(_RESIZE_TIMEOUT_SECS, self._end_resize)

    def _end_resize(self) -> None:
        self.screen.remove_class("-resizing")

    @work(thread=True, group="deck")
    def action_leave_deck(self) -> None:
        try:
            deck_api.close_deck(self.deck)
        except Exception as exc:
            logger.warning("could not leave the deck", exc_info=True)
            self.call_from_thread(self._flash, f"could not leave: {exc}")

    def _open(self, session_name: str) -> None:
        """Show *session_name* and move focus to it."""
        state = self._state
        if state is not None and state.main is not None:
            if session_name == self._shown and state.main_attached:
                self._select_pane(state.main.pane_id)
                return
        self._shown = session_name
        self._render_list()
        self._show(session_name)

    @work(thread=True, group="deck")
    def _show(self, session_name: str) -> None:
        self._reconcile_blocked = True
        try:
            deck_api.show_session(self.deck, session_name)
        except Exception as exc:
            logger.warning("could not show %s", session_name, exc_info=True)
            self.call_from_thread(self._flash, f"could not open {session_name}: {exc}")
        finally:
            self._reconcile_blocked = False

    @work(thread=True, group="deck")
    def _select_pane(self, pane_id: str) -> None:
        deck_api.select_pane(pane_id)

    @work(thread=True, group="deck")
    def _set_collapsed(self, collapsed: bool) -> None:
        try:
            deck_api.set_sidebar_collapsed(self.deck, collapsed)
        except Exception:
            logger.warning("could not resize the sidebar", exc_info=True)


def _log_to_file() -> None:
    log_path = Path.home() / ".gitdirector" / "sidebar.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path)
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("gitdirector")
    root.addHandler(handler)
    root.setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m gitdirector.commands.tui.sidebar <deck>", file=sys.stderr)
        return 2
    _log_to_file()
    app = SessionSidebar(args[0], os.environ.get("TMUX_PANE"))
    app.run()
    return app.return_code or 0


if __name__ == "__main__":
    sys.exit(main())
