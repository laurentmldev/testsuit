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

const settled = (c: any) => !!(c.done || c.bypassed);

/** One check of a manual operation: tick it, or bypass it with a reason; a comment either way. */
function CheckRow({ index, check, busy, send }: { index: number; check: any; busy: boolean; send: (body: any) => Promise<any> }) {
  const [editing, setEditing] = useState(false);
  const [comment, setComment] = useState<string>(check.comment || "");
  const [bypassing, setBypassing] = useState(false);
  const [nonconform, setNonconform] = useState(false);
  useEffect(() => { if (!editing) setComment(check.comment || ""); }, [check.comment]);
  const save = async (extra: any = {}) => {
    if (await send({ check: index, comment: comment.trim(), ...extra })) { setEditing(false); setBypassing(false); }
  };
  return (
    <li className={classes(check.done && "done", check.bypassed && "bypassed")}>
      <div className="manual-check-line">
        <label>
          <input type="checkbox" checked={!!check.done} disabled={busy} onChange={(e) => send({ check: index, done: e.target.checked })} />
          <span>{check.text}</span>
        </label>
        {check.bypassed && <span className="badge warn" title={check.nonconform ? "The operator went on without it, and said the run is then not conform" : "The operator went on without it"}>{check.nonconform ? "bypassed · non-conform" : "bypassed"}</span>}
        {settled(check) && check.by && <span className="muted small">{check.by}, {ago(check.at)}</span>}
        <span className="spacer" />
        {!editing && <button className="small link" disabled={busy} onClick={() => setEditing(true)}>{check.comment ? "Edit comment" : "Comment"}</button>}
        {!editing && !check.bypassed && <button className="small" disabled={busy} title="Go on without this check: give the reason, kept with the run"
          onClick={() => { setBypassing(true); setEditing(true); }}>Bypass…</button>}
        {check.bypassed && <button className="small" disabled={busy} onClick={() => send({ check: index, bypass: false })}>Undo bypass</button>}
      </div>
      {check.comment && !editing && <div className="manual-check-comment small">{check.comment}</div>}
      {editing && (
        <div className="manual-check-edit">
          <input value={comment} autoFocus onChange={(e) => setComment(e.target.value)}
            placeholder={bypassing ? "why it can be bypassed, e.g. LED broken, power checked with a meter" : "a comment on this check"}
            onKeyDown={(e) => { if (e.key === "Enter" && comment.trim()) save(bypassing ? { bypass: true, nonconform } : {}); if (e.key === "Escape") { setEditing(false); setBypassing(false); } }} />
          {bypassing
            ? <>
                <label className="small nowrap" title="Going on without this check means the results cannot be trusted as they are: the run is marked non-conform, with this reason">
                  <input type="checkbox" checked={nonconform} onChange={(e) => setNonconform(e.target.checked)} /> makes the run non-conform</label>
                <button className="small primary" disabled={busy || !comment.trim()} onClick={() => save({ bypass: true, nonconform })}>Bypass</button>
              </>
            : <button className="small primary" disabled={busy} onClick={() => save()}>Save</button>}
          <button className="small" onClick={() => { setEditing(false); setBypassing(false); setComment(check.comment || ""); }}>Cancel</button>
        </div>
      )}
    </li>
  );
}

/** The dialog of a manual operation: what to do, each check to tick or bypass (with a comment), a global comment, then
 *  Done (once every check is ticked or bypassed) or Failed. */
export function ManualDialog({ runId, record, onClose, onAnswered }: { runId: string; record: any; onClose: () => void; onAnswered: (record: any) => void }) {
  const m = record.manual;
  const key = manualKey(record);
  const [by, setBy] = useState(operatorName);
  const [note, setNote] = useState<string>(m.note || "");
  const [failing, setFailing] = useState(false);
  const [redoing, setRedoing] = useState(false);
  const redoFrom: string[] = m.redo_from || [];
  const [target, setTarget] = useState<string>(redoFrom[0] || "");
  const [busy, setBusy] = useState(false);
  const checks: any[] = m.checks || [];
  const left = checks.filter((c) => !settled(c)).length;
  const skipped = checks.filter((c) => c.bypassed).length;
  useEffect(() => {
    try { localStorage.setItem(AUTHOR, JSON.stringify({ ...JSON.parse(localStorage.getItem(AUTHOR) || "{}"), name: by.trim() })); } catch { /* storage off */ }
  }, [by]);
  // quiet: a save on the side (the comment, when it loses the focus) that keeps the buttons enabled, else the click
  // that moved the focus onto Done, Failed or Redo would land on a button disabled in between
  const send = async (body: any, success?: string, quiet = false) => {
    if (!quiet) setBusy(true);
    const result = await attempt(() => post(`runs/${runId}/manual/${encodeURIComponent(key)}`, { ...body, by: by.trim() }), success);
    if (!quiet) setBusy(false);
    if (result) onAnswered(result);
    return result;
  };
  const answer = async (outcome: "done" | "failed") => {
    if (await send({ outcome, note: note.trim() }, `${m.title}: ${outcome}`)) onClose();
  };
  const redo = async () => {
    if (await send({ outcome: "redo", from: target, note: note.trim() }, `${m.title}: redone from ${target}`)) onClose();
  };
  return (
    <Dialog title={`Manual operation: ${m.title}`} onClose={onClose} wide footer={
      <>
        <label className="small manual-by">By <input value={by} onChange={(e) => setBy(e.target.value)} placeholder="your name" /></label>
        <span className="spacer" />
        {redoing ? (
          <>
            <label className="small">Redo from <select value={target} onChange={(e) => setTarget(e.target.value)}>
              {redoFrom.map((t) => <option key={t} value={t}>{t}</option>)}</select></label>
            <span className="small muted">that step and the ones after it run again, then this operation is asked again</span>
            <button className="primary" disabled={busy || !target} onClick={redo}>Redo</button>
            <button onClick={() => setRedoing(false)}>Back</button>
          </>
        ) : failing ? (
          <>
            <span className="small">The comment above says why it failed.</span>
            <button className="danger" disabled={busy || !note.trim()} onClick={() => answer("failed")}>Fail the step</button>
            <button onClick={() => setFailing(false)}>Back</button>
          </>
        ) : (
          <>
            {redoFrom.length > 0 && <button disabled={busy} onClick={() => setRedoing(true)}
              title={`The bench is not right: run ${redoFrom.join(" or ")} again, and the steps after it, then come back here`}>Redo from…</button>}
            <button className="danger" disabled={busy} onClick={() => setFailing(true)} title="The operation cannot be done: the step fails, and the run goes on as for any failed step">Failed…</button>
            <button className="primary" disabled={busy || left > 0} onClick={() => answer("done")}
              title={left ? `Tick or bypass the ${left} check(s) left first` : skipped ? `Done, ${skipped} check(s) bypassed: the run goes on` : "The operation is done: the run goes on"}>
              {skipped ? `Done (${skipped} bypassed)` : "Done"}</button>
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
            {checks.map((c, i) => <CheckRow key={i} index={i} check={c} busy={busy} send={(b) => send(b)} />)}
          </ol>
        )}
        {checks.length > 0 && <p className="muted small">{left ? `${left} of ${checks.length} check(s) left to tick or bypass before Done.` : "Every check is ticked or bypassed."} Ticks, bypasses and comments are shared at once with the others looking at this run.</p>}
        <label className={classes("manual-global", failing && "required")}>
          <span className="small">{failing ? "Why it failed (required)" : "Comment on the whole operation (optional)"}</span>
          <textarea rows={2} value={note} autoFocus={failing} onChange={(e) => setNote(e.target.value)}
            onBlur={() => note.trim() !== (m.note || "") && send({ note: note.trim() }, undefined, true)}
            placeholder={failing ? "what went wrong, e.g. no cable A on the bench" : "anything the results should say about this operation"} />
        </label>
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
  // closed for this time it is asked: asked again (after a Redo from), it opens again
  const asked = (s: any) => `${manualKey(s)}@${s.manual.since}`;
  const shown = waiting.find((s) => manualKey(s) === opened) || waiting.find((s) => !closed.has(asked(s)));
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
      {shown && <ManualDialog key={asked(shown)} runId={runId} record={shown} onAnswered={onAnswered}
        onClose={() => { setClosed(new Set([...closed, asked(shown)])); setOpened(null); }} />}
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
