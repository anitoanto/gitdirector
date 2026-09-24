"""The session sidebar shown beside an open session."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import OptionList, Static

from gitdirector.commands.tui import sidebar as S
from gitdirector.commands.tui.constants import TablePalette
from gitdirector.integrations.tmux.deck import DeckPane, DeckState

PALETTE = TablePalette(success="green", yellow="yellow", muted="grey50", primary="magenta")
DECK = "gd/deck/1-a"
CLAUDE = "gd/alpha_aaaaa/claude/1"
CLAUDE_2 = "gd/alpha_aaaaa/claude/2"
SHELL = "gd/beta_bbbbb/shell/1"


def _raw(name: str, repo: str, description: str = "-") -> dict[str, str]:
    _, slug, purpose, _ = name.split("/")
    return {
        "session_name": name,
        "repo": repo,
        "repo_slug": slug,
        "purpose": purpose,
        "description": description,
    }


RAW = [_raw(SHELL, "beta"), _raw(CLAUDE_2, "alpha"), _raw(CLAUDE, "alpha", "fix tests")]


class TestBuildEntries:
    def test_orders_by_repository_purpose_and_number(self):
        entries = S.build_entries(RAW, {CLAUDE: "running"})
        assert [e.session_name for e in entries] == [CLAUDE, CLAUDE_2, SHELL]
        assert entries[0].status == "running"
        assert entries[1].status is None
        assert entries[0].description == "fix tests"
        assert entries[1].description == ""

    def test_drops_sessions_tmux_no_longer_has(self):
        entries = S.build_entries(RAW, {}, live_sessions={CLAUDE, SHELL})
        assert [e.session_name for e in entries] == [CLAUDE, SHELL]

    def test_ignores_everything_but_repository_sessions(self):
        raw = [*RAW, {"session_name": "gd/deck/1-a"}, {"session_name": "gd/panel/dev"}]
        assert len(S.build_entries(raw, {})) == 3

    def test_repositories_sharing_a_label_stay_apart(self):
        raw = [_raw("gd/api_aaaaa/shell/1", "api"), _raw("gd/api_bbbbb/shell/1", "api")]
        options = S.build_options(
            S.build_entries(raw, {}), PALETTE, shown=None, rail=False, width=30
        )
        assert [o.id for o in options if o.disabled] == ["group:api_aaaaa", "group:api_bbbbb"]


class TestNearestSurvivor:
    def test_prefers_the_next_then_the_previous(self):
        order = ["a", "b", "c"]
        assert S.nearest_survivor(order, "b", ["a", "c"]) == "c"
        assert S.nearest_survivor(order, "c", ["a", "b"]) == "b"
        assert S.nearest_survivor(order, "x", ["a"]) == "a"
        assert S.nearest_survivor(order, "a", []) is None


class TestBuildOptions:
    def _options(self, **kwargs):
        entries = S.build_entries(RAW, {CLAUDE: "waiting", SHELL: "idle"})
        return S.build_options(entries, PALETTE, **{"shown": CLAUDE, "width": 30, **kwargs})

    def test_groups_are_disabled_headers(self):
        options = self._options(rail=False)
        assert [o.id for o in options] == [
            "group:alpha_aaaaa",
            CLAUDE,
            CLAUDE_2,
            "group:beta_bbbbb",
            SHELL,
        ]
        assert [o.disabled for o in options] == [True, False, False, True, False]

    def test_session_rows(self):
        options = {o.id: o.prompt.plain for o in self._options(rail=False)}
        # Each row leads with the number that ends its tmux session name.
        assert options[CLAUDE] == "▌ ● 1/claude\n▌   waiting · fix tests"
        assert options[CLAUDE_2] == "  · 2/claude\n    checking"
        assert options[SHELL] == "  ○ 1/shell\n    idle"

    def test_lines_are_cut_to_the_width(self):
        raw = [_raw(CLAUDE, "alpha", "a very long description of what this agent does")]
        entries = S.build_entries(raw, {CLAUDE: "running"})
        (_group, option) = S.build_options(entries, PALETTE, shown=None, rail=False, width=20)
        assert all(len(line) <= 20 for line in option.prompt.plain.split("\n"))
        assert option.prompt.plain.endswith("…")

    def test_rail(self):
        options = self._options(rail=True, width=4)
        assert [o.prompt.plain for o in options] == ["▌ ●", "  ·", " ──", "  ○"]


# -- the app -------------------------------------------------------------------


def _pane(pane_id: str, *, command: str = "bash", placeholder: bool = False) -> DeckPane:
    return DeckPane(pane_id, f"/dev/{pane_id}", command, placeholder, False)


def _state(target: str | None = CLAUDE, live=(CLAUDE, CLAUDE_2, SHELL), **kwargs) -> DeckState:
    values = {
        "target": target,
        "main": _pane("%1"),
        "sidebar": _pane("%2"),
        "main_attached": True,
        "sidebar_focused": True,
        "live_sessions": frozenset(live),
    }
    values.update(kwargs)
    return DeckState(**values)


@pytest.fixture
def deck_api():
    api = MagicMock()
    api.read_deck_state.return_value = _state()
    api.sidebar_collapsed.return_value = False
    api.prefix_key_label.return_value = "C-b"
    api.session_label.side_effect = lambda name: name
    api.ATTACH_ENDED_COMMAND = "sleep"
    with (
        patch.object(S, "deck_api", api),
        patch.object(S, "list_all_gd_sessions", return_value=RAW),
        patch.object(S.TmuxMonitor, "entries", return_value=RAW),
        patch.object(S.TmuxMonitor, "statuses", return_value={CLAUDE: "running"}),
    ):
        yield api


async def _until(pilot, predicate, timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await pilot.pause(0.05)


def _highlighted(app) -> str | None:
    option = app.query_one(OptionList).highlighted_option
    return option.id if option else None


class TestSidebarApp:
    async def test_lists_sessions_with_the_cursor_on_the_shown_one(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app.query_one(OptionList).option_count == 5)
            assert _highlighted(app) == CLAUDE
            deck_api.register_sidebar.assert_called_once_with(DECK, "%2")
            # The keys are on the deck's tmux status line, not in the sidebar.
            assert not app.query("#hints")

    async def test_enter_opens_the_highlighted_session(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed and _highlighted(app) == CLAUDE)
            await pilot.press("j")
            assert _highlighted(app) == CLAUDE_2
            await pilot.press("enter")
            await _until(pilot, lambda: deck_api.show_session.called)
            deck_api.show_session.assert_called_once_with(DECK, CLAUDE_2)

    async def test_enter_on_the_shown_session_just_focuses_it(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed and _highlighted(app) == CLAUDE)
            await pilot.press("enter")
            await _until(pilot, lambda: deck_api.select_pane.called)
            deck_api.select_pane.assert_called_once_with("%1")
            deck_api.show_session.assert_not_called()

    async def test_tab_moves_focus_to_the_session(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed and _highlighted(app) == CLAUDE)
            await pilot.press("tab")
            await _until(pilot, lambda: deck_api.select_pane.called)
            deck_api.select_pane.assert_called_once_with("%1")

    async def test_b_toggles_the_rail(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed and _highlighted(app) == CLAUDE)
            await pilot.press("b")
            await _until(pilot, lambda: deck_api.set_sidebar_collapsed.called)
            deck_api.set_sidebar_collapsed.assert_called_once_with(DECK, True)

    async def test_the_back_arrow_leaves_the_deck(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed)
            assert str(app.query_one("#back", Static).render()) == "«"
            await pilot.click("#back")
            await _until(pilot, lambda: deck_api.close_deck.called)
            deck_api.close_deck.assert_called_once_with(DECK)

    async def test_the_back_button_sits_before_the_title(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed)
            ids = [widget.id for widget in app.query_one("#bar").children]
            assert ids == ["back", "title", "toggle"]

    async def test_an_error_shows_in_the_header_then_clears(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed)
            app._flash("could not open gd/x: boom")
            title = app.query_one("#title", Static)
            assert "could not open" in str(title.render())
            assert title.has_class("-error")
            app._flash_timer.stop()
            app._flash_timer = None
            app._render_title()
            assert "Sessions" in str(title.render())

    async def test_content_hides_until_the_resized_frame_is_drawn(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed)
            hidden_at_resize = []
            deck_api.set_sidebar_collapsed.side_effect = lambda *_: hidden_at_resize.append(
                app.screen.has_class("-resizing")
            )
            await pilot.press("b")
            await _until(pilot, lambda: deck_api.set_sidebar_collapsed.called)
            # Hidden first, so tmux's clipped redraw of the old frame shows nothing.
            assert hidden_at_resize == [True]
            await pilot.resize_terminal(5, 30)
            await _until(pilot, lambda: not app.screen.has_class("-resizing"))
            assert app.screen.has_class("-rail")

    async def test_content_comes_back_even_without_a_resize(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed)
            await pilot.press("b")
            await _until(pilot, lambda: not app.screen.has_class("-resizing"), timeout=2.0)

    async def test_narrow_sidebar_is_a_rail(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(5, 30)) as pilot:
            await _until(pilot, lambda: app.query_one(OptionList).option_count == 4)
            assert app.screen.has_class("-rail")
            assert str(app.query_one("#toggle", Static).render()) == "◧"
            assert not app.query_one("#back").display

    async def test_an_ended_session_is_replaced_by_a_message(self, deck_api):
        deck_api.read_deck_state.return_value = _state(live=(CLAUDE_2, SHELL))
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: deck_api.show_placeholder.called)
            args = deck_api.show_placeholder.call_args.args
            assert args[:4] == (DECK, "%1", "session ended", CLAUDE)
            deck_api.close_deck.assert_not_called()

    async def test_no_sessions_left_closes_the_deck(self, deck_api):
        deck_api.read_deck_state.return_value = _state(live=(DECK,))
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: deck_api.close_deck.called)
            deck_api.close_deck.assert_called_with(DECK)

    async def test_a_closed_main_pane_is_put_back(self, deck_api):
        states = iter([_state(main=None), _state()])
        deck_api.read_deck_state.side_effect = lambda _deck: next(states, _state())
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: deck_api.recreate_main_pane.called)
            deck_api.recreate_main_pane.assert_called_once_with(DECK, "%2")
            await _until(pilot, lambda: deck_api.show_session.called)
            deck_api.show_session.assert_called_with(DECK, CLAUDE, focus=False)

    async def test_a_client_that_left_the_main_pane_gets_a_message(self, deck_api):
        deck_api.read_deck_state.return_value = _state(
            main=_pane("%1", command="sleep"), main_attached=False
        )
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: deck_api.show_placeholder.called)
            assert deck_api.show_placeholder.call_args.args[2] == "session closed here"

    async def test_exits_when_the_deck_is_gone(self, deck_api):
        deck_api.read_deck_state.return_value = None
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: not app.is_running)


class TestFirstFrame:
    async def test_everything_stays_hidden_until_the_first_sample(self, deck_api):
        with patch.object(S.TmuxMonitor, "entries", return_value=None):
            app = S.SessionSidebar(DECK, "%2")
            async with app.run_test(size=(32, 30)) as pilot:
                await pilot.pause(0.2)
                # No monitor sample yet: nothing is visible.
                assert not app._revealed
                assert all(w.has_class("-pending") for w in app.query("#bar, #sessions"))

    async def test_reveals_complete_and_focused(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed)
            assert not app.query(".-pending")
            assert app.query_one(OptionList).option_count == 5
            assert _highlighted(app) == CLAUDE
            assert app.focused is app.query_one(OptionList)
            # Statuses came with the first frame: nothing says "checking".
            assert "checking" not in app.query_one(OptionList).get_option(CLAUDE).prompt.plain


class TestListAndSearch:
    async def test_the_cursor_stops_at_the_ends(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed and _highlighted(app) == CLAUDE)
            for _ in range(5):
                await pilot.press("down")
            assert _highlighted(app) == SHELL
            for _ in range(5):
                await pilot.press("k")
            assert _highlighted(app) == CLAUDE

    async def test_slash_filters_by_repo_agent_or_session(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed)
            await pilot.press("slash")
            assert isinstance(app.focused, S.SearchInput)
            await pilot.press(*"beta")
            ids = [o.id for o in app.query_one(OptionList).options if not o.disabled]
            assert ids == [SHELL]
            assert "1/3" in str(app.query_one("#title", Static).render())
            # Letters that are keys elsewhere (l, b) type into the search.
            await pilot.press("backspace", "backspace", "backspace", "backspace", "c", "l")
            ids = [o.id for o in app.query_one(OptionList).options if not o.disabled]
            assert ids == [CLAUDE, CLAUDE_2]
            deck_api.select_pane.assert_not_called()
            deck_api.set_sidebar_collapsed.assert_not_called()

    async def test_enter_goes_to_the_list_and_escape_clears(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed)
            await pilot.press("slash", *"shell", "enter")
            assert app.focused is app.query_one(OptionList)
            assert _highlighted(app) == SHELL
            # Esc in the list clears the filter before it would leave the sidebar.
            await pilot.press("escape")
            assert app._query == ""
            assert not app.screen.has_class("-searching")
            assert app.query_one(OptionList).option_count == 5
            assert _highlighted(app) == SHELL
            deck_api.select_pane.assert_not_called()

    async def test_no_match_says_so(self, deck_api):
        app = S.SessionSidebar(DECK, "%2")
        async with app.run_test(size=(32, 30)) as pilot:
            await _until(pilot, lambda: app._revealed)
            await pilot.press("slash", *"zzz")
            assert app.screen.has_class("-empty")
            assert str(app.query_one("#empty", Static).render()) == "no match"
            await pilot.press("escape")
            assert not app.screen.has_class("-empty")
