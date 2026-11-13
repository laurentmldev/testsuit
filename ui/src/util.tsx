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

// Small helpers: hash routing, formatting, light Markdown for help texts, notifications.
import { Fragment, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

// ── routing (#/page/arg?key=value) ─────────────────────────────────────

export interface Route {
  parts: string[];
  query: URLSearchParams;
}

function parseHash(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const [path, query] = hash.split("?");
  return { parts: path.split("/").filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(query || "") };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

function hashOf(path: string, query?: Record<string, string | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query || {})) if (v) q.set(k, v);
  const s = q.toString();
  return "#/" + path.replace(/^\//, "") + (s ? "?" + s : "");
}

export function navigate(path: string, query?: Record<string, string | null | undefined>) {
  window.location.hash = hashOf(path, query);
}

// Opens a route of this app in a new browser tab. The tab is opened at once (inside the click, so the
// browser does not block it as a popup); `target` may be a promise for the route, resolved afterwards, and
// the tab is closed again if it resolves to nothing.
export async function openInNewTab(target: { path: string; query?: Record<string, string | null | undefined> } | null
  | Promise<{ path: string; query?: Record<string, string | null | undefined> } | null>) {
  const base = window.location.href.split("#")[0];
  const tab = window.open("", "_blank");
  let t = null;
  try { t = await target; } catch { /* reported by the caller */ }
  if (!t) { tab?.close(); return; }
  const url = base + hashOf(t.path, t.query);
  if (tab) tab.location.href = url;
  else window.location.hash = hashOf(t.path, t.query);  // popups blocked: open here instead
}

export function setQuery(key: string, value: string | null) {
  const route = parseHash();
  const q = route.query;
  if (value) q.set(key, value);
  else q.delete(key);
  const s = q.toString();
  const hash = "#/" + route.parts.map(encodeURIComponent).join("/") + (s ? "?" + s : "");
  if (window.location.hash !== hash) window.history.replaceState(null, "", hash), window.dispatchEvent(new HashChangeEvent("hashchange"));
}

// ── formatting ──────────────────────────────────────────────────────────

export function ago(iso?: string | null): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "now";
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function duration(from?: string | null, to?: string | null): string {
  if (!from) return "";
  const s = ((to ? new Date(to).getTime() : Date.now()) - new Date(from).getTime()) / 1000;
  if (s < 60) return `${s.toFixed(0)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.floor(s % 60)} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

export function time(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "medium" });
}

export function size(n?: number | null): string {
  if (n === null || n === undefined) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1 << 20) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1 << 30) return `${(n / (1 << 20)).toFixed(1)} MB`;
  return `${(n / (1 << 30)).toFixed(2)} GB`;
}

export function shortId(id: string): string {
  return id.length > 12 ? id.slice(0, 12) : id;
}

// ── light Markdown: `code`, **bold**, [links](https://…), lists, code blocks, paragraphs ──

function inline(text: string, key: number): ReactNode {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)\s]+\)|https?:\/\/[^\s<>()]*[^\s<>().,;:!?'"])/g);
  return (
    <Fragment key={key}>
      {parts.map((p, i) => {
        if (p.startsWith("`") && p.endsWith("`") && p.length > 1) return <code key={i}>{p.slice(1, -1)}</code>;
        if (p.startsWith("**") && p.endsWith("**") && p.length > 4) return <strong key={i}>{p.slice(2, -2)}</strong>;
        const md = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(p);
        const href = md ? md[2] : /^https?:\/\//.test(p) ? p : null;
        if (href && /^(https?:|mailto:)/i.test(href))   // only web and mail links: never javascript: or the like
          return <a key={i} href={href} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>{md ? md[1] : p}</a>;
        return p;
      })}
    </Fragment>
  );
}

/** A small spinning ring, with what is being waited for next to it. */
export function Spinner({ text, className }: { text?: string; className?: string }) {
  return (
    <span className={classes("spinner-line", className)} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />{text && <span>{text}</span>}
    </span>
  );
}

export function Markdown({ text, className }: { text?: string | null; className?: string }) {
  if (!text) return null;
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) blocks.push(<ul key={blocks.length}>{list.map((l, i) => <li key={i}>{inline(l, i)}</li>)}</ul>);
    list = [];
  };
  for (const para of text.split(/\n\n+/)) {
    if (para.trimStart().startsWith("```")) {              // a code block (without blank lines in it)
      flush();
      blocks.push(<pre key={blocks.length} className="md-code">{para.trim().split("\n").slice(1).filter((l, i, all) => i < all.length - 1 || !l.startsWith("```")).join("\n")}</pre>);
      continue;
    }
    for (const line of para.split("\n")) {
      if (/^\s*- /.test(line)) list.push(line.replace(/^\s*- /, ""));
      else {
        flush();
        if (line.trim()) blocks.push(<p key={blocks.length}>{inline(line, 0)}</p>);
      }
    }
    flush();
  }
  return <div className={"md " + (className || "")}>{blocks}</div>;
}

// ── notifications ───────────────────────────────────────────────────────

type Toast = { id: number; text: string; kind: "ok" | "error" | "info" };
let toasts: Toast[] = [];
const toastListeners = new Set<(t: Toast[]) => void>();
let nextToast = 1;

export function notify(text: string, kind: Toast["kind"] = "ok") {
  const toast = { id: nextToast++, text, kind };
  toasts = [...toasts, toast];
  toastListeners.forEach((l) => l(toasts));
  window.setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== toast.id);
    toastListeners.forEach((l) => l(toasts));
  }, kind === "error" ? 9000 : 4000);
}

export function Toasts() {
  const [list, setList] = useState<Toast[]>(toasts);
  useEffect(() => {
    toastListeners.add(setList);
    return () => {
      toastListeners.delete(setList);
    };
  }, []);
  return (
    <div className="toasts">
      {list.map((t) => (
        <div key={t.id} className={"toast " + t.kind}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

/** Runs an action, notifying its error (and its success message when given). */
export async function attempt<T>(action: () => Promise<T>, success?: string): Promise<T | undefined> {
  try {
    const result = await action();
    if (success) notify(success);
    return result;
  } catch (e: any) {
    notify(String(e?.message || e), "error");
    return undefined;
  }
}

export function classes(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}

export const STATE_COLORS: Record<string, string> = {
  succeeded: "ok", passed: "ok", running: "run", active: "run", queued: "wait", waiting: "wait", pending: "wait",
  failed: "bad", error: "bad", broken: "bad", offline: "bad", cancelled: "muted", killed: "bad", skipped: "muted",
  idle: "idle", configured: "info", unknown: "muted", done: "ok", OK: "ok", FAILED: "bad",
};

/** A component's address: its name of the lab's host names (tsq_hosts) when it has one, the address it stands for
 *  shown when pointed at; else the address. */
export function Address({ host, alias }: { host?: string | null; alias?: string | null }) {
  if (!alias) return <>{host}</>;
  return <span className="host-alias" title={`${alias} is ${host} (the lab's host names)`}>{alias}</span>;
}

export function Badge({ value, title }: { value?: string | null; title?: string }) {
  if (!value) return null;
  return (
    <span className={"badge " + (STATE_COLORS[value] || "muted")} title={title}>
      {value}
    </span>
  );
}

/** What the user types in an id field, as an id: other characters than letters, digits and "_" as "_". Unlike slug(),
 * nothing is trimmed and nothing is made up, so an empty field stays empty (and shows as invalid). */
export function idInput(text: string): string {
  return text.replace(/[^A-Za-z0-9_]+/g, "_");
}

/** The same for a lab id (stage, component, lab role or component, stage type): components (inventory host
 * names) may also have "." and "-". */
export function labIdInput(text: string): string {
  return text.replace(/[^A-Za-z0-9_.-]+/g, "_");
}

/** A lab id for a name. */
export function labId(name: string): string {
  return labIdInput(name).replace(/^_+/, "");
}

/** Why a lab id is not usable, or null. */
export function labIdProblem(id: string, taken: Iterable<string> = []): string | null {
  return idProblem(id, taken);
}

/** Why an id is not usable, or null. */
export function idProblem(id: string, taken: Iterable<string> = []): string | null {
  if (!id) return "required";
  if (!/[A-Za-z0-9]/.test(id)) return "letters or digits needed";
  if ([...taken].includes(id)) return "already used";
  return null;
}

export function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "item";
}

/** A name not in taken: base, base_2, base_3... */
export function freeName(base: string, taken: Iterable<string>): string {
  const set = new Set(taken);
  if (!set.has(base)) return base;
  for (let n = 2; ; n++) if (!set.has(`${base}_${n}`)) return `${base}_${n}`;
}

export function relPath(from: string, to: string): string {
  // relative path from the folder of file `from` to file `to` (both relative to the same root)
  const a = from.split("/").slice(0, -1);
  const b = to.split("/");
  let i = 0;
  while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
  return [...Array(a.length - i).fill(".."), ...b.slice(i)].join("/") || ".";
}

export function joinPath(fromFile: string, rel: string): string {
  const parts = fromFile.split("/").slice(0, -1);
  for (const p of rel.split("/")) {
    if (p === "..") parts.pop();
    else if (p !== "." && p !== "") parts.push(p);
  }
  return parts.join("/");
}

/** A probe or command that does not run on its own component but through another one (``via``: a jump host, a
 *  stage server…): the component it runs on, in the accent colour, so it is not taken for the component itself. */
export function ViaTag({ via, host, label, children, title }: {
  via: string; host?: string | null; label?: string | null; children?: ReactNode; title?: string;
}) {
  return (
    <span className="via-tag" title={title ?? `Runs on ${via}${host ? ` (${host})` : ""}, not on the component itself: ${via} reaches it`}>
      <span className="via-arrow" aria-hidden="true">↪</span>{children ?? "via"} <strong>{label || via}</strong>{host && <span className="via-host"> {host}</span>}
    </span>
  );
}

/** Where a probe or command goes when it runs through another component: from the sequencer to the component it
 *  runs on, which reaches the target. */
export function ViaRoute({ via, viaHost, target, targetHost }: {
  via: string; viaHost?: string | null; target: string; targetHost?: string | null;
}) {
  return (
    <div className="via-route">
      <div className="via-route-path">
        <span className="via-node muted">sequencer</span>
        <span className="via-link" aria-hidden="true">→</span>
        <span className="via-node via-hop"><strong>{via}</strong>{viaHost && <span className="via-host"> {viaHost}</span>}</span>
        <span className="via-link" aria-hidden="true">→</span>
        <span className="via-node"><strong>{target}</strong>{targetHost && <span className="via-host"> {targetHost}</span>}</span>
      </div>
    </div>
  );
}

/** A line a traced command ran (the sequencer's set -x: "+tsq$ "), in its output just before what it printed. */
const TRACED = /^\++tsq\$ /;
/** A note of the sequencer in a step log ("[tsq <time>] …"). */
const NOTE = /^\[tsq [^\]]*\] /;

/** A comment line of a traced command, made a no-op (": '# …'") to show where it is; null for another line. */
function commentOf(ran: string): string | null {
  if (!ran.startsWith(": ")) return null;
  let arg = ran.slice(2);
  if (arg.startsWith("'") && arg.endsWith("'")) arg = arg.slice(1, -1).replace(/'\\''/g, "'");   // bash quotes it
  return arg.startsWith("#") ? arg : null;
}

/** The output of a command as a console: each line it ran, shown as a prompt, then what that line printed; its
 *  comments where they are. */
export function ConsoleText({ text }: { text: string }) {
  const lines = text.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return (
    <>
      {lines.map((line, i) => TRACED.test(line)
        ? (commentOf(line.replace(TRACED, "")) !== null
          ? <div key={i} className="console-comment">{commentOf(line.replace(TRACED, ""))}</div>
          : <div key={i} className="console-cmd"><span className="console-prompt">$ </span>{line.replace(TRACED, "")}</div>)
        : NOTE.test(line) ? <div key={i} className="console-note">{line}</div>
          : <div key={i} className="console-out">{line || " "}</div>)}
    </>
  );
}

/** The output of a command that runs, kept in view: as lines come, the dialog (or page) scrolls with them, unless
 *  the user scrolled away from its end (to read above), then it stays where it is until they come back. */
export function LiveConsole({ text, empty }: { text: string; empty?: ReactNode }) {
  const end = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  useEffect(() => {                          // what the user does: back at its end, or away from it
    const check = () => {
      const r = end.current?.getBoundingClientRect();
      if (r) following.current = r.top <= window.innerHeight + 40;
    };
    window.addEventListener("scroll", check, true);      // any scroll: the page's or the dialog's
    return () => window.removeEventListener("scroll", check, true);
  }, []);
  useLayoutEffect(() => {
    if (following.current) end.current?.scrollIntoView({ block: "end" });
  }, [text]);
  return (
    <pre className="small console">
      {text ? <ConsoleText text={text} /> : empty}
      <div ref={end} />
    </pre>
  );
}

/** A button opening a short list of actions under it (a menu); a click outside, or Escape, closes it. */
export function MenuButton({ label, items, disabled, title }: {
  label: ReactNode; disabled?: boolean; title?: string;
  items: { label: ReactNode; title?: string; onClick: () => void }[];
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return (
    <span className="menu-button" ref={box}>
      <button disabled={disabled} title={title} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {label} <span className="menu-caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="menu" role="menu">
          {items.map((item, i) => (
            <button key={i} role="menuitem" title={item.title} onClick={() => { setOpen(false); item.onClick(); }}>{item.label}</button>
          ))}
        </div>
      )}
    </span>
  );
}
