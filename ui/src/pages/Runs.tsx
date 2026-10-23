// History of runs, filtered by state.
import { useState } from "react";
import { useResource } from "../api";
import { ago, Badge, duration, time } from "../util";

const FILTERS: [string, string][] = [
  ["", "all"], ["queued,waiting_resources", "waiting"], ["reserved,selftest,running,cleaning", "running"],
  ["done", "done"], ["cancelled", "cancelled"],
];

export function RunsPage() {
  const [state, setState] = useState("");
  const [text, setText] = useState("");
  const runs = useResource<any[]>(`runs?limit=500${state ? `&state=${state}` : ""}`, ["run"]);
  const list = (runs.data || []).filter((r) => !text || `${r.id} ${r.name} ${r.testdef}`.toLowerCase().includes(text.toLowerCase()));
  return (
    <div className="page">
      <div className="page-head">
        <h2>Runs</h2>
        <div className="segmented">
          {FILTERS.map(([value, label]) => (
            <button key={label} className={state === value ? "selected" : ""} onClick={() => setState(value)}>{label}</button>
          ))}
        </div>
        <input className="search" placeholder="filter by test or id…" value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      {runs.error && <div className="error-box">{runs.error}</div>}
      <table className="grid">
        <thead>
          <tr><th>Run</th><th>Test</th><th>State</th><th>Verdict</th><th>Submitted</th><th>Duration</th><th>Conform</th><th>Reason</th></tr>
        </thead>
        <tbody>
          {list.map((r) => (
            <tr key={r.id}>
              <td><a href={`#/runs/${r.id}`}>{r.id}</a></td>
              <td title={r.name}>{r.testdef}</td>
              <td><Badge value={r.state} /></td>
              <td><Badge value={r.verdict} /></td>
              <td title={time(r.submitted_at)}>{ago(r.submitted_at)}</td>
              <td>{r.activated_at ? duration(r.activated_at, r.ended_at) : ""}</td>
              <td><Badge value={r.conform ? "conform" : "non-conform"} title={(r.nonconform || []).join("\n")} /></td>
              <td className="reason">{r.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {list.length === 0 && !runs.loading && <p className="muted">No run.</p>}
    </div>
  );
}
