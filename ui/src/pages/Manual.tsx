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

// Manual operations (sequencer SPEC section 6.9): a step that waits for a person on the bench. The Run page opens
// its dialog (the text, its pictures, its checks to tick, then Done or Failed); All runs and the campaign pages flag
// the runs that expect one.
import { useEffect, useState } from "react";
import { post, url } from "../api";
import { Dialog } from "../fields";
import { ago, attempt, classes, Markdown, time } from "../util";

const AUTHOR = "tsq.author";

/** The manual operations a run's step records wait for now. */
export function waitingManual(records: any[]): any[] {
  return records.filter((s) => s.state === "running" && s.manual && !s.manual.outcome);
}

function manualKey(s: any): string {
  return `${s.phase}:${s.id}` + (s.slot ? `[${s.slot}]` : "");
}

function operatorName(): string {
  try { return JSON.parse(localStorage.getItem(AUTHOR) || "{}").name || ""; } catch { return ""; }
}

/** The dialog of a manual operation: what to do, each check to tick, then Done (once all are ticked) or Failed. */
export function ManualDialog({ runId, record, onClose, onAnswered }: { runId: string; record: any; onClose: () => void; onAnswered: (record: any) => void }) {
  const m = record.manual;
  const key = manualKey(record);
  const [by, setBy] = useState(operatorName);
  const [note, setNote] = useState("");
  const [failing, setFailing] = useState(false);
  const [busy, setBusy] = useState(false);
  const checks: any[] = m.checks || [];
  const left = checks.filter((c) => !c.done).length;
  useEffect(() => {
    try { localStorage.setItem(AUTHOR, JSON.stringify({ ...JSON.parse(localStorage.getItem(AUTHOR) || "{}"), name: by.trim() })); } catch { /* storage off */ }
  }, [by]);
  const send = async (body: any, success?: string) => {
    setBusy(true);
    const result = await attempt(() => post(`runs/${runId}/manual/${encodeURIComponent(key)}`, { ...body, by: by.trim() }), success);
    setBusy(false);
    if (result) onAnswered(result);
    return result;
  };
  const answer = async (outcome: "done" | "failed") => {
    if (await send(outcome === "done" ? { outcome } : { outcome, note: note.trim() }, `${m.title}: ${outcome}`)) onClose();
  };
  return (
    <Dialog title={`Manual operation: ${m.title}`} onClose={onClose} wide footer={
      <>
        <label className="small manual-by">By <input value={by} onChange={(e) => setBy(e.target.value)} placeholder="your name" /></label>
        <span className="spacer" />
        {failing ? (
          <>
            <input className="manual-note" value={note} autoFocus onChange={(e) => setNote(e.target.value)} placeholder="what went wrong, e.g. no cable A on the bench" />
            <button className="danger" disabled={busy || !note.trim()} onClick={() => answer("failed")}>Fail the step</button>
            <button onClick={() => setFailing(false)}>Back</button>
          </>
        ) : (
          <>
            <button className="danger" disabled={busy} onClick={() => setFailing(true)} title="The operation cannot be done: the step fails, and the run goes on as for any failed step">Failed…</button>
            <button className="primary" disabled={busy || left > 0} onClick={() => answer("done")}
              title={left ? `Tick the ${left} check(s) left first` : "The operation is done: the run goes on"}>Done</button>
          </>
        )}
      </>
    }>
      <div className="manual-op">
        <div className="muted small">
          Step <code>{key}</code> of run <code>{runId}</code> waits for you since {ago(m.since)} <span title={time(m.since)}>({time(m.since)})</span>.
        </div>
        <Markdown text={m.text} className="manual-text" base={url(`runs/${runId}/files/${m.base || "definition/"}`)} />
        {checks.length > 0 && (
          <ol className="manual-checks">
            {checks.map((c, i) => (
              <li key={i} className={classes(c.done && "done")}>
                <label>
                  <input type="checkbox" checked={!!c.done} disabled={busy} onChange={(e) => send({ check: i, done: e.target.checked })} />
                  <span>{c.text}</span>
                </label>
                {c.done && c.by && <span className="muted small"> · {c.by}, {ago(c.at)}</span>}
              </li>
            ))}
          </ol>
        )}
        {checks.length > 0 && <p className="muted small">{left ? `${left} of ${checks.length} check(s) left before Done.` : "Every check is ticked."} Each tick is shared at once with the others looking at this run.</p>}
      </div>
    </Dialog>
  );
}

/** On the Run page: the manual operations the run waits for, a banner each, and the dialog of the first one, opened
 *  by itself (closed, it stays closed until opened again from its banner). */
export function ManualOperations({ runId, records, onAnswered }: { runId: string; records: any[]; onAnswered: () => void }) {
  const waiting = waitingManual(records);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [opened, setOpened] = useState<string | null>(null);
  const shown = waiting.find((s) => manualKey(s) === opened) || waiting.find((s) => !closed.has(manualKey(s)));
  if (!waiting.length) return null;
  return (
    <>
      {waiting.map((s) => (
        <div key={manualKey(s)} className="banner manual-banner">
          <span className="manual-icon" aria-hidden="true">✋</span>
          <span>Waiting for a manual operation: <strong>{s.manual.title}</strong> <span className="muted">(step <code>{manualKey(s)}</code>, since {ago(s.manual.since)})</span></span>
          <span className="spacer" />
          <button className="small primary" onClick={() => setOpened(manualKey(s))}>Open</button>
        </div>
      ))}
      {shown && <ManualDialog key={manualKey(shown)} runId={runId} record={shown} onAnswered={onAnswered}
        onClose={() => { setClosed(new Set([...closed, manualKey(shown)])); setOpened(null); }} />}
    </>
  );
}

/** In a list of runs: the run expects a manual operation. */
export function ManualFlag({ run }: { run: any }) {
  const waiting: any[] = run.awaiting_manual || [];
  if (!waiting.length) return null;
  return (
    <a className="badge manual-flag" href={`#/runs/${run.id || run.run}`} onClick={(e) => e.stopPropagation()}
      title={"Expects a manual operation: " + waiting.map((w) => `${w.title} (${w.step})`).join(", ")}>✋ manual op</a>
  );
}

/** Run a datapack of a campaign again: a new run of the same zip, unchanged; the earlier attempt is kept. */
export async function rerun(run: { id?: string; run?: string }) {
  const id = run.id || run.run;
  if (!window.confirm(`Run the datapack of run ${id} again in its campaign? A new run of the same datapack is queued, unchanged; run ${id} is kept as an earlier attempt.`)) return undefined;
  return attempt(() => post(`runs/${id}/rerun`), `datapack of run ${id} queued again`);
}
