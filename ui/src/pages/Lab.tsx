// Components and stages (sequencer README, section 13.1): monitored state, lock state and holder,
// self-test results; hold, release, maintenance, resume and self-test.
import { useEffect, useState } from "react";
import { get, post, useResource } from "../api";
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

const STATE_HELP: Record<string, string> = {
  idle: "it answers and runs nothing the sequencer started",
  configured: "deployed, and nothing runs yet",
  running: "something the sequencer started runs on it",
  offline: "the sequencer cannot reach it, or its probe says so",
  broken: "it answers, but its probe or its last self-test says it cannot be used",
  unknown: "its probe gives no state (no probe, or no rule matches its answer)",
};

/** One action of a probe: what was run, and what the component answered. */
function ProbeStep({ step, index }: { step: any; index: number }) {
  const answer = [step.output, step.error].filter((x) => x && String(x).trim()).join("\n");
  return (
    <div className="probe-step">
      <div><span className="muted">{index + 1}.</span> {step.action}</div>
      {step.command && <pre className="probe-command">{step.command}</pre>}
      {(answer || step.exit !== undefined) && (
        <div className="probe-answer">
          {step.exit !== undefined && step.exit !== null && <span className="muted small">exit status <code>{step.exit}</code></span>}
          {answer ? <pre className={step.error ? "bad-text" : ""}>{answer}</pre> : <span className="muted small"> no output</span>}
        </div>
      )}
    </div>
  );
}

function Checks({ checks }: { checks: any[] }) {
  return (
    <table className="grid compact">
      <thead><tr><th>Check</th><th>Result</th><th>What it tested</th><th>Answer</th></tr></thead>
      <tbody>
        {checks.map((c) => (
          <tr key={c.id}>
            <td><code>{c.id}</code></td>
            <td><Badge value={c.ok ? "passed" : "failed"} /></td>
            <td className="mono small">{c.command || c.spec?.run || (c.spec?.builtin ? `built-in ${c.spec.builtin}` : c.spec ? JSON.stringify(c.spec) : "")}</td>
            <td className="reason">{c.exit !== undefined && c.exit !== null && <span className="muted small">exit status <code>{c.exit}</code></span>}<pre>{c.detail}</pre></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Why a component has its state: what its last probe tested, what it answered, the rules of its probe and
 * the one that matched; and its last self-test, when it keeps the component broken. */
function ProbeDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  useEffect(() => {
    get(`components/${id}/probe`).then(setData).catch((e) => setError(e.message));
  }, [id]);
  const again = async () => {
    setProbing(true);
    const fresh = await attempt(() => post(`components/${id}/probe`));
    setProbing(false);
    if (fresh) setData(fresh);
  };
  const p = data?.probe;
  const failure = p?.kept_broken || data?.selftest_failed;
  const selftest = data?.selftest;
  return (
    <Dialog title={`State of ${id}`} onClose={onClose} wide
      footer={<button disabled={probing} onClick={again}>{probing ? "Probing…" : "Probe again"}</button>}>
      {error && <div className="error-box">{error}</div>}
      {!data && !error && <p className="muted">Reading its last probe…</p>}
      {data && (
        <div className="probe-report">
          <p>
            <Badge value={data.state} /> <span className="muted">{STATE_HELP[data.state] || ""}</span>
            {data.expected && data.expected !== data.state && <> · <span className="warning-text">expected {data.expected}</span></>}
          </p>
          {p?.why && <p className="probe-why"><strong>Why:</strong> {p.why}.</p>}
          {!p && <p className="muted">Not probed since tsqd started{data.detail ? <>: last recorded detail <code>{data.detail}</code></> : ""}. Probe it again to see what is tested.</p>}
          {p && (
            <>
              <h4>What was tested</h4>
              <p className="muted small">
                {p.kind ? <>status probe <code>{p.kind}</code></> : "no status probe"}
                {p.chain?.length > 1 && <> of the lab component <code>{p.chain[p.chain.length - 2]}</code></>}
                {p.via
                  ? <>{" "}for <code>{p.host}</code>, run on <code>{p.via.id}</code> ({p.via.host}, {p.transport}) which it is reached through</>
                  : <>{" "}on <code>{p.host}</code> ({p.transport})</>}
                , {time(p.at)}, {p.duration} s
              </p>
              {(p.steps || []).map((st: any, i: number) => <ProbeStep key={i} step={st} index={i} />)}
              {p.rules && Object.keys(p.rules).length > 0 && (
                <>
                  <h4>How its answer gives the state</h4>
                  <p className="muted small">The first rule that matches the answer gives the state; no match gives unknown.</p>
                  <table className="grid compact">
                    <thead><tr><th>State</th><th>Rule</th><th /></tr></thead>
                    <tbody>
                      {Object.entries<any>(p.rules).map(([state, rule]) => (
                        <tr key={state} className={p.matched === state ? "matched" : ""}>
                          <td><Badge value={state} /></td>
                          <td className="mono small">{Object.entries(rule).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ")}</td>
                          <td>{p.matched === state ? <strong>matched</strong> : <span className="muted">no match</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </>
          )}
          {failure && (
            <>
              <h4>Failed self-test ({time(failure.at)})</h4>
              <p className="muted small">It keeps the component broken whatever its probe says, until a self-test passes (one runs when it turns idle, or with Self-test).</p>
              {failure.checks ? <Checks checks={failure.checks} /> : <pre>{failure.detail}</pre>}
            </>
          )}
          {!failure && selftest && (
            <>
              <h4>Last self-test ({time(selftest.at)}) <Badge value={selftest.ok ? "passed" : "failed"} /></h4>
              <Checks checks={selftest.checks || []} />
            </>
          )}
        </div>
      )}
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
  const [details, setDetails] = useState<string | null>(null);
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
      <td>{c.role}</td>
      <td><code>{c.extends}</code></td>
      <td>{c.host}</td>
      <td><button className="link state-button" onClick={() => setDetails(c.id)} title={`${c.detail ? c.detail + "\n\n" : ""}What was tested, the answer, and why this state`}><Badge value={c.state} /></button>{c.expected && c.expected !== c.state && <span className="muted small"> expected {c.expected}</span>}
        <div className="muted small" title={time(c.probed_at)}>{c.probed_at ? `probed ${ago(c.probed_at)}` : ""}</div></td>
      <td>{c.lock}{c.hold && <Badge value="hold" title={c.note || ""} />}{c.maintenance && <Badge value="maintenance" title={c.note || ""} />}</td>
      <td><Holders item={c} /></td>
      <td><Actions kind="components" item={c} onDone={refresh} onSelftest={setSelftest} /></td>
    </tr>
  );
  const head = <thead><tr><th>Component</th><th>Role</th><th>Extends</th><th>Host</th><th>State</th><th>Lock</th><th>Held by</th><th /></tr></thead>;
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
        {editor && <button onClick={editLab} title="Open the lab inventory, roles and components in the Editor, in place">Edit lab config</button>}
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
      {details && <ProbeDialog id={details} onClose={() => {
        setDetails(null);
        refresh();
      }} />}
      {selftest !== undefined && <SelftestDialog target={selftest} onClose={() => {
        setSelftest(undefined);
        refresh();
      }} />}
    </div>
  );
}
