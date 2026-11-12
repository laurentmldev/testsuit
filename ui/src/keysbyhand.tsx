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

// Keys set by hand (sequencer README, sections 5.3 and 7.7): values typed for a datapack generation or for a
// command run in maintenance, over the keys already defined. Setting a key nothing defines is harmless;
// overriding a defined one is said on its row, with the value it replaces.
import { Combo, type Choice } from "./fields";
import { classes } from "./util";

export interface HandKey { key: string; value: string }
/** What a key set by hand does: it overrides a defined key (was, origin), or cannot be set (blocked: why). */
export interface HandKeyStatus { overrides: boolean; was?: string; origin?: string; blocked?: string }

/** The rows as the API takes them: name -> value as typed (read as YAML by the server); unnamed rows left out. */
export function handKeys(rows: HandKey[]): Record<string, string> | undefined {
  const named = rows.filter((r) => r.key.trim());
  return named.length ? Object.fromEntries(named.map((r) => [r.key.trim(), r.value])) : undefined;
}

const shown = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value);

/** Status of a key against a list of defined keys (dotted names): the same rule as the server applies. */
export function statusAmong(defined: { key: string; value: string; origin?: string }[], builtin?: (origin: string) => boolean) {
  return (key: string): HandKeyStatus => {
    const hit = defined.find((d) => d.key === key) || defined.find((d) => key.startsWith(d.key + ".")) || defined.find((d) => d.key.startsWith(key + "."));
    if (!hit) return { overrides: false };
    if (builtin?.(hit.origin || "")) return { overrides: true, blocked: `${hit.key} is a built-in key: it cannot be set by hand` };
    return { overrides: true, was: hit.key === key ? hit.value : `${hit.key}: ${hit.value}`, origin: hit.origin };
  };
}

export function KeysByHand({ rows, onChange, status, known, missing, overrideNote, setNote }: {
  rows: HandKey[]; onChange: (rows: HandKey[]) => void;
  status: (key: string) => HandKeyStatus | undefined;      // undefined: not known yet
  known?: Choice[];                                         // the keys proposed for a new row
  missing?: string[];                                       // undefined keys that are read: proposed first
  overrideNote: string;                                     // what overriding a defined key means here
  setNote: string;                                          // and setting an undefined one
}) {
  const set = (i: number, patch: Partial<HandKey>) => onChange(rows.map((r, j) => j === i ? { ...r, ...patch } : r));
  const taken = new Set(rows.map((r) => r.key.trim()));
  const proposed = (missing || []).filter((m) => !taken.has(m));
  return (
    <div className="keys-by-hand">
      {rows.map((row, i) => {
        const name = row.key.trim();
        const bad = name && !/^[A-Za-z_]\w*(\.\w+)*$/.test(name) ? "not a key name (name, or name.sub for a nested key)"
          : name && rows.findIndex((r) => r.key.trim() === name) !== i ? "set twice" : null;
        const st = name && !bad ? status(name) : undefined;
        return (
          <div key={i} className="kbh-row">
            <div className="kbh-key"><Combo value={row.key} onChange={(key) => set(i, { key })} choices={(known || []).filter((c) => !taken.has(c.value))} placeholder="key (name.sub)" mono /></div>
            <span className="muted">=</span>
            <input className="mono kbh-value" value={row.value} placeholder="value" onChange={(e) => set(i, { value: e.target.value })} />
            <button type="button" className="icon" title="Remove: back to the defined value" onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
            <div className={classes("kbh-status small", (bad || st?.blocked) ? "bad-text" : st?.overrides ? "warn-text" : "muted")}>
              {bad || st?.blocked || (!st ? "" : st.overrides
                ? <><strong>overrides</strong> <code title={st.was}>{shown(st.was)}</code>{st.origin && <> from {st.origin}</>}: {overrideNote}</>
                : <>not defined elsewhere: {setNote}</>)}
            </div>
          </div>
        );
      })}
      <div className="kbh-add">
        <button type="button" className="small" onClick={() => onChange([...rows, { key: "", value: "" }])}>Set a key by hand</button>
        {proposed.length > 0 && <span className="muted small">undefined:</span>}
        {proposed.map((m) => <button key={m} type="button" className="small link mono" title="This key is read and not defined: set it by hand" onClick={() => onChange([...rows.filter((r) => r.key.trim() || r.value), { key: m, value: "" }])}>{m}</button>)}
      </div>
    </div>
  );
}
