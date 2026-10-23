// State shared by the views of an editing session: its files, the helpers of the lab and workspace
// (context), the schemas, and the document being edited (saved to the scratch clone as you go).
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { get, post, put } from "../api";
import type { Choice } from "../fields";
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
  const refreshContext = useCallback(async () => {
    const [c, s] = await Promise.all([get(`edit/sessions/${sid}/context`), get(`edit/sessions/${sid}/schemas`)]);
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
 * (YAML view). Both are saved to the scratch clone after a short pause, and checked by the server. */
export function useDoc(sid: string, path: string | null, onSaved?: (doc: Doc) => void) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [state, setState] = useState<"saved" | "pending" | "saving" | "error">("saved");
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ data?: any; content?: string } | null>(null);
  const timer = useRef<number | null>(null);
  const saving = useRef<Promise<void> | null>(null);
  const savedRef = useRef(onSaved);
  savedRef.current = onSaved;

  useEffect(() => {
    setDoc(null);
    setError(null);
    pending.current = null;
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
    if (saving.current) await saving.current;
    const body = pending.current;
    if (!body || !path) return;
    pending.current = null;
    setState("saving");
    saving.current = (async () => {
      try {
        const result = await put(`edit/sessions/${sid}/files/${path}`, body);
        setDoc((d) => ({
          ...result,
          // keep what was typed meanwhile
          content: pending.current?.content ?? result.content,
          data: pending.current?.data ?? (body.content !== undefined && d ? result.data ?? d.data : result.data),
        }));
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

  const setData = (data: any) => {
    setDoc((d) => (d ? { ...d, data } : d));
    pending.current = { data };
    schedule(500);
  };
  const setText = (content: string) => {
    setDoc((d) => (d ? { ...d, content } : d));
    pending.current = { content };
    schedule(1200);
  };
  useEffect(() => () => {
    if (pending.current) flush();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  return { doc, state, error, setData, setText, flush };
}

// ── choices built from the context ──────────────────────────────────────

export function typeChoices(ctx: any, filter?: (name: string, t: any) => boolean): Choice[] {
  return Object.entries(ctx.lab.types || {})
    .filter(([name, t]) => !filter || filter(name, t))
    .map(([name, t]: [string, any]) => ({
      value: name,
      description: [
        t.description,
        t.extends ? `extends \`${t.extends}\`` : "",
        Object.keys(t.commands || {}).length ? `commands: ${Object.keys(t.commands).map((c) => `\`${c}\``).join(" ")}` : "no command",
        t.used_by?.length ? `used by ${t.used_by.join(", ")}` : "",
      ].filter(Boolean).join(" · "),
      group: t.source === "<builtin>" ? "built-in" : t.source === "<plugin>" ? "plugin" : undefined,
    }));
}

export function stageTypeChoices(ctx: any): Choice[] {
  const result: Record<string, Choice> = {};
  for (const [type, stages] of Object.entries<any>(ctx.lab.stage_types || {})) {
    const roles = Object.entries(ctx.lab.stage_type_roles?.[type] || {}).map(([r, t]) => `${r} (${t})`).join(", ");
    result[type] = { value: type, description: `in the lab: ${stages.join(", ")}${roles ? ` · roles ${roles}` : ""}` };
  }
  for (const setup of ctx.workspace?.setups || [])
    for (const [cfg, c] of Object.entries<any>(setup.configurations || {}))
      for (const [, type] of Object.entries<any>(c.stages || {}))
        if (type && !result[type]) result[type] = { value: type, description: `asked for by setup ${setup.id} (${cfg}), no such stage in the lab yet` };
  return Object.values(result);
}

/** Roles: those used in the lab, then those datapack setups ask for. stageType narrows to the roles of
 * stages of that type; adhoc to the ad hoc components. */
export function roleChoices(ctx: any, opts: { stageType?: string | null; adhoc?: boolean } = {}): Choice[] {
  const result: Record<string, Choice> = {};
  if (opts.stageType) {
    for (const [role, type] of Object.entries<any>(ctx.lab.stage_type_roles?.[opts.stageType] || {}))
      result[role] = { value: role, description: `in the ${opts.stageType} stages: a \`${type}\`` };
  } else if (opts.adhoc) {
    for (const [role, type] of Object.entries<any>(ctx.lab.adhoc_roles || {}))
      result[role] = { value: role, description: `ad hoc component of type \`${type}\`` };
  } else {
    for (const [role, comps] of Object.entries<any>(ctx.lab.roles || {}))
      result[role] = { value: role, description: `used by ${comps.join(", ")}` };
  }
  for (const setup of ctx.workspace?.setups || [])
    for (const [cfg, c] of Object.entries<any>(setup.configurations || {}))
      for (const [id, comp] of Object.entries<any>(c.components || {})) {
        if (!comp.role || result[comp.role]) continue;
        const stageType = comp.stage ? c.stages?.[comp.stage] : null;
        if ((opts.stageType && stageType !== opts.stageType) || (opts.adhoc && comp.stage)) continue;
        result[comp.role] = { value: comp.role, description: `asked for by setup ${setup.id} (${cfg}), component ${id}: no lab component has it yet` };
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
