// Components and stages (sequencer README, section 13.1): monitored state, lock state and holder,
// self-test results; hold, release, maintenance, resume and self-test.
import { useEffect, useState } from "react";
import { post, useResource } from "../api";
import { Dialog } from "../fields";
import { ago, attempt, Badge, navigate, time } from "../util";
import { LogsDialog } from "./Logs";

const ACTION_HELP: Record<string, string> = {
  hold: "Keep it out of new runs (a run holding it goes on). Resume to give it back.",
  maintenance: "Out of scheduling for maintenance, whatever its probed state, until resumed.",
  resume: "Back in scheduling (clears hold and maintenance).",
  release: "Free its locks now, held by a run that is gone (after a crash). A live run keeps running.",
  selftest: "Run the platform self-test now: reachability, remote root, disk, clock and the type's own checks.",
};

function Actions({ kind, item, onDone, onSelftest }: { kind: "stages" | "components"; item: any; onDone: () => void; onSelftest: (name: string) => void }) {
  const act = async (action: string) => {
    let note = "";
    if (action === "hold" || action === "maintenance") {
      const answer = window.prompt(`Note for ${action} of ${item.id} (optional, shown to others):`, item.note || "");
      if (answer === null) return;
      note = answer;
    }
    if (action === "release" && !window.confirm(`Release the locks on ${item.id}? Only for locks left by a run that is gone.`)) return;
    await attempt(() => post(`${kind}/${item.id}/${action}`, { note }), `${item.id}: ${action}`);
    onDone();
  };
  const off = item.hold || item.maintenance;
  return (
    <div className="row-actions">
      {!item.hold && <button className="small" title={ACTION_HELP.hold} onClick={() => act("hold")}>Hold</button>}
      {!item.maintenance && <button className="small" title={ACTION_HELP.maintenance} onClick={() => act("maintenance")}>Maintenance</button>}
      {off && <button className="small primary" title={ACTION_HELP.resume} onClick={() => act("resume")}>Resume</button>}
      {item.holders?.length > 0 && <button className="small" title={ACTION_HELP.release} onClick={() => act("release")}>Release</button>}
      <button className="small" title={ACTION_HELP.selftest} onClick={() => onSelftest(item.id)}>Self-test</button>
    </div>
  );
}

function Holders({ item }: { item: any }) {
  if (!item.holders?.length) return <span className="muted">free</span>;
  return <>{item.holders.map((h: any) => <a key={h.run} href={`#/runs/${h.run}`} className="holder">{h.run} ({h.mode})</a>)}</>;
}

function SelftestDialog({ target, onClose }: { target: string | null; onClose: () => void }) {
  const [results, setResults] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    post<any[]>("selftest", target ? { target } : {}).then(setResults).catch((e) => setError(e.message));
  }, [target]);
  return (
    <Dialog title={`Self-test of ${target || "the whole lab"}`} onClose={onClose} wide>
      {error && <div className="error-box">{error}</div>}
      {!results && !error && <p className="muted">Running the checks…</p>}
      {results?.map((r) => (
        <div key={r.component} className="selftest">
          <h4>{r.component} <Badge value={r.ok ? "passed" : "failed"} /> {r.state && <Badge value={r.state} />}</h4>
          <table className="grid compact">
            <tbody>
              {(r.checks || []).map((c: any) => (
                <tr key={c.id}><td><code>{c.id}</code></td><td><Badge value={c.ok ? "passed" : "failed"} /></td><td className="reason">{c.detail}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </Dialog>
  );
}

export function LabPage({ editor }: { editor?: boolean }) {
  const lab = useResource<any>("lab", ["lab"]);
  const comps = useResource<any[]>("components", ["component", "probe", "resource", "release", "run", "lab", "selftest"]);
  const stages = useResource<any[]>("stages", ["resource", "release", "run", "lab"]);
  const [selftest, setSelftest] = useState<string | null | undefined>(undefined);
  const [reloading, setReloading] = useState(false);
  const [logs, setLogs] = useState(false);
  const [probing, setProbing] = useState(false);
  // "probed 3s ago" kept true: redrawn every second, the components read again after each probe (events),
  // and every component probed when the page opens
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const probeNow = async () => {
    setProbing(true);
    await attempt(() => post("lab/probe"));
    setProbing(false);
  };
  useEffect(() => {
    post("lab/probe").catch(() => undefined);
  }, []);
  const refresh = () => {
    comps.refresh();
    stages.refresh();
  };
  const byId: Record<string, any> = Object.fromEntries((comps.data || []).map((c) => [c.id, c]));
  const reload = async (pull: boolean) => {
    const revision = pull ? window.prompt("Revision to check out (tag or commit; empty: the latest commit of the branch):", "") : null;
    if (pull && revision === null) return;
    setReloading(true);
    await attempt(() => post("lab/reload", { pull, revision: revision || null }), "lab reloaded");
    setReloading(false);
    lab.refresh();
    refresh();
  };
  const editLab = async () => {
    const s = await attempt(() => post("edit/sessions", { repo: "lab", local: true }));
    if (s) navigate(`edit/${s.id}`);
  };
  const git = lab.data?.git;
  const row = (c: any) => (
    <tr key={c.id} className={c.hold || c.maintenance ? "off" : ""}>
      <td><strong>{c.id}</strong><div className="muted small">{c.label !== c.id ? c.label : ""}</div></td>
      <td><code>{c.type}</code></td>
      <td>{c.role}</td>
      <td>{c.host}</td>
      <td><Badge value={c.state} title={c.detail || ""} />{c.expected && c.expected !== c.state && <span className="muted small"> expected {c.expected}</span>}
        <div className="muted small" title={time(c.probed_at)}>{c.probed_at ? `probed ${ago(c.probed_at)}` : ""}</div></td>
      <td>{c.lock}{c.hold && <Badge value="hold" title={c.note || ""} />}{c.maintenance && <Badge value="maintenance" title={c.note || ""} />}</td>
      <td><Holders item={c} /></td>
      <td><Actions kind="components" item={c} onDone={refresh} onSelftest={setSelftest} /></td>
    </tr>
  );
  const head = <thead><tr><th>Component</th><th>Type</th><th>Role</th><th>Host</th><th>State</th><th>Lock</th><th>Held by</th><th /></tr></thead>;
  return (
    <div className="page">
      <div className="page-head">
        <h2>Lab</h2>
        <span className="muted">{git ? <>commit <code>{git.commit?.slice(0, 10)}</code>{git.tag && <> tag <code>{git.tag}</code></>}{git.branch && <> on {git.branch}</>}{!git.clean && <Badge value="local changes" />}</> : "not a git work tree"}</span>
        <span className="muted small">loaded {ago(lab.data?.loaded_at)}</span>
        <span className="spacer" />
        <button disabled={reloading} onClick={() => reload(false)} title="Read the lab folder again as it is (tsq lab reload)">Reload</button>
        <button disabled={reloading} onClick={() => reload(true)} title="Fetch the lab repository and check out a revision first (tsq lab reload --pull)">Pull and reload…</button>
        <button disabled={probing} onClick={probeNow} title="Probe the state of every component now (they are also probed periodically, and when this page opens)">{probing ? "Probing…" : "Probe now"}</button>
        <button onClick={() => setSelftest(null)}>Self-test the lab</button>
        {editor && <button onClick={editLab} title="Open the lab inventory and component types in the Editor, in place">Edit lab config</button>}
        <button onClick={() => setLogs(true)} title="The events of the sequencer, its warnings and errors">Logs</button>
      </div>
      {logs && <LogsDialog onClose={() => setLogs(false)} />}
      {(stages.data || []).map((s) => (
        <div key={s.id} className="card stage">
          <div className="card-head">
            <h3>Stage {s.id}</h3>
            <span className="muted">type <code>{s.type}</code></span>
            {s.hold && <Badge value="hold" title={s.note || ""} />}
            {s.maintenance && <Badge value="maintenance" title={s.note || ""} />}
            <span className="small">held by <Holders item={s} /></span>
            <span className="spacer" />
            <Actions kind="stages" item={s} onDone={refresh} onSelftest={setSelftest} />
          </div>
          <table className="grid">{head}<tbody>{s.components.map((id: string) => byId[id] && row(byId[id]))}</tbody></table>
        </div>
      ))}
      <div className="card">
        <div className="card-head"><h3>Ad hoc components</h3><span className="muted small">in no stage, bound by role on their own</span></div>
        <table className="grid">{head}<tbody>{(comps.data || []).filter((c) => c.adhoc).map(row)}</tbody></table>
      </div>
      {selftest !== undefined && <SelftestDialog target={selftest} onClose={() => {
        setSelftest(undefined);
        refresh();
      }} />}
    </div>
  );
}
