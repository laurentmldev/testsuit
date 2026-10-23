// Client of the tsqd REST API (sequencer README, section 13). In production the UI is served under /ui,
// so the API is one level up; in development, Vite proxies /api to tsqd.
import { useEffect, useRef, useState } from "react";

export const API = import.meta.env.DEV ? "/api/" : new URL("../", window.location.href).toString();

export function url(path: string): string {
  return API + path.replace(/^\//, "");
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parse(response: Response): Promise<any> {
  const text = await response.text();
  let body: any = text;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    /* plain text */
  }
  if (!response.ok) {
    const detail = body && typeof body === "object" ? body.detail : body;
    const message = Array.isArray(detail)
      ? detail.map((d: any) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ")
      : String(detail || response.statusText);
    throw new ApiError(response.status, message);
  }
  return body;
}

export async function get<T = any>(path: string): Promise<T> {
  return parse(await fetch(url(path)));
}

export async function getText(path: string): Promise<{ text: string; size: number | null }> {
  const response = await fetch(url(path));
  if (!response.ok) await parse(response);
  const size = response.headers.get("X-Log-Size");
  return { text: await response.text(), size: size ? Number(size) : null };
}

export async function send<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "content-type": "application/json" };
  }
  return parse(await fetch(url(path), init));
}

export const post = <T = any>(path: string, body?: unknown) => send<T>("POST", path, body ?? {});
export const put = <T = any>(path: string, body?: unknown) => send<T>("PUT", path, body);
export const del = <T = any>(path: string) => send<T>("DELETE", path);

// ── live data ──────────────────────────────────────────────────────────

export type EventHandler = (kind: string, data: any) => void;

const listeners = new Set<EventHandler>();
let source: EventSource | null = null;
export const connection = { connected: false, listeners: new Set<(c: boolean) => void>() };

function ensureSource() {
  if (source) return;
  source = new EventSource(url("events"));
  const kinds = ["run", "phase", "step", "component", "lab", "release", "resource", "selftest", "state_mismatch"];
  for (const kind of kinds) {
    source.addEventListener(kind, (e) => {
      let data: any = null;
      try {
        data = JSON.parse((e as MessageEvent).data);
      } catch {
        return;
      }
      listeners.forEach((l) => l(kind, data));
    });
  }
  source.onopen = () => {
    connection.connected = true;
    connection.listeners.forEach((l) => l(true));
  };
  source.onerror = () => {
    connection.connected = false;
    connection.listeners.forEach((l) => l(false));
  };
}

/** Calls handler on each server event (one shared EventSource for the whole page). */
export function useEvents(handler: EventHandler) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    ensureSource();
    const l: EventHandler = (k, d) => ref.current(k, d);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
}

/** Fetches path, again on refresh(), and when one of the given event kinds arrives (debounced). */
export function useResource<T = any>(path: string | null, events: string[] = [], filter?: (data: any) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const timer = useRef<number | null>(null);
  const seq = useRef(0);

  const load = () => {
    if (!path) return;
    const n = ++seq.current;
    setLoading(true);
    get<T>(path)
      .then((d) => {
        if (n === seq.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => n === seq.current && setError(String(e.message || e)))
      .finally(() => n === seq.current && setLoading(false));
  };

  useEffect(() => {
    setData(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  useEvents((kind, payload) => {
    if (!events.includes(kind) || (filter && !filter(payload))) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, 300);
  });

  return { data, error, loading, refresh: load, setData };
}

export function useConnected(): boolean {
  const [connected, setConnected] = useState(connection.connected);
  useEffect(() => {
    ensureSource();
    connection.listeners.add(setConnected);
    return () => {
      connection.listeners.delete(setConnected);
    };
  }, []);
  return connected;
}
