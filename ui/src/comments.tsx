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

// Comment cards of a pipeline (sequencer README, section 8.3): notes on the graph of a phase or a sub-pipeline,
// like sticky notes, in Markdown, free or pinned to a step, in a color. Never run: the Editor draws them where they
// were put (a pinned one follows its step), the run graph under their phase or at the bottom of their sub-pipeline.
import type React from "react";
import { BaseEdge, Handle, Position, useInternalNode, type EdgeProps, type InternalNode, type NodeProps } from "@xyflow/react";
import { classes, Markdown } from "./util";

export const COMMENT_WIDTH = 220;

/** The named colors of a card (the model's COMMENT_COLORS), each a class with its light and dark shades; a card
 *  can also be given any '#rrggbb'. None: yellow. */
export const COMMENT_COLORS = ["yellow", "orange", "red", "pink", "purple", "blue", "green", "grey"] as const;

/** The class and style of a card of that color: a named one by its class, a '#rrggbb' as its background, with a
 *  darker border and a text that reads on it. */
export function commentColor(color?: string): { className?: string; style?: React.CSSProperties } {
  if (!color || color === "yellow") return {};
  if ((COMMENT_COLORS as readonly string[]).includes(color)) return { className: `note-${color}` };
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color)?.[1];
  if (!hex) return {};
  const full = hex.length === 3 ? hex.split("").map((c) => c + c).join("") : hex;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  const dark = 0.299 * r + 0.587 * g + 0.114 * b < 140;
  return { style: { background: color, borderColor: `color-mix(in srgb, ${color} 70%, ${dark ? "white" : "black"})`, color: dark ? "#f5f5f5" : "#2a2a2a" } };
}

export interface CommentCard {
  text: string;
  step?: string;            // the step it is pinned to (x, y then from that step); none: a free card
  x?: number;
  y?: number;
  width?: number;
  color?: string;           // a COMMENT_COLORS name or '#rrggbb'; none: yellow
}

/** A card as the editor reads it: one given as its text only (`- a note`) is `{text}`. */
export function commentCard(value: any): CommentCard {
  if (typeof value === "string") return { text: value };
  return value && typeof value === "object" ? { ...value, text: typeof value.text === "string" ? value.text : "" } : { text: "" };
}

/** A comment card on a graph: its Markdown, or a hint when it is empty (the Editor only). */
export function CommentNode({ data, selected }: NodeProps) {
  const d = data as { text: string; editable?: boolean; pin?: string; lost?: boolean; color?: string };
  const empty = !d.text.trim();
  const tint = commentColor(d.color);
  return (
    <div className={classes("comment-node", tint.className, selected && "selected", d.editable && "editable", empty && "empty", d.pin && "pinned")} style={tint.style}
      title={d.editable ? `Comment${d.pin ? ` pinned to ${d.pin}${d.lost ? " (no such step: shown free)" : ""}` : ""}: drag it to move it, click it to edit it` : "Comment (never run)"}>
      {/* where the line to its step ends (drawn by PinEdge, from border to border): never connectable */}
      <Handle type="target" position={Position.Top} isConnectable={false} className="comment-handle" />
      {d.pin && <div className={classes("comment-pin", d.lost && "lost")}>📌 {d.pin}</div>}
      {empty ? <span className="muted small">empty comment: write it in the panel on the right</span> : <Markdown text={d.text} />}
    </div>
  );
}

/** The point where the line from the center of a node to ``to`` leaves the node's box. */
function border(node: InternalNode, to: { x: number; y: number }): { x: number; y: number; cx: number; cy: number } {
  const { x, y } = node.internals.positionAbsolute;
  const w = node.measured.width || 0, h = node.measured.height || 0;
  const cx = x + w / 2, cy = y + h / 2;
  const dx = to.x - cx, dy = to.y - cy;
  const t = Math.min(dx ? w / 2 / Math.abs(dx) : Infinity, dy ? h / 2 / Math.abs(dy) : Infinity, 1);
  return { x: cx + dx * t, y: cy + dy * t, cx, cy };
}

/** The line from a pinned comment card to its step (the edge's source is the step, its target the card): straight,
 *  dashed, from border to border, whatever the sides they face. */
export function PinEdge({ id, source, target, style }: EdgeProps) {
  const step = useInternalNode(source);
  const card = useInternalNode(target);
  if (!step?.measured.width || !card?.measured.width) return null;
  const centerOf = (n: InternalNode) => ({ x: n.internals.positionAbsolute.x + (n.measured.width || 0) / 2, y: n.internals.positionAbsolute.y + (n.measured.height || 0) / 2 });
  const a = border(step, centerOf(card));
  const b = border(card, centerOf(step));
  return <BaseEdge id={id} path={`M ${a.x},${a.y} L ${b.x},${b.y}`} className="pin-edge" style={style} />;
}

/** The line of a card pinned to the step of that node id, for the graph's edges. */
export function pinEdge(card: string, step: string): { id: string; source: string; target: string; type: "pin"; selectable: false; deletable: false; focusable: false; zIndex: number } {
  return { id: `pin:${card}`, source: step, target: card, type: "pin", selectable: false, deletable: false, focusable: false, zIndex: 999 };
}
