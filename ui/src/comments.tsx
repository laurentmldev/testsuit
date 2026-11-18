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
// like sticky notes, in Markdown. Never run: the Editor draws them where they were put, the run graph under
// their phase.
import type { NodeProps } from "@xyflow/react";
import { classes, Markdown } from "./util";

export const COMMENT_WIDTH = 220;

export interface CommentCard {
  text: string;
  x?: number;
  y?: number;
  width?: number;
}

/** A card as the editor reads it: one given as its text only (`- a note`) is `{text}`. */
export function commentCard(value: any): CommentCard {
  if (typeof value === "string") return { text: value };
  return value && typeof value === "object" ? { ...value, text: typeof value.text === "string" ? value.text : "" } : { text: "" };
}

/** A comment card on a graph: its Markdown, or a hint when it is empty (the Editor only). */
export function CommentNode({ data, selected }: NodeProps) {
  const d = data as { text: string; editable?: boolean };
  const empty = !d.text.trim();
  return (
    <div className={classes("comment-node", selected && "selected", d.editable && "editable", empty && "empty")}
      title={d.editable ? "Comment: drag it to move it, click it to edit it" : "Comment (never run)"}>
      {empty ? <span className="muted small">empty comment: write it in the panel on the right</span> : <Markdown text={d.text} />}
    </div>
  );
}
