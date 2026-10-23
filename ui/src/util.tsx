// Small helpers: hash routing, formatting, light Markdown for help texts, notifications.
import { Fragment, ReactNode, useEffect, useState } from "react";

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

export function navigate(path: string, query?: Record<string, string | null | undefined>) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query || {})) if (v) q.set(k, v);
  const s = q.toString();
  window.location.hash = "#/" + path.replace(/^\//, "") + (s ? "?" + s : "");
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

// ── light Markdown: `code`, **bold**, lists, paragraphs ────────────────

function inline(text: string, key: number): ReactNode {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return (
    <Fragment key={key}>
      {parts.map((p, i) =>
        p.startsWith("`") && p.endsWith("`") && p.length > 1 ? (
          <code key={i}>{p.slice(1, -1)}</code>
        ) : p.startsWith("**") && p.endsWith("**") ? (
          <strong key={i}>{p.slice(2, -2)}</strong>
        ) : (
          p
        ),
      )}
    </Fragment>
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
  idle: "idle", configured: "info", unknown: "muted", done: "ok",
};

export function Badge({ value, title }: { value?: string | null; title?: string }) {
  if (!value) return null;
  return (
    <span className={"badge " + (STATE_COLORS[value] || "muted")} title={title}>
      {value}
    </span>
  );
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
