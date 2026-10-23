// Queue and running datapacks (sequencer README, section 13.1): post a datapack, change priorities,
// see what each queued run waits for, cancel or kill.
import { useState } from "react";
import { post, send, useResource } from "../api";
import { Dialog, Field } from "../fields";
import { ago, attempt, Badge, duration, navigate } from "../util";

export const ACTIVE = ["reserved", "selftest", "running", "cleaning"];

export function PriorityInput({ run, onDone }: { run: any; onDone?: () => void }) {
  const [value, setValue] = useState<string>(String(run.priority));
  const save = async () => {
    const n = Number(value);
    if (n === run.priority || Number.isNaN(n)) return;
    await attempt(() => send("PATCH", `runs/${run.id}`, { priority: n }), `priority of ${run.id}: ${n}`);
    onDone?.();
  };
  return (
    <input className="priority" type="number" min={0} max={100} value={value} title="0 to 100: higher runs first. Enter to apply."
      onChange={(e) => setValue(e.target.value)} onBlur={save} onKeyDown={(e) => e.key === "Enter" && save()} />
  );
}

export async function abort(run: any, mode: "cancel" | "kill") {
  const text = mode === "cancel"
    ? `Cancel run ${run.id}? Running steps are stopped (stop command), then the finally steps run and the outputs are collected.`
    : `Kill run ${run.id}? Every process it started is killed at once (kill command); the finally steps still run.`;
  if (!window.confirm(text)) return;
  await attempt(() => post(`runs/${run.id}/${mode}`), `${mode} requested for ${run.id}`);
}

function PostDialog({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [baseline, setBaseline] = useState<File | null>(null);
  const [priority, setPriority] = useState("");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!file) return;
    const form = new FormData();
    form.append("datapack", file);
    if (priority) form.append("priority", priority);
    if (baseline) form.append("baseline", baseline);
    setBusy(true);
    setError(null);
    try {
      setResult(await send("POST", "runs", form));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title="Post a datapack" onClose={onClose} footer={
      result ? <button className="primary" onClick={() => navigate(`runs/${result.id}`)}>Follow the run</button>
        : <button className="primary" disabled={!file || busy} onClick={submit}>{busy ? "Checking…" : "Post to the queue"}</button>
    }>
      {!result && (
        <>
          <Field label="Datapack" help="The zip made by the datapack tool (`datapack <testdef.yml>`), or by **Generate** in the Editor. It is verified (bag checksums), unpacked and checked against the lab before it is queued.">
            <input type="file" accept=".zip" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </Field>
          <Field label="Priority" help="0 to 100, default from the configuration (`queue.default_priority`). Higher runs first; equal priorities run in submission order. It can be changed while the run waits.">
            <input type="number" min={0} max={100} value={priority} placeholder="default" onChange={(e) => setPriority(e.target.value)} />
          </Field>
          <Field label="Expected baseline" help="Optional: the campaign's expected baseline (YAML). The datapack is checked against it: a mismatch does not refuse the run, it makes it non-conform.">
            <input type="file" accept=".yml,.yaml" onChange={(e) => setBaseline(e.target.files?.[0] || null)} />
          </Field>
          {error && <pre className="error-box">{error}</pre>}
        </>
      )}
      {result && (
        <div className="post-result">
          <p>
            Queued as <strong>{result.id}</strong> with priority {result.priority}. <Badge value={result.conform ? "conform" : "non-conform"} />
          </p>
          {(result.nonconform || []).length > 0 && (
            <ul className="warn-list">{result.nonconform.map((n: string) => <li key={n}>{n}</li>)}</ul>
          )}
          <p className="muted">{result.name}</p>
        </div>
      )}
    </Dialog>
  );
}

export function QueuePage() {
  const runs = useResource<any[]>("runs?limit=200&state=" + ["queued", "waiting_resources", ...ACTIVE].join(","), ["run", "phase", "release"]);
  const [posting, setPosting] = useState(false);
  const all = runs.data || [];
  const active = all.filter((r) => ACTIVE.includes(r.state));
  const queued = all.filter((r) => !ACTIVE.includes(r.state)).sort((a, b) => b.priority - a.priority || a.submitted_at.localeCompare(b.submitted_at));
  return (
    <div className="page">
      <div className="page-head">
        <h2>Queue</h2>
        <button className="primary" onClick={() => setPosting(true)}>Post a datapack</button>
      </div>
      {runs.error && <div className="error-box">{runs.error}</div>}
      <h3>Running ({active.length})</h3>
      {active.length === 0 && <p className="muted">Nothing is running.</p>}
      <div className="cards">
        {active.map((r) => <ActiveCard key={r.id} run={r} />)}
      </div>
      <h3>Waiting ({queued.length})</h3>
      {queued.length === 0 ? <p className="muted">The queue is empty. Post a datapack, or generate one from the Editor.</p> : (
        <table className="grid">
          <thead>
            <tr><th>#</th><th>Run</th><th>Test</th><th title="0 to 100, higher first">Priority</th><th>Waiting</th><th>Waits for</th><th>Conform</th><th /></tr>
          </thead>
          <tbody>
            {queued.map((r, i) => (
              <tr key={r.id}>
                <td>{i + 1}</td>
                <td><a href={`#/runs/${r.id}`}>{r.id}</a></td>
                <td title={r.name}>{r.testdef}</td>
                <td><PriorityInput run={r} onDone={runs.refresh} /></td>
                <td>{ago(r.submitted_at)}</td>
                <td className="waits">{r.waiting_for || (r.state === "queued" ? "its turn" : "")}</td>
                <td><Badge value={r.conform ? "conform" : "non-conform"} title={(r.nonconform || []).join("\n")} /></td>
                <td><button className="small" onClick={() => abort(r, "cancel").then(runs.refresh)}>Cancel</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {posting && <PostDialog onClose={() => {
        setPosting(false);
        runs.refresh();
      }} />}
    </div>
  );
}

function ActiveCard({ run }: { run: any }) {
  const detail = useResource<any>(`runs/${run.id}`, ["step", "phase", "run"], (d) => d.run === run.id);
  const steps: any[] = detail.data?.steps || [];
  const done = steps.filter((s) => ["succeeded", "failed", "error", "skipped"].includes(s.state)).length;
  const runningSteps = steps.filter((s) => s.state === "running");
  return (
    <div className="card run-card">
      <div className="card-head">
        <a href={`#/runs/${run.id}`}><strong>{run.testdef}</strong></a>
        <Badge value={run.state} />
      </div>
      <div className="muted small">{run.id} · priority {run.priority} · started {ago(run.activated_at)} · {duration(run.activated_at)}</div>
      {run.binding && <div className="small">on {Object.values(run.binding.stages || {}).join(", ")} {Object.values(run.binding.components || {}).length} components</div>}
      {steps.length > 0 && (
        <div className="progress" title={`${done} of ${steps.length} steps ended`}>
          <div style={{ width: `${(100 * done) / steps.length}%` }} />
        </div>
      )}
      {runningSteps.length > 0 && <div className="small">now: {runningSteps.map((s) => `${s.phase}:${s.id}${s.component ? `[${s.component}]` : ""}`).join(", ")}</div>}
      <div className="card-actions">
        <button className="small" onClick={() => abort(run, "cancel")}>Cancel</button>
        <button className="small danger" onClick={() => abort(run, "kill")}>Kill</button>
      </div>
    </div>
  );
}

