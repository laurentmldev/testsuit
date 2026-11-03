// The log of tsqd (Lab page, Logs): the events of the sequencer (runs queued, started, ended; components
// changing state; lab reloads; holds and releases), datapack generations and pushes of the Editor, its
// warnings and errors with their traceback, and the state probes of the lab components (each command, its
// result, its errors). Refreshed every 2 s while open.
import { useEffect, useRef, useState } from "react";
import { get } from "../api";
import { Dialog } from "../fields";

const LEVELS = [
  { value: "", label: "Everything" },
  { value: "warning", label: "Warnings and errors" },
  { value: "error", label: "Errors" },
];

export function LogsDialog({ onClose }: { onClose: () => void }) {
  const [level, setLevel] = useState("");
  const [lines, setLines] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [follow, setFollow] = useState(true);
  const [probes, setProbes] = useState(true);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let since = 0;
    let live = true;
    setLines([]);
    const poll = async () => {
      try {
        const r: any = await get(`logs?since=${since}${level ? `&level=${level}` : ""}${probes ? "" : "&probes=false"}`);
        if (!live) return;
        since = r.next;
        if (r.lines.length) setLines((l) => [...l, ...r.lines].slice(-3000));
        setError(null);
      } catch (e: any) {
        if (live) setError(e.message);
      }
      if (live) timer = setTimeout(poll, 2000);
    };
    let timer = setTimeout(poll, 0);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [level, probes]);

  const shown = filter ? lines.filter((l) => `${l.logger} ${l.message}`.toLowerCase().includes(filter.toLowerCase())) : lines;
  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [shown.length, follow]);
  // following the end while it is in view; scrolling up stops it, back to the end resumes it
  const scrolled = () => {
    const el = box.current;
    if (el) setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 30);
  };

  return (
    <Dialog title="Sequencer log" onClose={onClose} wide>
      <div className="logs-bar">
        <select value={level} onChange={(e) => setLevel(e.target.value)} title="The least severe lines shown">
          {LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
        </select>
        <label className="small" title="The state probes of the lab components: each command, its result, its errors"><input type="checkbox" checked={probes} onChange={(e) => setProbes(e.target.checked)} /> probes</label>
        <input type="search" placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <span className="muted small">{shown.length} line{shown.length === 1 ? "" : "s"} · the last 2000 events and 1000 probes of this server since it started</span>
      </div>
      {error && <div className="error-box">{error}</div>}
      <div className="log sequencer-log" ref={box} onScroll={scrolled}>
        {shown.length === 0 && <span className="muted">No line{level ? " at this level" : ""} yet.</span>}
        {shown.map((l) => (
          <div key={l.n} className={`log-line ${l.level === "ERROR" || l.level === "CRITICAL" ? "log-error" : l.level === "WARNING" ? "log-warn" : l.probe ? "log-probe" : ""}`}>
            <span className="log-time" title={l.time}>{new Date(l.time).toLocaleTimeString()}</span>
            <span className="log-level">{l.probe ? (l.level === "DEBUG" ? "probe" : `probe ${l.level.toLowerCase()}`) : l.level}</span>
            <span className="log-message">{l.message}</span>
          </div>
        ))}
      </div>
    </Dialog>
  );
}
