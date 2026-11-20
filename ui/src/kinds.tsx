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

// What a name is: the same word is often a lab role, a component model, a slot and a slot type ("pilot").
// A small glyph before the name tells them apart everywhere, the hover spells the kind out.
// The lab side (what exists) is drawn solid, the datapack side (what a test needs) dashed: a slot is the
// dashed outline of the component bound to it, a stage slot that of a stage. A type is its shape stacked.
import type { ReactNode } from "react";

export type Kind = "component" | "model" | "role" | "stage" | "stage_type" | "slot" | "stage_slot" | "slot_type" | "phase" | "command";

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.3, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
const D = { ...S, strokeDasharray: "2 1.4" };

export const KINDS: Record<Kind, { label: string; family: "lab" | "test" | "pipeline"; draw: ReactNode }> = {
  component: { label: "lab component", family: "lab", draw: <rect {...S} x="2.5" y="2.5" width="7" height="7" rx="1.5" /> },
  model: { label: "component model", family: "lab", draw: <path {...S} d="M6 1.5l4 2.25v4.5L6 10.5l-4-2.25v-4.5z" /> },
  role: { label: "lab role", family: "lab", draw: <g {...S}><circle cx="6" cy="6" r="4" /><circle cx="6" cy="6" r="1" fill="currentColor" /></g> },
  stage: { label: "lab stage", family: "lab", draw: <g {...S}><rect x="1" y="2.5" width="10" height="7" rx="1.5" /><path d="M3.6 5h1.6v2H3.6zM6.8 5h1.6v2H6.8z" /></g> },
  stage_type: { label: "stage type", family: "lab", draw: <g {...S}><path d="M3 1.2h8v5.3" /><rect x="0.8" y="3.3" width="8.6" height="7" rx="1.5" /><path d="M3 6h1.4v1.6H3zM5.8 6h1.4v1.6H5.8z" /></g> },
  slot: { label: "slot", family: "test", draw: <rect {...D} x="2.5" y="2.5" width="7" height="7" rx="1.5" /> },
  stage_slot: { label: "stage slot", family: "test", draw: <g><rect {...D} x="1" y="2.5" width="10" height="7" rx="1.5" /><path {...S} d="M3.6 5h1.6v2H3.6zM6.8 5h1.6v2H6.8z" /></g> },
  slot_type: { label: "slot type", family: "test", draw: <g><path {...S} d="M4 1.5h6.5v6.5" /><rect {...D} x="1.5" y="3.5" width="7" height="7" rx="1.5" /></g> },
  phase: { label: "phase", family: "pipeline", draw: <path {...S} d="M1.5 3h6l3 3-3 3h-6l2-3z" /> },
  command: { label: "command", family: "pipeline", draw: <path {...S} d="M2 3.5L5 6 2 8.5M6 9h4" /> },
};

/** The glyph of a kind of name, its kind spelled out on hover (title: a longer hover text). */
export function KindIcon({ kind, title }: { kind: Kind; title?: string }) {
  const k = KINDS[kind];
  return (
    <svg className={`kind-icon nk-${k.family}`} viewBox="0 0 12 12" width="12" height="12" role="img" aria-label={k.label}>
      <title>{title || k.label}</title>
      {k.draw}
    </svg>
  );
}

/** A name with the glyph of what it is ("slot pilot" on hover, with an optional detail: "role pilot, inputs of pilot_app"). */
export function Named({ kind, name, detail, className, children }: {
  kind: Kind; name: string; detail?: string | null; className?: string; children?: ReactNode;
}) {
  const title = `${KINDS[kind].label}${name ? ` ${name}` : ""}${detail ? ` · ${detail}` : ""}`;
  return (
    <span className={"named" + (className ? ` ${className}` : "")} title={title}>
      <KindIcon kind={kind} title={title} />{children ?? name}
    </span>
  );
}

/** "slot pilot": the kind before the name, in plain text (sentences, hover texts). */
export const kindName = (kind: Kind, name: string) => `${KINDS[kind].label} ${name}`;
