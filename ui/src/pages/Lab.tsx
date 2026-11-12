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

// Components and stages (sequencer README, section 13.1): a diagram of the lab (stage areas, components and their
// links, live states and runs), and a table; monitored state, lock state and holder, self-test results; hold,
// release, maintenance, resume and self-test.
import { useEffect, useState } from "react";
import { get, post, useLabels, useResource } from "../api";
import { Dialog } from "../fields";
import { handKeys, KeysByHand, type HandKey } from "../keysbyhand";
import { Address, ago, attempt, Badge, classes, navigate, setQuery, time, useRoute } from "../util";
import { ComponentDialog, LabGraph, LinksDialog, Rendered, StageDialog, type LabLink, type LabView } from "./LabGraph";
import { LogsDialog } from "./Logs";

const ACTION_HELP: Record<string, string> = {
  hold: "Keep it out of new runs (a run holding it goes on). Resume to give it back.",
  maintenance: "Out of scheduling for maintenance, whatever its probed state, until resumed.",
  resume: "Back in scheduling (clears hold and maintenance).",
  release: "Free its locks now, held by a run that is gone (after a crash). A live run keeps running.",
  selftest: "Run the platform self-test now: reachability, remote root, disk, clock and the type's own checks.",
  commands: "Run one of its commands by hand (start, stop, a check…): only in maintenance, while no run holds it.",
};

function Actions({ kind, item, onDone, onSelftest, onCommands }: { kind: "stages" | "components"; item: any; onDone: () => void; onSelftest: (name: string) => void; onCommands?: () => void }) {
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
      {onCommands && <button className="small" title={ACTION_HELP.commands} onClick={onCommands}>Commands…</button>}
    </div>
  );
}

const COMMANDS_SLOW = 4000;          // ms: past this, "Reading its commands…" says it still waits
const COMMANDS_GIVE_UP = 30000;      // ms: past this, it gives up, with a Retry button

/** The commands of a component in maintenance, each run by hand, with what it did (README section 7.6). */
function CommandsDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const labelOf = useLabels();
  const [info, setInfo] = useState<any>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [results, setResults] = useState<any[]>([]);
  // keys set by hand for the commands run from this dialog: the commands are shown as they will run with them
  const [rows, setRows] = useState<HandKey[]>([]);
  const [keysError, setKeysError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);                  // Retry: read them again
  const [slow, setSlow] = useState(false);                  // still no answer after a few seconds
  const keys = handKeys(rows);
  const keysText = JSON.stringify(keys || {});
  useEffect(() => {
    let current = true;
    let giveUp: ReturnType<typeof setTimeout> | undefined;
    // the sequencer answers within a few seconds, even for a component that does not answer: past
    // COMMANDS_GIVE_UP, the dialog says so (with Retry) instead of reading forever
    const read = () => Promise.race([
      keys ? post(`components/${id}/commands`, { keys }) : get(`components/${id}/commands`),
      new Promise((_, reject) => { giveUp = setTimeout(() => reject(new Error(`the sequencer did not answer within ${COMMANDS_GIVE_UP / 1000} s`)), COMMANDS_GIVE_UP); }),
    ])
      .then((r) => { if (current) { setInfo(r); setKeysError(null); } })
      .catch((e) => { if (current) setKeysError(e.message); })
      .finally(() => { clearTimeout(giveUp); if (current) setSlow(false); });
    setSlow(false);
    const slowTimer = setTimeout(() => { if (current) setSlow(true); }, COMMANDS_SLOW);
    const timer = setTimeout(read, keys ? 300 : 0);          // while typing: once it pauses
    return () => { current = false; clearTimeout(timer); clearTimeout(slowTimer); clearTimeout(giveUp); };
  }, [id, keysText, reload]);
  const retry = () => { setKeysError(null); setReload((n) => n + 1); };
  const run = async (name: string) => {
    setRunning(name);
    const r = await attempt(() => post(`components/${id}/commands/${name}`, keys ? { keys } : {}), `${id}: ${name}`);
    setRunning(null);
    if (r) setResults((all) => [r, ...all]);
  };
  const statusOf = (key: string) => {
    const known = (info?.keys || []).find((k: any) => k.key === key);
    return known ? { overrides: known.overrides, was: known.was } : undefined;
  };
  // proposed for a new row: the keys the commands read, the undefined ones first
  const read: string[] = info ? [...new Set<string>(info.commands.flatMap((c: any) => (c.segments || []).map((seg: any) => seg.key).filter((k: any) => k && /^[A-Za-z_]\w*(\.\w+)*$/.test(k))))] : [];
  const blocked = info && (!info.maintenance ? "not in maintenance" : info.held_by?.length ? `held by ${info.held_by.join(", ")}` : null);
  return (
    <Dialog title={`Commands of ${labelOf(id)}`} onClose={onClose} wide>
      <p className="muted small">In maintenance, its commands run by hand with the keys of the lab (no datapack: no params), and with the keys set by hand below. A detached command keeps running; its signal commands, or kill, stop what was started by hand.</p>
      {!info ? (keysError
        ? <div className="error-box">Cannot read its commands: {keysError}. <button className="small" onClick={retry}>Retry</button></div>
        : <p className="muted">Reading its commands…{slow && <> still waiting for the sequencer (is it busy, or restarting?)</>}</p>) : (
        <>
          {blocked && <div className="error-box">Cannot run its commands: {blocked}.</div>}
          {info.root_note && <div className="warn-text small">{info.root_note}.</div>}
          <details className="kbh-box" open={rows.length > 0 || info.missing?.length > 0}>
            <summary>Keys set by hand{rows.length > 0 && <> ({rows.length})</>}{info.missing?.length > 0 && <span className="warn-text"> · {info.missing.length} undefined key{info.missing.length > 1 ? "s" : ""} read by its commands</span>}</summary>
            <KeysByHand rows={rows} onChange={setRows} status={statusOf} missing={info.missing}
              known={[...(info.missing || []), ...read.filter((k) => !(info.missing || []).includes(k))].map((k) => ({ value: k }))}
              setNote="set for the commands run from here." overrideNote="the lab's value is replaced for the commands run from here (recorded with each run)." />
            {keysError && <div className="error-box">{keysError}</div>}
          </details>
          <table className="grid compact">
            <tbody>
              {info.commands.map((c: any) => (
                <tr key={c.name}>
                  <td><code>{c.name}</code></td>
                  <td className="small mono">{c.segments ? <Rendered segments={c.segments} /> : c.spec.run ?? (c.spec.signal ? `signal ${c.spec.signal}` : c.spec.http ? (typeof c.spec.http === "string" ? `GET ${c.spec.http}` : `${String(c.spec.http.method || "GET").toUpperCase()} ${c.spec.http.url}`) : c.kind)}{c.spec.via && <span className="muted"> (through {c.spec.via})</span>}{c.spec.detach && <span className="muted"> (detached)</span>}</td>
                  <td>{c.manual
                    ? <button className="small" disabled={!!blocked || !!running} onClick={() => run(c.name)}>{running === c.name ? "Running…" : "Run"}</button>
                    : <span className="muted small" style={{ whiteSpace: "nowrap" }} title="deploy, collect, wait_for and python commands need a datapack">needs a run</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {results.map((r, i) => (
            <div key={i} className="manual-result">
              <div><Badge value={r.ok ? "OK" : "FAILED"} /> <code>{r.command}</code> on {r.on || labelOf(id)}{r.via && <> (through {r.via})</>}, {time(r.at)}, {r.duration} s{r.exit !== undefined && r.exit !== null && <>, exit status <code>{r.exit}</code></>}</div>
              {r.keys?.length > 0 && <div className="small muted">keys set by hand: {r.keys.map((k: any, j: number) => <span key={k.key}>{j > 0 && ", "}<code>{k.key}={typeof k.value === "string" ? k.value : JSON.stringify(k.value)}</code>{k.overrides && <span className="warn-text"> (overrides <code>{typeof k.was === "string" ? k.was : JSON.stringify(k.was)}</code>)</span>}</span>)}</div>}
              {r.run && <pre className="small">$ {r.run}</pre>}
              {(r.output || r.error || r.detail) && <pre className="small">{[r.output, r.error && `error: ${r.error}`, r.detail].filter(Boolean).join("\n")}</pre>}
            </div>
          ))}
        </>
      )}
    </Dialog>
  );
}

function Holders({ item }: { item: any }) {
  if (!item.holders?.length) return <span className="muted">free</span>;
  return <>{item.holders.map((h: any) => <a key={h.run} href={`#/runs/${h.run}`} className="holder">{h.run} ({h.mode})</a>)}</>;
}

function SelftestDialog({ target, onClose }: { target: string | null; onClose: () => void }) {
  const labelOf = useLabels();
  const [results, setResults] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    post<any[]>("selftest", target ? { target } : {}).then(setResults).catch((e) => setError(e.message));
  }, [target]);
  return (
    <Dialog title={`Self-test of ${labelOf(target) || "the whole lab"}`} onClose={onClose} wide>
      {error && <div className="error-box">{error}</div>}
      {!results && !error && <p className="muted">Running the checks…</p>}
      {results?.map((r) => (
        <div key={r.component} className="selftest">
          <h4 title={r.component}>{labelOf(r.component)} <Badge value={r.ok ? "passed" : "failed"} /> {r.state && <Badge value={r.state} />}</h4>
          <table className="grid compact">
            <tbody>
              {(r.checks || []).map((c: any) => (
                <tr key={c.id}><td><code>{c.id}</code></td><td><Badge value={c.skipped ? "not run" : c.ok ? "OK" : "FAILED"} /></td><td className="reason">{c.detail}</td></tr>
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
function ProbeStep({ step, index, running }: { step: any; index: number; running?: boolean }) {
  const answer = [step.output, step.error].filter((x) => x && String(x).trim()).join("\n");
  return (
    <div className={classes("probe-step", running && "running")}>
      <div><span className="muted">{index + 1}.</span> {step.action}{step.ok !== undefined && <> <Badge value={step.ok ? "OK" : "FAILED"} /></>}
        {running && <span className="probe-running"><span className="pulse run" /> running…</span>}</div>
      {step.command && <pre className="probe-command">{step.command}</pre>}
      {!running && (answer || step.exit !== undefined) && (
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
            <td><Badge value={c.skipped ? "not run" : c.ok ? "OK" : "FAILED"} /></td>
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
  const labelOf = useLabels();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  useEffect(() => {
    get(`components/${id}/probe`).then(setData).catch((e) => setError(e.message));
  }, [id]);
  const again = async () => {
    setProbing(true);
    setData((d: any) => d && { ...d, probe: { running: true, steps: [] } });       // the last report is gone at once
    const fresh = await attempt(() => post(`components/${id}/probe`));
    setProbing(false);
    if (fresh) setData(fresh);
    else get(`components/${id}/probe`).then(setData).catch(() => undefined);
  };
  const p = data?.probe;
  const live = probing || !!p?.running;                  // a probe goes on (this one, or the sequencer's own)
  // while a probe runs, its report is read again and again: each step shows as it starts, then its answer
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => {
      get(`components/${id}/probe`).then((d) => setData((current: any) => (probing && !d.probe?.running ? current : d))).catch(() => undefined);
    }, 300);
    return () => window.clearInterval(timer);
  }, [live, probing, id]);
  const failure = !live && (p?.kept_broken || data?.selftest_failed);
  const selftest = data?.selftest;
  return (
    <Dialog title={`State of ${labelOf(id)}`} onClose={onClose} wide
      footer={<button disabled={probing} onClick={again}>{probing ? "Probing…" : "Probe again"}</button>}>
      {error && <div className="error-box">{error}</div>}
      {!data && !error && <p className="muted">Reading its last probe…</p>}
      {data && (
        <div className="probe-report">
          <p>
            {live ? <><span className="pulse run" /> <strong>Probing…</strong> <span className="muted">last known state <Badge value={data.state} /></span></>
              : <><Badge value={data.state} /> <span className="muted">{STATE_HELP[data.state] || ""}</span>
                {data.expected && data.expected !== data.state && <> · <span className="warning-text">expected {data.expected}</span></>}</>}
          </p>
          {!live && p?.why && <p className="probe-why"><strong>Why:</strong> {p.why}.</p>}
          {!p && <p className="muted">Not probed since tsqd started{data.detail ? <>: last recorded detail <code>{data.detail}</code></> : ""}. Probe it again to see what is tested.</p>}
          {p && (
            <>
              <h4>{live ? "What is being tested" : "What was tested"}</h4>
              {p.host && (
                <p className="muted small">
                  {p.kind ? <>status probe <code>{p.kind}</code></> : live ? "status probe" : "no status probe"}
                  {p.chain?.length > 1 && <> of the lab component <code>{p.chain[p.chain.length - 2]}</code></>}
                  {p.via
                    ? <>{" "}for <code>{p.host}</code>, run on <code>{p.via.id}</code> ({p.via.host}, {p.transport}) which it is reached through</>
                    : <>{" "}on <code>{p.host}</code> ({p.transport})</>}
                  {!live && <>, {time(p.at)}, {p.duration} s</>}
                </p>
              )}
              {(p.steps || []).map((st: any, i: number) => <ProbeStep key={i} step={st} index={i} running={live && i === p.steps.length - 1} />)}
              {live && !(p.steps || []).length && <p className="muted small"><span className="pulse run" /> starting…</p>}
              {!live && p.rules && Object.keys(p.rules).length > 0 && (
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
          {!live && !failure && selftest && (
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
  const route = useRoute();
  const tab = route.query.get("view") === "table" ? "table" : "diagram";
  const focus = route.query.get("focus");
  const labelOf = useLabels();
  const lab = useResource<any>("lab", ["lab"]);
  const view = useResource<LabView>("lab/view", ["lab", "run", "step", "resource", "release"]);
  const [picked, setPicked] = useState<{ kind: "component" | "stage"; id: string } | { kind: "links"; links: LabLink[] } | null>(null);
  const comps = useResource<any[]>("components", ["component", "probe", "probing", "resource", "release", "run", "lab", "selftest"]);
  const stages = useResource<any[]>("stages", ["resource", "release", "run", "lab"]);
  const [selftest, setSelftest] = useState<string | null | undefined>(undefined);
  const [reloading, setReloading] = useState(false);
  const [logs, setLogs] = useState(false);
  const [probing, setProbing] = useState(false);
  const [details, setDetails] = useState<string | null>(null);
  const [commands, setCommands] = useState<string | null>(null);
  const stageById: Record<string, any> = Object.fromEntries((stages.data || []).map((s) => [s.id, s]));
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
    view.refresh();
  };
  useEffect(() => {                          // "Show in the table": scrolled to, and marked a moment
    if (tab !== "table" || !focus || !comps.data) return;
    const row = document.getElementById(`lab-row-${focus}`);
    row?.scrollIntoView({ block: "center", behavior: "smooth" });
    const t = window.setTimeout(() => setQuery("focus", null), 2500);
    return () => window.clearTimeout(t);
  }, [tab, focus, comps.data]);
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
  const editLab = async (host?: string) => {
    const s = await attempt(() => post("edit/sessions", { repo: "lab", local: true }));
    if (!s) return;
    const ctx = host ? await get<any>(`edit/sessions/${s.id}/context`).catch(() => null) : null;
    navigate(`edit/${s.id}`, ctx?.inventory ? { file: ctx.inventory, item: `host:${host}` } : {});
  };
  const commandsOf = (c: any) => c.maintenance || (c.stages || []).some((st: string) => stageById[st]?.maintenance) ? () => setCommands(c.id) : undefined;
  const git = lab.data?.git;
  const row = (c: any) => (
    <tr key={c.id} id={`lab-row-${c.id}`} className={classes((c.hold || c.maintenance) && "off", focus === c.id && "focused")}>
      <td><strong title={c.id}>{c.label || c.id}</strong><div className="muted small">{c.label && c.label !== c.id ? c.id : ""}</div>{c.description && <div className="small description">{c.description}</div>}</td>
      <td>{c.role}</td>
      <td><code>{c.extends}</code></td>
      <td><Address host={c.host} alias={c.host_alias} /></td>
      <td><button className="link state-button" onClick={() => setDetails(c.id)} title={`${c.detail ? c.detail + "\n\n" : ""}What was tested, the answer, and why this state`}><Badge value={c.state} /></button>{c.expected && c.expected !== c.state && <span className="muted small"> expected {c.expected}</span>}
        <div className="muted small" title={time(c.probed_at)}>{c.probing ? <><span className="pulse run" /> probing…</> : c.probed_at ? `probed ${ago(c.probed_at)}` : ""}</div></td>
      <td>{c.lock}{c.hold && <Badge value="hold" title={c.note || ""} />}{c.maintenance && <Badge value="maintenance" title={c.note || ""} />}</td>
      <td><Holders item={c} /></td>
      <td><Actions kind="components" item={c} onDone={refresh} onSelftest={setSelftest} onCommands={commandsOf(c)} /></td>
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
        {editor && <button onClick={() => editLab()} title="Open the lab inventory, roles and components in the Editor, in place">Edit lab config</button>}
        <button onClick={() => setLogs(true)} title="The events of the sequencer, its warnings and errors">Logs</button>
      </div>
      {logs && <LogsDialog onClose={() => setLogs(false)} />}
      {lab.data?.description && <p className="description lab-description">{lab.data.description}</p>}
      <div className="tabs">
        <button className={tab === "diagram" ? "selected" : ""} onClick={() => setQuery("view", null)} title="Stages, components and their links, with what runs on them now">Diagram</button>
        <button className={tab === "table" ? "selected" : ""} onClick={() => setQuery("view", "table")} title="Every stage and component, one row each, with its actions">Table</button>
      </div>
      {tab === "diagram" && (comps.data && stages.data
        ? <LabGraph comps={comps.data} stages={stages.data} view={view.data}
          onComponent={(id) => setPicked({ kind: "component", id })} onProbe={setDetails} onStage={(id) => setPicked({ kind: "stage", id })}
          onLinks={(links) => setPicked({ kind: "links", links })} />
        : <p className="muted">Reading the lab…</p>)}
      {view.error && tab === "diagram" && <div className="error-box">{view.error}</div>}
      {picked?.kind === "component" && byId[picked.id] && (
        <ComponentDialog comp={byId[picked.id]} view={view.data} labelOf={labelOf} onClose={() => setPicked(null)}
          onProbe={() => setDetails(picked.id)} onComponent={(id) => setPicked({ kind: "component", id })}
          onTable={() => { setPicked(null); setQuery("focus", picked.id); setQuery("view", "table"); }}
          onEdit={editor ? () => editLab(picked.id) : undefined}
          actions={<Actions kind="components" item={byId[picked.id]} onDone={refresh} onSelftest={setSelftest} onCommands={commandsOf(byId[picked.id])} />} />
      )}
      {picked?.kind === "stage" && stageById[picked.id] && (
        <StageDialog stage={stageById[picked.id]} view={view.data} labelOf={labelOf} onClose={() => setPicked(null)}
          onComponent={(id) => setPicked({ kind: "component", id })}
          actions={<Actions kind="stages" item={stageById[picked.id]} onDone={refresh} onSelftest={setSelftest} />} />
      )}
      {picked?.kind === "links" && (
        <LinksDialog links={picked.links} labelOf={labelOf} onClose={() => setPicked(null)} onComponent={(id) => setPicked({ kind: "component", id })} />
      )}
      {tab === "table" && (stages.data || []).map((s) => (
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
          {s.description && <p className="description">{s.description}</p>}
          <table className="grid">{head}<tbody>{s.components.map((id: string) => byId[id] && row(byId[id]))}</tbody></table>
        </div>
      ))}
      {tab === "table" && <div className="card">
        <div className="card-head"><h3>Ad hoc components</h3><span className="muted small">in no stage, bound by role on their own</span></div>
        <table className="grid">{head}<tbody>{(comps.data || []).filter((c) => c.adhoc).map(row)}</tbody></table>
      </div>}
      {details && <ProbeDialog id={details} onClose={() => {
        setDetails(null);
        refresh();
      }} />}
      {commands && <CommandsDialog id={commands} onClose={() => setCommands(null)} />}
      {selftest !== undefined && <SelftestDialog target={selftest} onClose={() => {
        setSelftest(undefined);
        refresh();
      }} />}
    </div>
  );
}
