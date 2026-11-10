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

// Graph layout (ELK, layered) for the pipeline views: steps are nodes, `needs` are edges.
import ELK from "elkjs/lib/elk.bundled.js";

const elk = new ELK();

export interface LayoutNode {
  id: string;
  width: number;
  height: number;
  parent?: string;          // a group node (a phase, a sub-pipeline) holding this one
  padding?: string;         // a group's own padding, as ELK writes it ("[top=36,left=16,bottom=16,right=16]")
}

export interface LayoutEdge {
  id: string;
  source: string;
  target: string;
}

export interface Placed {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Positions of the nodes (relative to their parent group), left to right in dependency order. */
export async function layout(nodes: LayoutNode[], edges: LayoutEdge[], direction: "RIGHT" | "DOWN" = "RIGHT",
  extra: Record<string, string> = {}): Promise<Record<string, Placed>> {
  const options = {
    "elk.algorithm": "layered", "elk.direction": direction, "elk.layered.spacing.nodeNodeBetweenLayers": "50",
    "elk.spacing.nodeNode": "22", "elk.padding": "[top=36,left=16,bottom=16,right=16]",
    "elk.hierarchyHandling": "INCLUDE_CHILDREN", "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF", ...extra,
  };
  const byId: Record<string, any> = {};
  const roots: any[] = [];
  for (const n of nodes) byId[n.id] = { id: n.id, width: n.width, height: n.height, children: [], layoutOptions: n.padding ? { ...options, "elk.padding": n.padding } : options };
  for (const n of nodes) {
    if (n.parent && byId[n.parent]) byId[n.parent].children.push(byId[n.id]);
    else roots.push(byId[n.id]);
  }
  for (const n of Object.values(byId)) if (!n.children.length) delete n.children;
  const ids = new Set(nodes.map((n) => n.id));
  const graph = {
    id: "root", layoutOptions: options, children: roots,
    edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };
  const result: any = await elk.layout(graph as any);
  const placed: Record<string, Placed> = {};
  const walk = (node: any) => {
    for (const child of node.children || []) {
      placed[child.id] = { x: child.x || 0, y: child.y || 0, width: child.width, height: child.height };
      walk(child);
    }
  };
  walk(result);
  return placed;
}
