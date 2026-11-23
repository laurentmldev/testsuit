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

// What a name is: the same word is often a lab role, a component class, a slot and a slot class ("pilot").
// A small glyph before the name tells them apart everywhere, the hover spells the kind out.
// The colour gives the side: lab (what exists) or datapack (what a test needs). The shape gives what it is,
// a component and the slot bound to it sharing the square, a stage and a stage slot a group of squares
// (the components it holds), a role a list of commands. Filled: a lab thing that is there; outlined: a slot, still to be filled;
// dashed: a class or type, of which those are instances.
import type { ReactNode } from "react";

export type Kind = "component" | "component_class" | "role" | "stage" | "stage_type" | "slot" | "stage_slot" | "slot_class" | "phase" | "command";

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.3, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
const D = { ...S, strokeWidth: 1, strokeDasharray: "1 1.6" };   // thinner, short dashes: gaps that show at 12 px
const F = { fill: "currentColor", stroke: "currentColor", strokeWidth: 1.3, strokeLinejoin: "round" as const };
const SQUARE = { x: "2.5", y: "2.5", width: "7", height: "7", rx: "1.5" };
const GROUP = <><rect x="1.2" y="1.2" width="3.6" height="3.6" rx="0.8" /><rect x="7.2" y="1.2" width="3.6" height="3.6" rx="0.8" /><rect x="4.2" y="7.2" width="3.6" height="3.6" rx="0.8" /></>;

export const KINDS: Record<Kind, { label: string; family: "lab" | "test" | "pipeline"; draw: ReactNode }> = {
  component: { label: "lab component", family: "lab", draw: <rect {...F} {...SQUARE} /> },
  component_class: { label: "component class", family: "lab", draw: <rect {...D} {...SQUARE} /> },
  role: { label: "lab role", family: "lab", draw: <path {...S} d="M1.5 2l1.3 1-1.3 1M1.5 5l1.3 1-1.3 1M1.5 8l1.3 1-1.3 1M5 3h5.5M5 6h5.5M5 9h5.5" /> },
  stage: { label: "lab stage", family: "lab", draw: <g {...F}>{GROUP}</g> },
  stage_type: { label: "stage type", family: "lab", draw: <g {...D}>{GROUP}</g> },
  slot: { label: "slot", family: "test", draw: <rect {...S} {...SQUARE} /> },
  stage_slot: { label: "stage slot", family: "test", draw: <g {...S}>{GROUP}</g> },
  slot_class: { label: "slot class", family: "test", draw: <rect {...D} {...SQUARE} /> },
  phase: { label: "phase", family: "pipeline", draw: <path {...S} d="M1.5 3h6l3 3-3 3h-6l2-3z" /> },
  command: { label: "command", family: "pipeline", draw: <path {...S} d="M2 3.5L5 6 2 8.5M6 9h4" /> },
};

/** The extra class of a name of this kind: role names read as weaker content (lighter, italic). */
export const nameClass = (kind?: Kind | null) => (kind === "role" ? "role-name" : "");

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
    <span className={["named", name && nameClass(kind), className].filter(Boolean).join(" ")} title={title}>
      <KindIcon kind={kind} title={title} />{children ?? name}
    </span>
  );
}

/** "slot pilot": the kind before the name, in plain text (sentences, hover texts). */
export const kindName = (kind: Kind, name: string) => `${KINDS[kind].label} ${name}`;

const LEGEND: { title: string; kinds: Kind[] }[] = [
  { title: "Lab", kinds: ["component", "component_class", "role", "stage", "stage_type"] },
  { title: "Datapack", kinds: ["slot", "slot_class", "stage_slot"] },
  { title: "Pipeline", kinds: ["phase", "command"] },
];

/** What the glyphs mean: a button of the top bar, opening the legend. */
export function KindLegend() {
  return (
    <details className="kind-legend">
      <summary title="What the small glyphs before names mean">legend</summary>
      <div className="kind-legend-pop">
        <div className="kind-legend-cols">
          {LEGEND.map((g) => (
            <div key={g.title}>
              <div className={`kind-legend-title nk-${KINDS[g.kinds[0]].family}`}>{g.title}</div>
              {g.kinds.map((k) => <div key={k}><KindIcon kind={k} /> {KINDS[k].label}</div>)}
            </div>
          ))}
        </div>
        <p className="muted small">
          The colour gives the side: the lab (what exists) or the datapack (what a test needs). Filled: in the
          lab; outlined: a slot, to be filled with a lab component; dashed: a class or a type. A component and its
          slot are squares, a stage and its stage slot a group of squares, a role a list of commands.
        </p>
      </div>
    </details>
  );
}
