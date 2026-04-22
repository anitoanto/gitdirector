"""Panel data model and YAML persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ...storage import advisory_file_lock, load_yaml_mapping, write_yaml_atomic

_UP = 1
_RIGHT = 2
_DOWN = 4
_LEFT = 8

_BOX_CHARS = {
    _UP | _DOWN: "│",
    _LEFT | _RIGHT: "─",
    _DOWN | _RIGHT: "┌",
    _DOWN | _LEFT: "┐",
    _UP | _RIGHT: "└",
    _UP | _LEFT: "┘",
    _UP | _DOWN | _RIGHT: "├",
    _UP | _DOWN | _LEFT: "┤",
    _LEFT | _RIGHT | _DOWN: "┬",
    _LEFT | _RIGHT | _UP: "┴",
    _UP | _RIGHT | _DOWN | _LEFT: "┼",
}

DEFAULT_PANEL_LAYOUT_KEY = "grid_1x2"


def _panel_layout_icon(layout_key: str) -> str:
    icon_map = {
        "tall_left": "▌",
        "tall_right": "▐",
        "wide_top": "▀",
        "wide_bottom": "▄",
        "duo_top_left_2x3": "▘",
        "duo_top_right_2x3": "▝",
        "duo_bottom_left_2x3": "▖",
        "duo_bottom_right_2x3": "▗",
        "duo_top_left_3x3": "▘",
        "duo_top_right_3x3": "▝",
        "duo_bottom_left_3x3": "▖",
        "duo_bottom_right_3x3": "▗",
        "quad_top_left_3x3": "▛",
        "quad_top_right_3x3": "▜",
        "quad_bottom_left_3x3": "▙",
        "quad_bottom_right_3x3": "▟",
    }
    if layout_key.startswith("grid_"):
        return "▦"
    return icon_map.get(layout_key, "▣")


@dataclass(frozen=True)
class PanePlacement:
    pane_index: int
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1


@dataclass(frozen=True)
class PanelLayout:
    key: str
    menu_label: str
    layout_label: str
    rows: int
    cols: int
    placements: tuple[PanePlacement, ...]
    sort_rank: int
    row_ratios: tuple[int, ...] | None = None
    col_ratios: tuple[int, ...] | None = None

    @property
    def total_panes(self) -> int:
        return len(self.placements)

    @property
    def icon(self) -> str:
        return _panel_layout_icon(self.key)

    @property
    def menu_display_label(self) -> str:
        return f"{self.icon} {self.menu_label}"

    @property
    def display_label(self) -> str:
        return f"{self.icon} {self.layout_label}"


def _make_grid_layout(rows: int, cols: int, *, sort_rank: int) -> PanelLayout:
    menu_descriptions = {
        (1, 1): "Single",
        (1, 2): "Two columns",
        (1, 3): "Three columns",
        (2, 1): "Two rows",
        (2, 2): "Four panes",
        (2, 3): "Six panes",
        (3, 1): "Three rows",
        (3, 2): "Six panes",
        (3, 3): "Nine panes",
    }
    layout_label = f"{rows}×{cols}"
    placements = tuple(
        PanePlacement(
            pane_index=(row * cols) + col + 1,
            row=row,
            col=col,
        )
        for row in range(rows)
        for col in range(cols)
    )
    return PanelLayout(
        key=f"grid_{rows}x{cols}",
        menu_label=f"{layout_label}  {menu_descriptions.get((rows, cols), 'Grid')}",
        layout_label=layout_label,
        rows=rows,
        cols=cols,
        placements=placements,
        sort_rank=sort_rank,
    )


def _make_filled_layout(
    *,
    key: str,
    menu_label: str,
    layout_label: str,
    rows: int,
    cols: int,
    merged_spans: tuple[tuple[int, int, int, int], ...],
    sort_rank: int,
    row_ratios: tuple[int, ...] | None = None,
    col_ratios: tuple[int, ...] | None = None,
) -> PanelLayout:
    occupied: set[tuple[int, int]] = set()
    spans: list[tuple[int, int, int, int]] = []

    for row, col, row_span, col_span in merged_spans:
        if row_span < 1 or col_span < 1:
            raise ValueError("Pane spans must be positive")
        if row < 0 or col < 0 or row + row_span > rows or col + col_span > cols:
            raise ValueError(f"Pane span {(row, col, row_span, col_span)} is out of bounds")
        for occupied_row in range(row, row + row_span):
            for occupied_col in range(col, col + col_span):
                cell = (occupied_row, occupied_col)
                if cell in occupied:
                    raise ValueError(f"Pane spans overlap at {cell}")
                occupied.add(cell)
        spans.append((row, col, row_span, col_span))

    for row in range(rows):
        for col in range(cols):
            if (row, col) not in occupied:
                spans.append((row, col, 1, 1))

    placements = tuple(
        PanePlacement(
            pane_index=index,
            row=row,
            col=col,
            row_span=row_span,
            col_span=col_span,
        )
        for index, (row, col, row_span, col_span) in enumerate(
            sorted(spans, key=lambda span: (span[0], span[1], span[2], span[3])),
            start=1,
        )
    )
    return PanelLayout(
        key=key,
        menu_label=menu_label,
        layout_label=layout_label,
        rows=rows,
        cols=cols,
        placements=placements,
        sort_rank=sort_rank,
        row_ratios=row_ratios,
        col_ratios=col_ratios,
    )


def _make_corner_duo_layout(
    rows: int,
    cols: int,
    corner_key: str,
    *,
    sort_rank: int,
) -> PanelLayout:
    corner_titles = {
        "top_left": ("Top-left duo", (0, 0, 1, 2)),
        "top_right": ("Top-right duo", (0, cols - 2, 1, 2)),
        "bottom_left": ("Bottom-left duo", (rows - 1, 0, 1, 2)),
        "bottom_right": ("Bottom-right duo", (rows - 1, cols - 2, 1, 2)),
    }
    title, merged_span = corner_titles[corner_key]
    size_label = f"{rows}×{cols}"
    return _make_filled_layout(
        key=f"duo_{corner_key}_{rows}x{cols}",
        menu_label=f"{size_label} {title}  Corner focus",
        layout_label=f"{size_label} {title}",
        rows=rows,
        cols=cols,
        merged_spans=(merged_span,),
        sort_rank=sort_rank,
    )


def _make_corner_quad_layout(
    rows: int,
    cols: int,
    corner_key: str,
    *,
    sort_rank: int,
) -> PanelLayout:
    corner_titles = {
        "top_left": ("Top-left quad", (0, 0, 2, 2)),
        "top_right": ("Top-right quad", (0, cols - 2, 2, 2)),
        "bottom_left": ("Bottom-left quad", (rows - 2, 0, 2, 2)),
        "bottom_right": ("Bottom-right quad", (rows - 2, cols - 2, 2, 2)),
    }
    title, merged_span = corner_titles[corner_key]
    size_label = f"{rows}×{cols}"
    return _make_filled_layout(
        key=f"quad_{corner_key}_{rows}x{cols}",
        menu_label=f"{size_label} {title}  Corner block",
        layout_label=f"{size_label} {title}",
        rows=rows,
        cols=cols,
        merged_spans=(merged_span,),
        sort_rank=sort_rank,
    )


_PANEL_LAYOUTS: dict[str, PanelLayout] = {
    layout.key: layout
    for layout in (
        _make_grid_layout(1, 1, sort_rank=1),
        _make_grid_layout(1, 2, sort_rank=2),
        _make_grid_layout(1, 3, sort_rank=3),
        _make_grid_layout(2, 1, sort_rank=4),
        _make_grid_layout(2, 2, sort_rank=5),
        _make_filled_layout(
            key="tall_left",
            menu_label="Tall left  Full left, stack right",
            layout_label="Tall left",
            rows=2,
            cols=2,
            merged_spans=((0, 0, 2, 1),),
            sort_rank=6,
        ),
        _make_filled_layout(
            key="tall_right",
            menu_label="Tall right  Full right, stack left",
            layout_label="Tall right",
            rows=2,
            cols=2,
            merged_spans=((0, 1, 2, 1),),
            sort_rank=7,
        ),
        _make_filled_layout(
            key="wide_top",
            menu_label="Wide top  Full top, stack bottom",
            layout_label="Wide top",
            rows=2,
            cols=2,
            merged_spans=((0, 0, 1, 2),),
            sort_rank=8,
        ),
        _make_filled_layout(
            key="wide_bottom",
            menu_label="Wide bottom  Full bottom, stack top",
            layout_label="Wide bottom",
            rows=2,
            cols=2,
            merged_spans=((1, 0, 1, 2),),
            sort_rank=9,
        ),
        _make_grid_layout(2, 3, sort_rank=10),
        _make_corner_duo_layout(2, 3, "top_left", sort_rank=11),
        _make_corner_duo_layout(2, 3, "top_right", sort_rank=12),
        _make_corner_duo_layout(2, 3, "bottom_left", sort_rank=13),
        _make_corner_duo_layout(2, 3, "bottom_right", sort_rank=14),
        _make_grid_layout(3, 1, sort_rank=15),
        _make_grid_layout(3, 2, sort_rank=16),
        _make_grid_layout(3, 3, sort_rank=17),
        _make_corner_duo_layout(3, 3, "top_left", sort_rank=18),
        _make_corner_duo_layout(3, 3, "top_right", sort_rank=19),
        _make_corner_duo_layout(3, 3, "bottom_left", sort_rank=20),
        _make_corner_duo_layout(3, 3, "bottom_right", sort_rank=21),
        _make_corner_quad_layout(3, 3, "top_left", sort_rank=22),
        _make_corner_quad_layout(3, 3, "top_right", sort_rank=23),
        _make_corner_quad_layout(3, 3, "bottom_left", sort_rank=24),
        _make_corner_quad_layout(3, 3, "bottom_right", sort_rank=25),
    )
}

_CREATE_PANEL_LAYOUT_KEYS = (
    DEFAULT_PANEL_LAYOUT_KEY,
    "grid_1x3",
    "grid_2x1",
    "grid_2x2",
    "tall_left",
    "tall_right",
    "wide_top",
    "wide_bottom",
    "grid_2x3",
    "duo_top_left_2x3",
    "duo_top_right_2x3",
    "duo_bottom_left_2x3",
    "duo_bottom_right_2x3",
    "grid_3x1",
    "grid_3x2",
    "grid_3x3",
    "duo_top_left_3x3",
    "duo_top_right_3x3",
    "duo_bottom_left_3x3",
    "duo_bottom_right_3x3",
    "quad_top_left_3x3",
    "quad_top_right_3x3",
    "quad_bottom_left_3x3",
    "quad_bottom_right_3x3",
)


def get_create_panel_layouts() -> tuple[PanelLayout, ...]:
    return tuple(_PANEL_LAYOUTS[key] for key in _CREATE_PANEL_LAYOUT_KEYS)


def resolve_panel_layout(
    layout_key: str | None = None,
    rows: int | None = None,
    cols: int | None = None,
) -> PanelLayout:
    if layout_key:
        layout = _PANEL_LAYOUTS.get(layout_key)
        if layout is not None:
            return layout
    if rows is None or cols is None:
        raise ValueError("Panel layout requires either a known layout key or rows and cols")

    grid_key = f"grid_{rows}x{cols}"
    layout = _PANEL_LAYOUTS.get(grid_key)
    if layout is not None:
        return layout
    return _make_grid_layout(rows, cols, sort_rank=999)


def _preview_axis_sizes(
    count: int,
    base_size: int,
    ratios: tuple[int, ...] | None,
) -> list[int]:
    if ratios and len(ratios) == count and all(ratio > 0 for ratio in ratios):
        return [base_size * ratio for ratio in ratios]
    return [base_size] * count


def render_panel_layout_preview(
    layout: PanelLayout,
    labels: dict[int, str] | None = None,
    *,
    cell_width: int = 7,
    cell_height: int = 1,
) -> str:
    col_widths = _preview_axis_sizes(layout.cols, cell_width, layout.col_ratios)
    row_heights = _preview_axis_sizes(layout.rows, cell_height, layout.row_ratios)

    width = sum(col_widths) + layout.cols + 1
    height = sum(row_heights) + layout.rows + 1
    connections = [[0 for _ in range(width)] for _ in range(height)]
    content = [[" " for _ in range(width)] for _ in range(height)]
    pane_labels = labels or {
        placement.pane_index: str(placement.pane_index) for placement in layout.placements
    }

    x_boundaries = [0]
    for col_width in col_widths:
        x_boundaries.append(x_boundaries[-1] + col_width + 1)

    y_boundaries = [0]
    for row_height in row_heights:
        y_boundaries.append(y_boundaries[-1] + row_height + 1)

    for placement in layout.placements:
        x0 = x_boundaries[placement.col]
        x1 = x_boundaries[placement.col + placement.col_span]
        y0 = y_boundaries[placement.row]
        y1 = y_boundaries[placement.row + placement.row_span]

        connections[y0][x0] |= _RIGHT | _DOWN
        connections[y0][x1] |= _LEFT | _DOWN
        connections[y1][x0] |= _RIGHT | _UP
        connections[y1][x1] |= _LEFT | _UP

        for x in range(x0 + 1, x1):
            connections[y0][x] |= _LEFT | _RIGHT
            connections[y1][x] |= _LEFT | _RIGHT
        for y in range(y0 + 1, y1):
            connections[y][x0] |= _UP | _DOWN
            connections[y][x1] |= _UP | _DOWN

        label = pane_labels.get(placement.pane_index, "")
        inner_width = x1 - x0 - 1
        inner_height = y1 - y0 - 1
        if label and inner_width > 0 and inner_height > 0:
            visible_label = label[:inner_width]
            label_y = y0 + 1 + ((inner_height - 1) // 2)
            label_x = x0 + 1 + max(0, (inner_width - len(visible_label)) // 2)
            for offset, char in enumerate(visible_label):
                content[label_y][label_x + offset] = char

    lines: list[str] = []
    for y in range(height):
        chars: list[str] = []
        for x in range(width):
            if content[y][x] != " ":
                chars.append(content[y][x])
            else:
                chars.append(_BOX_CHARS.get(connections[y][x], " "))
        lines.append("".join(chars).rstrip())
    return "\n".join(lines)


@dataclass
class Panel:
    name: str
    rows: int
    cols: int
    panes: dict[int, str | None] = field(default_factory=dict)
    layout_key: str | None = None
    closed_panes: set[int] = field(default_factory=set)

    @property
    def layout(self) -> PanelLayout:
        return resolve_panel_layout(self.layout_key, self.rows, self.cols)

    @property
    def total_panes(self) -> int:
        return self.layout.total_panes

    @property
    def filled_panes(self) -> int:
        return sum(1 for pane_index in range(1, self.total_panes + 1) if self.panes.get(pane_index))

    @property
    def is_empty(self) -> bool:
        return self.filled_panes == 0 and not self.closed_panes

    @property
    def all_panes_closed(self) -> bool:
        return self.total_panes > 0 and all(
            pane_index in self.closed_panes and not self.panes.get(pane_index)
            for pane_index in range(1, self.total_panes + 1)
        )

    @property
    def layout_label(self) -> str:
        return self.layout.layout_label

    @property
    def layout_display_label(self) -> str:
        return self.layout.layout_label

    @property
    def pane_placements(self) -> tuple[PanePlacement, ...]:
        return self.layout.placements

    def is_pane_closed(self, pane_index: int) -> bool:
        return pane_index in self.closed_panes


class PanelStore:
    def __init__(self) -> None:
        self.config_dir = Path.home() / ".gitdirector"
        self.panels_file = self.config_dir / "panels.yaml"
        self.lock_file = self.config_dir / "panels.lock"
        self._panels: list[Panel] = []
        self._load()

    @staticmethod
    def _normalize_panes(
        layout: PanelLayout,
        panes: dict[int, str | None] | None = None,
    ) -> dict[int, str | None]:
        normalized_panes = {i: None for i in range(1, layout.total_panes + 1)}
        if panes:
            for pane_index, session_name in panes.items():
                if 1 <= pane_index <= layout.total_panes:
                    normalized_panes[pane_index] = session_name or None
        return normalized_panes

    def _load(self) -> None:
        data = load_yaml_mapping(self.panels_file, description="GitDirector panels config")
        self._panels = []
        for entry in data.get("panels", []):
            layout = resolve_panel_layout(
                entry.get("layout"),
                entry.get("rows"),
                entry.get("cols"),
            )
            panes: dict[int, str | None] = {i: None for i in range(1, layout.total_panes + 1)}
            for k, v in (entry.get("panes") or {}).items():
                pane_index = int(k)
                if 1 <= pane_index <= layout.total_panes:
                    panes[pane_index] = v if v else None
            closed_panes: set[int] = set()
            for raw_pane_index in entry.get("closed_panes") or []:
                pane_index = int(raw_pane_index)
                if 1 <= pane_index <= layout.total_panes and not panes.get(pane_index):
                    closed_panes.add(pane_index)
            self._panels.append(
                Panel(
                    name=entry["name"],
                    rows=layout.rows,
                    cols=layout.cols,
                    panes=panes,
                    layout_key=layout.key,
                    closed_panes=closed_panes,
                )
            )

    def _save(self) -> None:
        self.config_dir.mkdir(exist_ok=True)
        data: dict = {"panels": []}
        for panel in self._panels:
            panes_data: dict[int, str | None] = {}
            for k, v in panel.panes.items():
                panes_data[k] = v
            closed_panes = sorted(
                pane_index
                for pane_index in panel.closed_panes
                if 1 <= pane_index <= panel.total_panes and not panel.panes.get(pane_index)
            )
            data["panels"].append(
                {
                    "name": panel.name,
                    "rows": panel.rows,
                    "cols": panel.cols,
                    "layout": panel.layout.key,
                    "closed_panes": closed_panes,
                    "panes": panes_data,
                }
            )
        with advisory_file_lock(self.lock_file):
            write_yaml_atomic(self.panels_file, data)

    def _kill_panel_sessions(self, panel_names: list[str]) -> None:
        if not panel_names:
            return
        from ...integrations.tmux import kill_panel_tmux_session

        for panel_name in panel_names:
            kill_panel_tmux_session(panel_name)

    @property
    def panels(self) -> list[Panel]:
        return list(self._panels)

    def create(
        self,
        name: str,
        rows: int | None = None,
        cols: int | None = None,
        panes: dict[int, str | None] | None = None,
        layout_key: str | None = None,
    ) -> Panel | None:
        layout = resolve_panel_layout(layout_key, rows, cols)
        normalized_panes = self._normalize_panes(layout, panes)

        if not any(normalized_panes.values()):
            return None

        panel = Panel(
            name=name,
            rows=layout.rows,
            cols=layout.cols,
            panes=normalized_panes,
            layout_key=layout.key,
            closed_panes=set(),
        )
        self._panels.append(panel)
        self._save()
        return panel

    def delete(self, name: str) -> bool:
        for i, p in enumerate(self._panels):
            if p.name == name:
                self._panels.pop(i)
                self._save()
                self._kill_panel_sessions([name])
                return True
        return False

    def get(self, name: str) -> Panel | None:
        for p in self._panels:
            if p.name == name:
                return p
        return None

    def rename(self, old_name: str, new_name: str) -> bool:
        for p in self._panels:
            if p.name == old_name:
                p.name = new_name
                self._save()
                return True
        return False

    def reconfigure(
        self,
        name: str,
        rows: int | None = None,
        cols: int | None = None,
        panes: dict[int, str | None] | None = None,
        layout_key: str | None = None,
    ) -> bool:
        panel = self.get(name)
        if panel is None:
            return False

        layout = resolve_panel_layout(layout_key, rows, cols)
        source_panes = panel.panes if panes is None else panes
        panel.rows = layout.rows
        panel.cols = layout.cols
        panel.layout_key = layout.key
        panel.panes = self._normalize_panes(layout, source_panes)
        panel.closed_panes = set()
        self._save()
        self._kill_panel_sessions([name])
        return True

    def update_pane(
        self,
        panel_name: str,
        pane_index: int,
        session_name: str | None,
        *,
        closed: bool = False,
    ) -> bool:
        panel = self.get(panel_name)
        if not panel or not (1 <= pane_index <= panel.total_panes):
            return False

        panel.panes[pane_index] = session_name or None
        if session_name:
            panel.closed_panes.discard(pane_index)
        elif closed:
            panel.closed_panes.add(pane_index)
        else:
            panel.closed_panes.discard(pane_index)
        self._save()
        return False

    def reload(self) -> None:
        self._load()
