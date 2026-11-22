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

// Operator controls of a running run (sequencer SPEC section 9.7): step-by-step mode and breakpoints, the steps
// paused after they ran (Next, Run again, Continue anyway), the same actions on any step of the running phase, and
// the list of what operators did.
import { useState } from "react";
import { post } from "../api";
import { Dialog } from "../fields";
import { ago, attempt, Badge, classes, time } from "../util";

const AUTHOR = "tsq.author";
function operatorName(): string {
  try { return JSON.parse(localStorage.getItem(AUTHOR) || "{}").name || ""; } catch { return ""; }
}

export const ACTION_LABELS: Record<string, string> = {
  next: "Next after", run_again: "Run again", continue: "Continued anyway", redo_from: "Redone from an earlier step",
};

async function stepAction(runId: string, step: string, action: string, reason = "") {
  return attempt(() => post(`runs/${runId}/steps/${encodeURIComponent(step)}/${action}`, { reason, by: operatorName() }),
    action === "next" ? `${step}: goes on` : action === "run_again" ? `${step}: runs again` : `${step}: continued anyway`);
}

/** Step-by-step mode of a run not ended: off, after each step, or at breakpoints. */
export function StepModeControl({ run, onChanged }: { run: any; onChanged: () => void }) {
  const mode = run.control?.mode || "off";
  const set = (m: string) => attempt(() => post(`runs/${run.id}/control`, { mode: m }), m === "off" ? "step mode off" : `pauses ${m === "each" ? "after each step" : "at breakpoints"}`).then(onChanged);
  return (
    <label className="small step-mode" title="Pause the run after each step, or after the steps marked as breakpoints, for the operator's Next">
      Step mode{" "}
      <select value={mode} onChange={(e) => set(e.target.value)}>
        <option value="off">off</option>
        <option value="each">pause after each step</option>
        <option value="breakpoints">pause at breakpoints{run.control?.breakpoints?.length ? ` (${run.control.breakpoints.length})` : ""}</option>
      </select>
    </label>
  );
}

/** Asks the reason of a Continue anyway, then sends it. */
function ContinueDialog({ runId, step, onClose, onDone }: { runId: string; step: string; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    const ok = await stepAction(runId, step, "continue", reason.trim());
    setBusy(false);
    if (ok) { onDone(); onClose(); }
  };
  return (
    <Dialog title={`Continue anyway past ${step}`} onClose={onClose} footer={
      <>
        <button onClick={onClose}>Cancel</button>
        <button className="danger" disabled={busy || !reason.trim()} onClick={send}>Continue anyway</button>
      </>
    }>
      <p className="small">The step ends <strong>succeeded</strong> now (if it runs, it is stopped; if it failed, the steps it held back run).
        The run is then <strong>not conform</strong>, and its results say who continued and why.</p>
      <label className="manual-global"><span className="small">Why the run can go on (required)</span>
        <textarea rows={2} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. LED checked by hand: the probe cannot see it on this bench" /></label>
    </Dialog>
  );
}

/** The steps paused after they ran (step-by-step mode): a banner each with Next, Run again and Continue anyway. */
export function PauseBanners({ run, onChanged }: { run: any; onChanged: () => void }) {
  const [continuing, setContinuing] = useState<string | null>(null);
  const paused: any[] = run.paused || [];
  if (!paused.length) return null;
  return (
    <>
      {paused.map((p) => (
        <div key={p.step} className="banner pause-banner">
          <span aria-hidden="true">⏸</span>
          <span>Paused after <code>{p.step}</code> <Badge value={p.verdict} /> <span className="muted">since {ago(p.since)}</span></span>
          <span className="spacer" />
          <button className="small" onClick={() => stepAction(run.id, p.step, "run_again").then(onChanged)} title="Run this step again, and the steps that need it">Run again</button>
          {p.verdict !== "succeeded" && <button className="small danger" onClick={() => setContinuing(p.step)} title="Count it as succeeded and go on">Continue anyway…</button>}
          <button className="small primary" onClick={() => stepAction(run.id, p.step, "next").then(onChanged)} title="Go on to the steps that need it">Next</button>
        </div>
      ))}
      {continuing && <ContinueDialog runId={run.id} step={continuing} onClose={() => setContinuing(null)} onDone={onChanged} />}
    </>
  );
}

/** The operator actions on the selected step, when it belongs to the phase running now. */
export function StepActions({ run, record, part, onChanged }: { run: any; record: any; part?: string; onChanged: () => void }) {
  const [continuing, setContinuing] = useState(false);
  if (!record || !run.running_phase || record.phase !== run.running_phase || part === "finally") return null;
  const step = `${record.phase}:${record.id}`;
  const ended = ["succeeded", "failed", "error", "skipped"].includes(record.state);
  const breakpoints: string[] = run.control?.breakpoints || [];
  const isBreak = breakpoints.includes(step) || breakpoints.includes(record.id);
  const toggleBreak = () => attempt(() => post(`runs/${run.id}/control`, {
    mode: "breakpoints", breakpoints: isBreak ? breakpoints.filter((b) => b !== step && b !== record.id) : [...breakpoints, step],
  })).then(onChanged);
  return (
    <div className="step-actions">
      {ended && record.state !== "skipped" && <button className="small" onClick={() => stepAction(run.id, step, "run_again").then(onChanged)} title="Run this step again, and every step that needs it (their earlier runs are kept)">Run again</button>}
      {(record.state === "running" || record.state === "failed" || record.state === "error") &&
        <button className="small danger" onClick={() => setContinuing(true)} title="Count it as succeeded and go on (the run is then not conform)">Continue anyway…</button>}
      <button className={classes("small", isBreak && "selected")} onClick={toggleBreak} title="Pause the run after this step (step mode: breakpoints)">{isBreak ? "● Breakpoint" : "○ Breakpoint"}</button>
      {record.passes > 1 && <span className="muted small">pass {record.passes}</span>}
      {continuing && <ContinueDialog runId={run.id} step={step} onClose={() => setContinuing(false)} onDone={onChanged} />}
    </div>
  );
}

/** What operators did on the run's steps. */
export function Interventions({ items }: { items?: any[] }) {
  if (!items?.length) return null;
  return (
    <details className="small interventions" open={items.some((i) => i.action === "continue")}>
      <summary>Operator actions ({items.length})</summary>
      <ul>
        {items.map((i, n) => (
          <li key={n} className={classes(i.action === "continue" && "warn-text")}>
            <span title={time(i.at)}>{ago(i.at)}</span> · {ACTION_LABELS[i.action] || i.action} <code>{i.action === "redo_from" ? i.target : i.step}</code>
            {i.action === "redo_from" && <> for <code>{i.step}</code></>}
            {i.by && <> by <strong>{i.by}</strong></>}{i.reason && <>: {i.reason}</>}
            {i.steps?.length > 1 && <span className="muted"> (ran again: {i.steps.join(", ")})</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** In a list of runs: the run is paused after a step. */
export function PausedFlag({ run }: { run: any }) {
  const paused: any[] = run.paused || [];
  if (!paused.length) return null;
  return <a className="badge pause-flag" href={`#/runs/${run.id || run.run}`} onClick={(e) => e.stopPropagation()}
    title={"Paused after " + paused.map((p) => p.step).join(", ")}>⏸ paused</a>;
}
