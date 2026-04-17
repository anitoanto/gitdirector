"""Panel data model and YAML persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Panel:
    name: str
    rows: int
    cols: int
    panes: dict[int, str | None] = field(default_factory=dict)

    @property
    def total_panes(self) -> int:
        return self.rows * self.cols

    @property
    def filled_panes(self) -> int:
        return sum(1 for v in self.panes.values() if v is not None)

    @property
    def layout_label(self) -> str:
        return f"{self.rows}×{self.cols}"


class PanelStore:
    def __init__(self) -> None:
        self.config_dir = Path.home() / ".gitdirector"
        self.panels_file = self.config_dir / "panels.yaml"
        self._panels: list[Panel] = []
        self._load()

    def _load(self) -> None:
        if not self.panels_file.exists():
            self._panels = []
            return
        with open(self.panels_file) as f:
            data = yaml.safe_load(f) or {}
        self._panels = []
        for entry in data.get("panels", []):
            panes: dict[int, str | None] = {}
            for k, v in (entry.get("panes") or {}).items():
                panes[int(k)] = v if v else None
            self._panels.append(
                Panel(
                    name=entry["name"],
                    rows=entry["rows"],
                    cols=entry["cols"],
                    panes=panes,
                )
            )

    def _save(self) -> None:
        self.config_dir.mkdir(exist_ok=True)
        data: dict = {"panels": []}
        for panel in self._panels:
            panes_data: dict[int, str | None] = {}
            for k, v in panel.panes.items():
                panes_data[k] = v
            data["panels"].append(
                {
                    "name": panel.name,
                    "rows": panel.rows,
                    "cols": panel.cols,
                    "panes": panes_data,
                }
            )
        with open(self.panels_file, "w") as f:
            yaml.dump(data, f, default_flow_style=False)

    @property
    def panels(self) -> list[Panel]:
        return list(self._panels)

    def create(
        self,
        name: str,
        rows: int,
        cols: int,
        panes: dict[int, str | None] | None = None,
    ) -> Panel:
        total_panes = rows * cols
        normalized_panes = {i: None for i in range(1, total_panes + 1)}
        if panes:
            for pane_index, session_name in panes.items():
                if 1 <= pane_index <= total_panes:
                    normalized_panes[pane_index] = session_name or None

        panel = Panel(name=name, rows=rows, cols=cols, panes=normalized_panes)
        self._panels.append(panel)
        self._save()
        return panel

    def delete(self, name: str) -> bool:
        for i, p in enumerate(self._panels):
            if p.name == name:
                self._panels.pop(i)
                self._save()
                return True
        return False

    def get(self, name: str) -> Panel | None:
        for p in self._panels:
            if p.name == name:
                return p
        return None

    def update_pane(self, panel_name: str, pane_index: int, session_name: str | None) -> None:
        panel = self.get(panel_name)
        if panel:
            panel.panes[pane_index] = session_name
            self._save()

    def cleanup_orphans(self) -> None:
        from ...integrations.tmux import _session_exists

        changed = False
        for panel in self._panels:
            for idx, session in list(panel.panes.items()):
                if session and not _session_exists(session):
                    panel.panes[idx] = None
                    changed = True
        if changed:
            self._save()

    def reload(self) -> None:
        self._load()
