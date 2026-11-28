// Copyright (C) 2026 Laurent Millet-Lacombe
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of testsuit.
// testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
// General Public License as published by the Free Software Foundation, either version 3 of the License,
// or (at your option) any later version.
// testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
// implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
// Public License for more details.
// You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
// see <https://www.gnu.org/licenses/>.

// Collapsible panels of the editor, for small screens: the file tree, the help and problems panel, and the list
// on the left of a form (roles and classes, inventory items, slots). A collapsed panel is a thin rail that opens
// it again; each one's state is remembered in this browser.
import { useState, type ReactNode } from "react";
import { classes } from "../util";

const STORE = "tsq.editor.panels";

function stored(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(STORE) || "{}") || {};
  } catch {
    return {};
  }
}

/** Whether a panel is collapsed, and its toggle; `narrow`: collapsed by default while the window is narrower. */
export function usePanel(key: string, narrow?: number): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState<boolean>(() => stored()[key] ?? (!!narrow && window.innerWidth < narrow));
  const toggle = () => setCollapsed((c) => {
    try {
      localStorage.setItem(STORE, JSON.stringify({ ...stored(), [key]: !c }));
    } catch { /* not kept: fine */ }
    return !c;
  });
  return [collapsed, toggle];
}

/** The rail a collapsed panel leaves: its name written down the side, a click opens it again. */
export function PanelRail({ label, side, onExpand }: { label: string; side: "left" | "right"; onExpand: () => void }) {
  return (
    <button type="button" className={classes("panel-rail", side)} onClick={onExpand} title={`Show the ${label.toLowerCase()} panel`}>
      <span aria-hidden="true">{side === "left" ? "»" : "«"}</span>
      <span className="panel-rail-label">{label}</span>
    </button>
  );
}

/** The small button in a panel's corner that collapses it. */
export function CollapseButton({ label, side, onCollapse }: { label: string; side: "left" | "right"; onCollapse: () => void }) {
  return (
    <button type="button" className="panel-collapse" onClick={onCollapse} title={`Hide the ${label.toLowerCase()} panel (more room for the form)`}
      aria-label={`Hide the ${label.toLowerCase()} panel`}>{side === "left" ? "«" : "»"}</button>
  );
}

/** A form with its list of items on the left (form-nav), which can be collapsed. */
export function FormSplit({ label, nav, children }: { label: string; nav: ReactNode; children: ReactNode }) {
  const [collapsed, toggle] = usePanel("form-nav", 1200);
  return (
    <div className={classes("form-split", collapsed && "nav-collapsed")}>
      {collapsed ? <PanelRail label={label} side="left" onExpand={toggle} /> : (
        <div className="form-nav">
          <CollapseButton label={label} side="left" onCollapse={toggle} />
          {nav}
        </div>
      )}
      {children}
    </div>
  );
}
