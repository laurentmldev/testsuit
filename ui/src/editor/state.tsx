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

// State shared by the views of an editing session: its files, the helpers of the lab and workspace
// (context), the schemas, and the document being edited (saved to the scratch clone as you go).
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { get, post, put } from "../api";
import type { Choice, KeyItem } from "../fields";
import { notify } from "../util";

export interface Problem {
  file: string;
  line?: number;
  column?: number;
  severity: string;
  message: string;
}

export interface FileInfo {
  path: string;
  size: number;
  kind: string;
}

export interface Session {
  sid: string;
  info: any;
  ctx: any;
  schemas: any;
  files: FileInfo[];
  refreshFiles: () => Promise<void>;
  refreshContext: () => Promise<void>;
  open: (path: string, line?: number) => void;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("no editing session");
  return s;
}

export function useSessionData(sid: string, openFile: (path: string, line?: number) => void): Session | null {
  const [info, setInfo] = useState<any>(null);
  const [ctx, setCtx] = useState<any>(null);
  const [schemas, setSchemas] = useState<any>(null);
  const [files, setFiles] = useState<FileInfo[]>([]);
  const refreshFiles = useCallback(async () => {
    setFiles(await get(`edit/sessions/${sid}/files`));
    setInfo(await get(`edit/sessions/${sid}`));
  }, [sid]);
  const contextCall = useRef(0);
  const refreshContext = useCallback(async () => {
    const call = ++contextCall.current;      // each save asks again: only the latest answer is kept
    const [c, s] = await Promise.all([get(`edit/sessions/${sid}/context`), get(`edit/sessions/${sid}/schemas`)]);
    if (call !== contextCall.current) return;
    setCtx(c);
    setSchemas(s);
  }, [sid]);
  useEffect(() => {
    setInfo(null);
    refreshFiles().catch((e) => notify(e.message, "error"));
    refreshContext().catch((e) => notify(e.message, "error"));
  }, [sid, refreshFiles, refreshContext]);
  if (!info || !ctx || !schemas) return null;
  return { sid, info, ctx, schemas, files, refreshFiles, refreshContext, open: openFile };
}

// ── the document being edited ───────────────────────────────────────────

export interface Doc {
  path: string;
  kind: string;
  content: string;
  data: any;
  aliases?: boolean;
  problems: Problem[];
}

/** A file of the session: setData merges a new version of its data (forms), setText replaces its text
 * (YAML view). Both are saved to the scratch clone after a short pause, and checked by the server.
 * undo and redo step through the versions of the data (Ctrl+Z, Ctrl+Shift+Z or Ctrl+Y): edits less
 * than a second apart, such as typing in a field, make one step. The history starts with the file. */
export function useDoc(sid: string, path: string | null, onSaved?: (doc: Doc) => void) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [state, setState] = useState<"saved" | "pending" | "saving" | "error">("saved");
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ data?: any; content?: string } | null>(null);
  const timer = useRef<number | null>(null);
  const saving = useRef<Promise<void> | null>(null);
  const savedRef = useRef(onSaved);
  savedRef.current = onSaved;
  const docRef = useRef(doc);
  docRef.current = doc;
  const past = useRef<any[]>([]);
  const future = useRef<any[]>([]);
  const lastEdit = useRef(0);
  const edits = useRef(0);                   // counts the edits: a save answering an older one keeps what is typed
  const [, setHistory] = useState(0);

  useEffect(() => {
    setDoc(null);
    setError(null);
    pending.current = null;
    past.current = [];
    future.current = [];
    setHistory((n) => n + 1);
    if (!path) return;
    let cancelled = false;
    Promise.all([get(`edit/sessions/${sid}/files/${path}`), post(`edit/sessions/${sid}/validate`, { path }).catch(() => ({ problems: [] }))])
      .then(([d, v]) => !cancelled && setDoc({ ...d, problems: v.problems || [] }))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [sid, path]);

  const flush = useCallback(async () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    // one save at a time, in the order of the edits: two at once could end in the older one
    while (saving.current) await saving.current;
    const body = pending.current;
    if (!body || !path) return;
    pending.current = null;
    const sent = edits.current;
    setState("saving");
    saving.current = (async () => {
      try {
        const result = await put(`edit/sessions/${sid}/files/${path}`, body);
        setDoc((d) => {
          // typed since this save was sent: the field keeps it (the next save writes it)
          if (d && edits.current !== sent) return { ...result, content: d.content, data: d.data };
          return { ...result, data: body.content !== undefined && d ? result.data ?? d.data : result.data };
        });
        setState(pending.current ? "pending" : "saved");
        setError(null);
        savedRef.current?.(result);
      } catch (e: any) {
        setState("error");
        setError(e.message);
      } finally {
        saving.current = null;
      }
    })();
    await saving.current;
  }, [sid, path]);

  const schedule = (delay: number) => {
    setState("pending");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, delay);
  };

  const copy = (data: any) => (data === undefined ? undefined : JSON.parse(JSON.stringify(data)));
  /** Keeps the data before an edit as an undo step, unless the previous edit was less than a second ago. */
  const record = () => {
    const now = Date.now();
    const current = docRef.current?.data;
    if (current !== undefined && current !== null && now - lastEdit.current > 1000) {
      past.current.push(copy(current));
      if (past.current.length > 100) past.current.shift();
    }
    lastEdit.current = now;
    future.current = [];
    setHistory((n) => n + 1);
  };
  const apply = (data: any) => {
    edits.current++;
    setDoc((d) => (d ? { ...d, data } : d));
    pending.current = { data };
    schedule(500);
  };
  const setData = (data: any) => {
    record();
    apply(data);
  };
  const step = (from: MutableRefObject<any[]>, to: MutableRefObject<any[]>) => {
    const data = from.current.pop();
    if (data === undefined) return;
    to.current.push(copy(docRef.current?.data));
    lastEdit.current = 0;
    apply(data);
    setHistory((n) => n + 1);
  };
  const undo = () => step(past, future);
  const redo = () => step(future, past);
  const setText = (content: string) => {
    record();
    edits.current++;
    setDoc((d) => (d ? { ...d, content } : d));
    pending.current = { content };
    schedule(1200);
  };
  useEffect(() => () => {
    if (pending.current) flush();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  return { doc, state, error, setData, setText, flush, undo, redo, canUndo: past.current.length > 0, canRedo: future.current.length > 0 };
}

// ── choices built from the context ──────────────────────────────────────

const sourceGroup = (source: string) => (source === "<builtin>" ? "built-in" : source === "<plugin>" ? "plugin" : undefined);

/** Lab roles (abstract): the commands each offers, as a host's `tsq_role` or a role's `extends`. */
export function labRoleChoices(ctx: any, filter?: (name: string, r: any) => boolean): Choice[] {
  return Object.entries(ctx.lab.roles || {})
    .filter(([name, r]) => !filter || filter(name, r))
    .map(([name, r]: [string, any]) => ({
      value: name,
      description: [
        r.description,
        r.extends ? `extends \`${r.extends}\`` : "",
        Object.keys(r.commands || {}).length ? `commands: ${Object.keys(r.commands).map((c) => `\`${c}\``).join(" ")}` : "no command",
        r.used_by?.length ? `used by ${r.used_by.map((h: string) => hostLabel(ctx, h)).join(", ")}` : "",
      ].filter(Boolean).join(" · "),
      group: sourceGroup(r.source),
    }));
}

/** Lab components (concrete): what a library component's `extends` names. With hosts, the inventory hosts
 * too (a host's `tsq_extends` can name another host). */
/** Where a probe or command can run instead of on its component (`via`): a role of its stage, else of an ad hoc
 *  component, or a host. */
export function viaChoices(ctx: any): Choice[] {
  return [
    ...labRoleChoices(ctx).map((c) => ({ ...c, description: `the component of role ${c.value} in its stage, else the ad hoc one` + (c.description ? ` · ${c.description}` : "") })),
    ...Object.keys(ctx.lab.components || {}).map((h) => ({ value: h, description: (hostLabel(ctx, h) !== h ? `${hostLabel(ctx, h)} · ` : "") + "this host of the inventory" })),
  ];
}

/** What the editor shows for a lab host: its label (tsq_label), else its id. */
export function hostLabel(ctx: any, id: string): string {
  return ctx?.lab?.components?.[id]?.label || id;
}

export function componentChoices(ctx: any, opts: { filter?: (name: string) => boolean; hosts?: boolean } = {}): Choice[] {
  const result: Choice[] = Object.entries(ctx.lab.library || {})
    .filter(([name]) => !opts.filter || opts.filter(name))
    .map(([name, c]: [string, any]) => ({
      value: name,
      description: [
        c.description,
        c.extends ? `extends \`${c.extends}\`` : "",
        Object.keys(c.commands || {}).length ? `implements: ${Object.keys(c.commands).map((x) => `\`${x}\``).join(" ")}` : "implements nothing",
        c.used_by?.length ? `extended by ${c.used_by.map((h: string) => hostLabel(ctx, h)).join(", ")}` : "",
      ].filter(Boolean).join(" · "),
      group: sourceGroup(c.source),
    }));
  if (opts.hosts)
    for (const [id, h] of Object.entries<any>(ctx.lab.components || {}))
      if (!opts.filter || opts.filter(id))
        result.push({ value: id, description: `host, role ${h.role || "?"}, extends ${h.extends}`, group: "hosts" });
  return result;
}

export function stageTypeChoices(ctx: any): Choice[] {
  const result: Record<string, Choice> = {};
  for (const [type, stages] of Object.entries<any>(ctx.lab.stage_types || {})) {
    const roles = Object.keys(ctx.lab.stage_type_roles?.[type] || {}).join(", ");
    result[type] = { value: type, description: `in the lab: ${stages.join(", ")}${roles ? ` · roles ${roles}` : ""}` };
  }
  for (const setup of ctx.workspace?.setups || [])
    for (const [cfg, c] of Object.entries<any>(setup.configurations || {}))
      for (const [, type] of Object.entries<any>(c.stage_slots || {}))
        if (type && !result[type]) result[type] = { value: type, description: `asked for by setup ${setup.id} (${cfg}), no such stage in the lab yet` };
  return Object.values(result);
}

/** Roles: the lab roles components take as `tsq_role`. stageType narrows to the roles of the stages of
 * that type, adhoc to those of the ad hoc components; otherwise every lab role. The roles datapack setups
 * ask for and no lab component has come after. */
export function roleChoices(ctx: any, opts: { stageType?: string | null; adhoc?: boolean } = {}): Choice[] {
  const result: Record<string, Choice> = {};
  const types = Object.fromEntries(labRoleChoices(ctx).map((c) => [c.value, c]));
  const roles = opts.stageType ? Object.keys(ctx.lab.stage_type_roles?.[opts.stageType] || {})
    : opts.adhoc ? Object.keys(ctx.lab.adhoc_roles || {}) : Object.keys(types);
  for (const role of roles)
    result[role] = { value: role, description: (opts.stageType ? `in the ${opts.stageType} stages · ` : opts.adhoc ? "ad hoc · " : "") + (types[role]?.description || "not a lab role") };
  for (const setup of ctx.workspace?.setups || [])
    for (const [cfg, c] of Object.entries<any>(setup.configurations || {}))
      for (const [id, comp] of Object.entries<any>(c.slots || {})) {
        if (!comp.role || result[comp.role]) continue;
        const stageType = comp.stage_slot ? c.stage_slots?.[comp.stage_slot] : null;
        if ((opts.stageType && stageType !== opts.stageType) || (opts.adhoc && comp.stage_slot)) continue;
        result[comp.role] = { value: comp.role, description: `asked for by setup ${setup.id} (${cfg}), slot ${id}: no lab component has it yet${types[comp.role] ? "" : ", and no lab role of that name"}` };
      }
  return Object.values(result);
}

export function transportChoices(ctx: any): Choice[] {
  const help: Record<string, string> = {
    ssh: "over SSH, key-based (the default)", local: "on the sequencer host, as the user running tsqd: emulated components, simulations",
  };
  return (ctx.transports || ["local", "ssh"]).map((t: string) => ({ value: t, description: help[t] || "plugin transport" }));
}

/** Every file of the session of these kinds, as choices. */
export function fileChoices(files: FileInfo[], kinds: string[], relativeTo?: string): Choice[] {
  return files.filter((f) => kinds.includes(f.kind)).map((f) => ({ value: relativeTo ? relativeFrom(relativeTo, f.path) : f.path, description: f.kind }));
}

function relativeFrom(fromFile: string, to: string): string {
  const a = fromFile.split("/").slice(0, -1);
  const b = to.split("/");
  let i = 0;
  while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
  return [...Array(a.length - i).fill(".."), ...b.slice(i)].join("/");
}

export function clone<T>(x: T): T {
  return x === undefined ? x : JSON.parse(JSON.stringify(x));
}

// ── keys a field rendered with keys can use (the {{ picker) ──────────────

/** Run-time keys of commands and steps (slot.*, component.*, run.*, params.*...), with their help. */
export function runtimeKeyItems(ctx: any): KeyItem[] {
  return Object.entries<string>(ctx?.runtime_keys || {}).map(([key, help]) => ({ key, help }));
}

/** The keys of a test definition's datapack (dictionaries, setup), with their values and files, then
 * the run-time keys. Datapack keys are also keys of its steps at run time. */
export function useTestKeys(sid: string, testdef: string | null | undefined, ctx: any, refresh?: unknown): KeyItem[] {
  const [items, setItems] = useState<KeyItem[]>([]);
  useEffect(() => {
    if (!testdef) return setItems([]);
    post(`edit/sessions/${sid}/keys`, { testdef })
      .then((r: any) => setItems((r.keys || []).map((k: any) => ({ key: k.key, value: k.value, origin: k.origin }))))
      .catch(() => setItems([]));
  }, [sid, testdef, refresh]);
  return useMemo(() => [...items, ...runtimeKeyItems(ctx)], [items, ctx]);
}

/** Tags of the lab components (or stages), each with who has it: their ids are tags too. */
export function labTagChoices(ctx: any, kind: "components" | "stages"): Choice[] {
  const owners: Record<string, string[]> = {};
  for (const [id, item] of Object.entries<any>(ctx.lab[kind] || {}))
    for (const tag of item?.tags || [id]) (owners[tag] ||= []).push(id);
  return Object.entries(owners).map(([tag, ids]) => ({ value: tag, description: ids.length === 1 && ids[0] === tag ? `the id of ${ids[0]}` : `${kind}: ${ids.join(", ")}` }));
}

/** Tags a stage slot can ask for: those of the lab stages of its type. */
export function stageSlotTagChoices(ctx: any, stageType: string): Choice[] {
  return labTagChoices({ lab: { stages: Object.fromEntries(Object.entries<any>(ctx.lab.stages || {}).filter(([, s]) => s?.type === stageType)) } }, "stages");
}

/** Tags a slot can ask for: those of the lab components it could be bound to, the ones whose role is its role or
 * extends it, in the stages of its stage slot's type having the stage slot's tags (ad hoc components otherwise). */
export function slotTagChoices(ctx: any, role: string, stageType: string | null, stageTags: string[] = []): Choice[] {
  const stages = Object.entries<any>(ctx.lab.stages || {})
    .filter(([, s]) => stageType && s?.type === stageType && stageTags.every((t) => (s?.tags || []).includes(t)))
    .map(([id]) => id);
  const fitting = Object.fromEntries(Object.entries<any>(ctx.lab.components || {}).filter(([, c]) =>
    (!role || (c?.roles || [c?.role]).includes(role))
    && (stageType ? (c?.stages || []).some((s: string) => stages.includes(s)) : !(c?.stages || []).length)));
  return labTagChoices({ lab: { components: fitting } }, "components");
}
