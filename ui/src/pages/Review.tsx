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

// Reviews of run results (sequencer SPEC section 11.1): accepting or rejecting a check, a step or a run's verdict
// after the run, with who and why, comments, sign-off; and exported reports (section 11.2), with their progress.
import { useEffect, useRef, useState } from "react";
import { get, post, url, useEvents, useResource } from "../api";
import { Dialog, Field, Text } from "../fields";
import { attempt, Badge, classes, time } from "../util";

const REVIEWER = "tsq.reviewer";

function reviewer(): string {
  try { return localStorage.getItem(REVIEWER) || ""; } catch { return ""; }
}

function rememberReviewer(name: string) {
  try { localStorage.setItem(REVIEWER, name); } catch { /* private window: asked again next time */ }
}

export type ReviewAction = "accepted" | "rejected" | "comment" | "revoke" | "sign_off" | "reopen";

const TITLES: Record<ReviewAction, string> = {
  accepted: "Accept", rejected: "Reject", comment: "Comment on", revoke: "Revoke review entry", sign_off: "Sign off the review",
  reopen: "Reopen the review",
};

/** Who, why, a comment and a reference for one review entry, then posted. path: runs/<id>/review or
 *  campaigns/<id>/review. */
export function ReviewDialog({ path, action, target, label, entry, onClose, onDone }: {
  path: string; action: ReviewAction; target?: any; label?: string; entry?: number; onClose: () => void; onDone: () => void;
}) {
  const [by, setBy] = useState(reviewer());
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const needsReason = ["accepted", "rejected", "revoke", "reopen"].includes(action);
  const canComment = !["revoke", "reopen"].includes(action);
  const ok = by.trim() && (!needsReason || reason.trim()) && (action !== "comment" || comment.trim());
  const submit = async () => {
    setBusy(true);
    rememberReviewer(by.trim());
    const body: any = { by: by.trim() };
    if (action === "accepted" || action === "rejected") Object.assign(body, { action: "review", target, status: action, reason, comment, ref });
    else if (action === "comment") Object.assign(body, { action: "review", target, comment, ref });
    else if (action === "revoke") Object.assign(body, { action: "revoke", entry, reason });
    else if (action === "sign_off") Object.assign(body, { action: "sign_off", comment });
    else Object.assign(body, { action: "reopen", reason });
    const done = await attempt(() => post(path, body), action === "sign_off" ? "Review signed off" : "Review recorded");
    setBusy(false);
    if (done) onDone();
  };
  const explain: Record<ReviewAction, string> = {
    accepted: "The result counts as met from now on, in the verdict, the campaign, the reports and the requirements coverage. The run's own results stay as they ran, shown beside.",
    rejected: "The result counts as not met from now on, in the verdict, the campaign, the reports and the requirements coverage. The run's own results stay as they ran, shown beside.",
    comment: "A note on this result, shown in the reports. It changes no status.",
    revoke: "The entry no longer counts; it stays in the review's history.",
    sign_off: "The review is closed and its reports become final (no DRAFT mark). Nothing can be reviewed until it is reopened.",
    reopen: "The review opens again: results can be accepted, rejected or commented on, and its reports are drafts again.",
  };
  return (
    <Dialog title={`${TITLES[action]}${label ? ` ${label}` : ""}`} onClose={onClose}
      footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={!ok || busy} onClick={submit}>{TITLES[action].split(" ")[0]}</button></>}>
      <p className="muted small">{explain[action]}</p>
      <Field label="Who" help="Your name, kept with the entry (an authenticating reverse proxy's user wins over it).">
        <Text value={by} onChange={setBy} placeholder="your name" invalid={!by.trim()} />
      </Field>
      {needsReason && <Field label="Why" help="Mandatory: why this result is judged so (it is shown in every report).">
        <Text value={reason} onChange={setReason} multiline placeholder="the reason" invalid={!reason.trim()} />
      </Field>}
      {canComment && <Field label="Comment" help="Free text, shown in the reports.">
        <Text value={comment} onChange={setComment} multiline placeholder={action === "comment" ? "the comment" : "optional"} invalid={action === "comment" && !comment.trim()} />
      </Field>}
      {(action === "accepted" || action === "rejected" || action === "comment") && (
        <Field label="Reference" help="Optional: a ticket, a deviation report, a waiver number...">
          <Text value={ref} onChange={setRef} placeholder="optional" />
        </Field>
      )}
    </Dialog>
  );
}

/** A verdict as reviewed, the raw one beside when the review changed it. */
export function ReviewedVerdict({ verdict, raw }: { verdict?: string | null; raw?: string | null }) {
  if (!raw || raw === verdict) return <Badge value={verdict} />;
  return <span className="reviewed-verdict" title={`As reviewed; the run gave ${raw}`}><Badge value={verdict} /> <span className="reviewed-mark">✎</span> <span className="muted small">raw</span> <Badge value={raw} /></span>;
}

/** The review notes of a target: status or comment, who, when, why. */
export function ReviewNotes({ notes }: { notes?: any[] }) {
  if (!notes?.length) return null;
  return (
    <div className="review-notes">
      {notes.map((n, i) => (
        <div key={i} className={classes("review-note", n.status || "comment")} title={time(n.at)}>
          <b>{n.status || "comment"}</b> {n.by}{n.reason ? `: ${n.reason}` : ""}{n.comment && <span className="muted"> {n.comment}</span>}{n.ref && <span className="muted"> [{n.ref}]</span>}
        </div>
      ))}
    </div>
  );
}

/** Accept, reject or comment on a target of an ended run whose review is not signed off. raw: its result as the run
 *  gave it (a check's status, a step's state, the verdict). */
export function ReviewButtons({ runId, target, label, raw, current, review, onDone }: {
  runId: string; target: any; label: string; raw?: string | null; current?: string | null; review?: any; onDone: () => void;
}) {
  const [action, setAction] = useState<ReviewAction | null>(null);
  if (review?.state === "signed_off") return null;
  const r = String(raw || "").toLowerCase();
  const c = String(current || raw || "").toLowerCase();
  const notMet = ["failed", "rejected"].includes(c);
  const met = ["success", "succeeded", "accepted"].includes(c);
  const canAccept = notMet && ["failed", "rejected"].includes(r);
  const canReject = (met || c === "error") && r !== "skipped";
  return (
    <span className="review-buttons">
      {canAccept && <button className="small" title="Count this result as met, with the reason why" onClick={() => setAction("accepted")}>Accept…</button>}
      {canReject && <button className="small" title="Count this result as not met, with the reason why" onClick={() => setAction("rejected")}>Reject…</button>}
      <button className="small" title="A comment on this result, shown in the reports" onClick={() => setAction("comment")}>Comment…</button>
      {action && <ReviewDialog path={`runs/${runId}/review`} action={action} target={target} label={label}
        onClose={() => setAction(null)} onDone={() => { setAction(null); onDone(); }} />}
    </span>
  );
}

const STATE_TEXT: Record<string, string> = { none: "not reviewed", open: "review open", signed_off: "signed off" };

export function ReviewStateBadge({ review }: { review?: any }) {
  const state = review?.state || "none";
  if (state === "none") return null;
  return <span className={classes("badge", state === "signed_off" ? "ok" : "warn")} title={state === "open" ? "Its review is open: its reports are drafts" : "Its review is signed off: its reports are final"}>{STATE_TEXT[state]}</span>;
}

/** The Review tab of a run: its state, the verdict as reviewed, sign-off, the history of its review (revoke), and the
 *  reviews of its earlier attempt that fit it. */
export function RunReviewTab({ run, onChanged }: { run: any; onChanged: () => void }) {
  const view = useResource<any>(`runs/${run.id}/review`, ["review", "run"], (d) => !d.run || d.run === run.id);
  const [action, setAction] = useState<{ action: ReviewAction; entry?: number; target?: any; label?: string } | null>(null);
  const v = view.data;
  if (!v) return <p className="muted">{view.error || "Reading the review…"}</p>;
  const ended = ["done", "cancelled"].includes(run.state);
  const signed = v.state === "signed_off";
  const gone = new Set((v.journal || []).filter((e: any) => e.action === "revoke").map((e: any) => e.revokes));
  const done = () => { setAction(null); view.refresh(); onChanged(); };
  const reapply = () => attempt(() => post(`runs/${run.id}/review`, { action: "reapply", by: reviewer() || "unknown" }), "Reviews reapplied").then((r) => r && done());
  return (
    <div className="review-tab">
      {!ended && <div className="banner info">The results of a run can be reviewed once it ended.</div>}
      <div className="review-head">
        <span>Verdict <ReviewedVerdict verdict={v.verdict} raw={v.raw_verdict} /></span>
        <span><ReviewStateBadge review={v} />{v.state === "none" && <span className="muted">nobody reviewed it</span>}</span>
        {v.accepted + v.rejected > 0 && <span className="muted small">{v.accepted} accepted, {v.rejected} rejected</span>}
        <span className="spacer" />
        {ended && !signed && <>
          <button onClick={() => setAction({ action: "comment", target: { kind: "run" }, label: "the run" })}>Comment…</button>
          {["failed"].includes(v.raw_verdict) && !(v.verdict === "succeeded" && v.raw_verdict === "failed") && <button title="Accept the run's failure: its verdict counts as succeeded" onClick={() => setAction({ action: "accepted", target: { kind: "run" }, label: "the run's verdict" })}>Accept the verdict…</button>}
          {["succeeded", "error"].includes(v.raw_verdict) && <button title="Reject the run's verdict: it counts as failed" onClick={() => setAction({ action: "rejected", target: { kind: "run" }, label: "the run's verdict" })}>Reject the verdict…</button>}
          <button className="primary" title="Close the review: its reports become final" onClick={() => setAction({ action: "sign_off" })}>Sign off…</button>
        </>}
        {signed && <button onClick={() => setAction({ action: "reopen" })}>Reopen…</button>}
        <a className="button" href={url(`runs/${run.id}/report`)} target="_blank" rel="noreferrer" title="The report of this run as reviewed">Report</a>
        <ExportButton kind="run" id={run.id} />
      </div>
      {(v.warnings || []).map((w: string) => <div key={w} className="banner warn">{w}</div>)}
      <p className="muted small">Accept or reject a check on the Pipeline tab (pick its exploit step), a step there or on the Steps tab. The run's own results never change: the review is kept beside them, and every report, the campaign and the requirements coverage count the results as reviewed.</p>
      {(v.reapplicable || []).length > 0 && !signed && (
        <div className="banner info list">
          <div><strong>{v.reapplicable.length}</strong> review(s) of the earlier attempt <a href={`#/runs/${v.reapplicable[0].run}`}>{v.reapplicable[0].run}</a> fit this run (same result): <button className="small" onClick={reapply}>Reapply them</button></div>
          <ul className="problems">{v.reapplicable.map((e: any) => <li key={e.id}><b>{e.status || "comment"}</b> {e.label} <span className="muted">({e.by}: {e.reason || e.comment})</span></li>)}</ul>
        </div>
      )}
      {(v.journal || []).length === 0 ? <p className="muted">No review entry yet.</p> : (
        <table className="grid compact review-journal">
          <thead><tr><th>#</th><th>When</th><th>Who</th><th>What</th><th>On</th><th>Why</th><th></th></tr></thead>
          <tbody>
            {v.journal.map((e: any) => (
              <tr key={e.id} className={classes(gone.has(e.id) && "revoked")}>
                <td>{e.id}</td><td title={e.at}>{time(e.at)}</td><td>{e.by}</td>
                <td><b className={classes("review-what", e.status || e.action)}>{e.action === "review" ? (e.status || "comment") : e.action.replace("_", " ")}</b>{gone.has(e.id) && <span className="muted"> (revoked)</span>}</td>
                <td>{e.target ? <code>{targetText(e.target)}</code> : e.revokes ? `entry ${e.revokes}` : ""}{e.was && e.status && <div className="muted small">was {String(e.was).toLowerCase()}</div>}</td>
                <td>{e.reason}{e.comment && <div className="muted">{e.comment}</div>}{e.ref && <div className="muted">{e.ref}</div>}{e.reapplied_from && <div className="muted small">reapplied from run {e.reapplied_from.run}</div>}</td>
                <td>{e.action === "review" && !gone.has(e.id) && !signed && <button className="small" onClick={() => setAction({ action: "revoke", entry: e.id, label: String(e.id) })}>Revoke…</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {action && <ReviewDialog path={`runs/${run.id}/review`} action={action.action} target={action.target} entry={action.entry}
        label={action.label} onClose={() => setAction(null)} onDone={done} />}
    </div>
  );
}

export function targetText(t: any): string {
  if (t.kind === "check") {
    const folder = t.scenario && !String(t.criteria).endsWith(t.scenario) ? `${t.criteria}/${t.scenario}` : t.criteria;
    return `${t.step}: ${folder}${t.check ? `::${t.check}` : ""}`;
  }
  if (t.kind === "step") return t.step;
  return t.kind;
}

/** The review of a campaign run: its state, comments, sign-off (with its runs' open reviews) and reopening. */
export function CampaignReview({ view, onChanged }: { view: any; onChanged: () => void }) {
  const [action, setAction] = useState<ReviewAction | null>(null);
  const own = view.review || { state: "none" };
  const counts = view.reviews || {};
  const signed = own.state === "signed_off";
  return (
    <div className="campaign-review">
      <div className="review-head">
        <span>Verdict <ReviewedVerdict verdict={view.verdict} raw={view.raw_verdict} /></span>
        <ReviewStateBadge review={own} />
        <span className="muted small">{counts.accepted || 0} result(s) accepted, {counts.rejected || 0} rejected · {counts.open || 0} run review(s) open, {counts.signed_off || 0} signed off</span>
        <span className="spacer" />
        {view.state === "done" && !signed && <>
          <button onClick={() => setAction("comment")}>Comment…</button>
          <button className="primary" title="Close the review of the campaign run, and of each of its runs whose review is open: the reports become final" onClick={() => setAction("sign_off")}>Sign off…</button>
        </>}
        {signed && <button onClick={() => setAction("reopen")}>Reopen…</button>}
      </div>
      {signed && own.signed_off && <div className="banner ok">Signed off by {own.signed_off.by} on {time(own.signed_off.at)}{own.signed_off.comment ? `: ${own.signed_off.comment}` : ""}</div>}
      {(own.entries || []).length > 0 && <ReviewNotes notes={own.entries} />}
      {view.state !== "done" && <p className="muted small">The campaign run can be signed off once its runs ended. Review each run's results on its page (Review tab).</p>}
      {action && <ReviewDialog path={`campaigns/${view.id}/review`} action={action} target={{ kind: "campaign" }} label={action === "comment" ? "the campaign run" : undefined}
        onClose={() => setAction(null)} onDone={() => { setAction(null); onChanged(); }} />}
    </div>
  );
}

/** Export a report (synthetic: one HTML page linking to the sequencer; complete: every page down to the logs,
 *  exploit reports and input previews, in one HTML file, or a zip when too large), with its progress. */
export function ExportButton({ kind, id }: { kind: "campaign" | "run"; id: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} title="Download the report as reviewed: the synthetic page, or the complete report (every page down to the logs, exploit reports and previews of the input files), navigable offline">Export…</button>
      {open && <ExportDialog kind={kind} id={id} onClose={() => setOpen(false)} />}
    </>
  );
}

function ExportDialog({ kind, id, onClose }: { kind: "campaign" | "run"; id: string; onClose: () => void }) {
  const [mode, setMode] = useState<"synthetic" | "complete">("complete");
  const [results, setResults] = useState(false);
  const [job, setJob] = useState<any>(null);
  const downloaded = useRef<string | null>(null);
  const path = kind === "campaign" ? `campaigns/${id}/export` : `runs/${id}/export`;
  useEvents((k, d) => {
    if (!job || d.export !== job.id) return;
    if (k === "export_progress") setJob((j: any) => ({ ...j, stage: d.stage, done: d.done, total: d.total }));
    if (k === "export") get(`exports/${job.id}`).then(setJob).catch(() => null);
  });
  // a fallback while the event stream is away: the job read now and then
  useEffect(() => {
    if (!job || job.state !== "running") return;
    const t = window.setInterval(() => get(`exports/${job.id}`).then((j) => setJob((cur: any) => cur && (j.state !== "running" || (j.done || 0) >= (cur.done || 0)) ? j : cur)).catch(() => null), 2000);
    return () => window.clearInterval(t);
  }, [job?.id, job?.state]);
  useEffect(() => {
    if (job?.state === "done" && downloaded.current !== job.id) {
      downloaded.current = job.id;
      window.location.href = url(`exports/${job.id}/download`);
    }
  }, [job?.state]);
  const start = async () => {
    const j = await attempt(() => post(path, { mode, include_results: results }));
    if (j) setJob(j);
  };
  const cancel = () => job && attempt(() => post(`exports/${job.id}/cancel`)).then(() => get(`exports/${job.id}`).then(setJob));
  const pct = job?.total ? Math.round((100 * (job.done || 0)) / job.total) : 0;
  return (
    <Dialog title={`Export the ${kind === "campaign" ? "campaign run's" : "run's"} report`} onClose={onClose}
      footer={!job || ["failed", "cancelled"].includes(job.state)
        ? <><button onClick={onClose}>Close</button><button className="primary" onClick={start}>Export</button></>
        : job.state === "running" ? <button onClick={cancel}>Cancel the export</button>
          : <><a className="button" href={url(`exports/${job.id}/download`)}>Download again</a><button className="primary" onClick={onClose}>Close</button></>}>
      {!job || ["failed", "cancelled"].includes(job.state) ? (
        <div className="export-options">
          <label className="radio"><input type="radio" checked={mode === "synthetic"} onChange={() => setMode("synthetic")} /> <b>Synthetic</b>: the report alone, one HTML page, its links to this sequencer. Small enough to email.</label>
          <label className="radio"><input type="radio" checked={mode === "complete"} onChange={() => setMode("complete")} /> <b>Complete</b>: every page{kind === "campaign" ? " from the campaign run down to each run" : ""}: the run reports, the steps' logs (a long log cut, its errors and warnings kept), the exploit reports and a preview of each input file, navigable offline. One HTML file that opens as it is; a zip when it would be too large.</label>
          {mode === "complete" && <label className="check"><input type="checkbox" checked={results} onChange={(e) => setResults(e.target.checked)} /> with the results zips (always a zip, then)</label>}
          <p className="muted small">The report is as reviewed: while a review is open, it is marked DRAFT.</p>
          {job?.state === "failed" && <div className="banner error">The export failed: {job.error}</div>}
          {job?.state === "cancelled" && <div className="banner warn">The export was cancelled.</div>}
        </div>
      ) : (
        <div className="export-progress">
          {job.state === "running" ? <>
            <div>{job.stage || "starting"}{job.total ? ` · ${job.done} of ${job.total}` : ""}</div>
            <div className="progress"><div style={{ width: `${pct}%` }} /></div>
            <p className="muted small">The export goes on if you close this; its file is kept until the results or their reviews change.</p>
          </> : <div className="banner ok">Ready: <code>{job.name}</code> ({Math.round((job.size || 0) / 1000)} kB){String(job.name).endsWith(".zip") && <> · open <code>index.html</code> once extracted, or <code>tsq view {job.name}</code></>}</div>}
        </div>
      )}
    </Dialog>
  );
}

/** The exports running now, a small badge in the header (they go on when their dialog is closed). */
export function ExportsBadge() {
  const [running, setRunning] = useState<Record<string, any>>({});
  useEvents((k, d) => {
    if (k === "export_progress") setRunning((r) => ({ ...r, [d.export]: d }));
    if (k === "export" && d.state !== "running") setRunning((r) => { const { [d.export]: _gone, ...rest } = r; return rest; });
    if (k === "export" && d.state === "running") setRunning((r) => ({ ...r, [d.export]: { ...d, done: 0, total: 0 } }));
  });
  const jobs = Object.values(running);
  if (!jobs.length) return null;
  const title = jobs.map((j: any) => `${j.of} ${j.target}: ${j.stage || "starting"} ${j.done || 0}/${j.total || "?"}`).join("\n");
  return <span className="badge run exports-badge" title={title}>exporting {jobs.length > 1 ? `${jobs.length} reports` : `${(jobs[0] as any).done || 0}/${(jobs[0] as any).total || "?"}`}</span>;
}
