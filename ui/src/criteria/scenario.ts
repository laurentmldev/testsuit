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

// Reading a scenario file as text, the way an editor needs it while the file is being typed (it may not
// parse): its entries and their lines, the field under the cursor, and the kind of each field of a
// criterion (x-mxp-kind in its JSON Schema).
import type { CriterionInfo, Description } from "./adapter";

export const KIND = "x-mxp-kind";
export type FieldKind = "expression" | "sequence_expression" | "param_name" | "cp_name";

export interface Entry {
  name: string;
  line: number;        // 1-based line of "name:"
  end: number;         // last line of the entry
  type: string | null;
  typeLine: number | null;
}

const TOP = /^([^\s#'"-][^:#]*?|"[^"]+"|'[^']+')\s*:\s*(#.*)?$/;
const TYPE = /^\s+type\s*:\s*['"]?([\w.-]+)['"]?\s*(#.*)?$/;

/** The entries of a scenario file (top-level keys), with their criterion. */
export function entries(text: string): Entry[] {
  const lines = text.split("\n");
  const out: Entry[] = [];
  lines.forEach((l, i) => {
    const m = l.match(TOP);
    if (m) {
      if (out.length) out[out.length - 1].end = i;
      out.push({ name: m[1].replace(/^["']|["']$/g, ""), line: i + 1, end: lines.length, type: null, typeLine: null });
    } else if (out.length && /^\S/.test(l) && !l.startsWith("#")) {
      out[out.length - 1].end = i;          // a top-level line that is not an entry
    }
  });
  for (const e of out) {
    let indent: number | null = null;
    for (let n = e.line; n < e.end && n < lines.length; n++) {
      const l = lines[n];
      if (!l.trim() || l.trimStart().startsWith("#")) continue;
      const own = l.length - l.trimStart().length;
      indent ??= own;
      const m = own === indent ? l.match(TYPE) : null;
      if (m) {
        e.type = m[1];
        e.typeLine = n + 1;
        break;
      }
    }
  }
  return out;
}

export function entryAt(list: Entry[], line: number): Entry | null {
  return list.find((e) => line >= e.line && line <= e.end) || null;
}

const FIELD = /^(\s*)(?:-\s+)?([A-Za-z_][\w]*)\s*:(\s*)(.*)$/;

/** The field whose value the cursor is in (line 1-based, column 1-based), following block scalars. */
export function fieldAt(text: string, line: number, column: number): string | null {
  const lines = text.split("\n");
  const current = lines[line - 1] ?? "";
  const before = current.slice(0, column - 1);
  const m = before.match(FIELD);
  if (m && m[1].length > 0) return m[2];
  if (m) return null;                     // the entry's own line
  // a continuation line (folded or literal block, or a long flow value): the nearest key less indented
  const indent = current.length - current.trimStart().length;
  if (!current.trim() && indent === 0) return null;
  for (let n = line - 2; n >= 0; n--) {
    const l = lines[n];
    if (!l.trim()) continue;
    const own = l.length - l.trimStart().length;
    if (own < indent || (own === indent && !current.trim())) {
      const k = l.match(FIELD);
      return k && k[1].length > 0 ? k[2] : null;
    }
  }
  return null;
}

/** Kind of each field of a criterion, by field name, wherever it is in its schema (sub-models too). */
export function kindsOf(crit: CriterionInfo | undefined): Record<string, FieldKind> {
  const out: Record<string, FieldKind> = {};
  if (!crit) return out;
  const defs = crit.schema?.$defs || {};
  const seen = new Set<any>();
  const kindIn = (node: any): FieldKind | null => {
    if (!node || typeof node !== "object") return null;
    if (node[KIND]) return node[KIND];
    for (const key of ["anyOf", "oneOf", "allOf"]) for (const n of node[key] || []) { const k = kindIn(n); if (k) return k; }
    if (node.items) return kindIn(node.items);
    if (node.$ref) return kindIn(defs[String(node.$ref).split("/").pop() || ""]);
    return null;
  };
  const walk = (node: any) => {
    if (!node || typeof node !== "object" || seen.has(node)) return;
    seen.add(node);
    for (const [name, prop] of Object.entries<any>(node.properties || {})) {
      const k = kindIn(prop);
      if (k) out[name] = k;
      walk(prop);
    }
    for (const key of ["anyOf", "oneOf", "allOf"]) (node[key] || []).forEach(walk);
    if (node.items) walk(node.items);
    if (node.additionalProperties && typeof node.additionalProperties === "object") walk(node.additionalProperties);
    if (node.$ref) walk(defs[String(node.$ref).split("/").pop() || ""]);
  };
  walk(crit.schema);
  return out;
}

/** The computed parameters defined by the entries before a line. */
export function computedBefore(list: Entry[], line: number, desc: Description | null): string[] {
  return list.filter((e) => e.line < line && e.type && desc?.criteria[e.type]?.defines_cp).map((e) => e.name);
}

/** A name not taken by an entry: base, base_2, base_3... */
export function freeEntryName(list: Entry[], base: string): string {
  const taken = new Set(list.map((e) => e.name));
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}_${i}`)) i++;
  return `${base}_${i}`;
}

/** The template of a criterion, its entry renamed to name. */
export function templateNamed(template: string, type: string, name: string): string {
  const text = template.trim() ? template : `${type}_check:\n  type: ${type}\n`;
  const lines = text.replace(/\s+$/, "").split("\n");
  const i = lines.findIndex((l) => TOP.test(l));
  if (i >= 0) lines[i] = `${name}:`;
  return lines.join("\n") + "\n";
}

/** The name an entry of a template starts with (its first top-level key). */
export function templateEntryName(template: string, type: string): string {
  const m = template.split("\n").map((l) => l.match(TOP)).find(Boolean);
  return m ? m[1] : `${type}_check`;
}

/** The fields of a criterion, from its schema: name, help, kind, whether required. */
export function fieldsOf(crit: CriterionInfo | undefined): { name: string; help: string; kind: FieldKind | null; required: boolean; type: string }[] {
  if (!crit) return [];
  const kinds = kindsOf(crit);
  const required = new Set<string>(crit.schema?.required || []);
  return Object.entries<any>(crit.schema?.properties || {}).filter(([name]) => name !== "type").map(([name, prop]) => ({
    name, help: prop.description || "", kind: kinds[name] || null, required: required.has(name), type: typeLabel(prop, crit.schema?.$defs || {}),
  }));
}

function typeLabel(prop: any, defs: any): string {
  if (!prop) return "";
  if (prop.$ref) return String(prop.$ref).split("/").pop() || "";
  if (prop.anyOf) return prop.anyOf.filter((p: any) => p.type !== "null").map((p: any) => typeLabel(p, defs)).join(" | ");
  if (prop.enum) return prop.enum.map((v: any) => JSON.stringify(v)).join(" | ");
  if (prop.type === "array") return `list of ${typeLabel(prop.items, defs) || "values"}`;
  if (prop.const !== undefined) return JSON.stringify(prop.const);
  return Array.isArray(prop.type) ? prop.type.join(" | ") : prop.type || "";
}
