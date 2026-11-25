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

// Issues of issue trackers (GitLab, GitHub, Jira, Redmine) bound to test results (sequencer SPEC section 11.3): the known
// issues of a run's failures, reporting a new issue pre-filled from a failure or binding an existing one, the
// Issues subtab of Datapacks (the sources of issues.yml and their issues), and the issues of each requirement.
import { useEffect, useState, type ReactNode } from "react";
import { get, post, useResource } from "../api";
import { createFile } from "../editor/Session";
import { Dialog, Field, Text } from "../fields";
import { NavIcon } from "../navicons";
import { attempt, classes, Markdown, openInNewTab, Spinner, time } from "../util";

const REVIEWER = "tsq.reviewer";
const who = () => { try { return localStorage.getItem(REVIEWER) || ""; } catch { return ""; } };
const remember = (name: string) => { try { localStorage.setItem(REVIEWER, name); } catch { /* asked again */ } };

/** A target's key, as reviews and bindings name it (SPEC §11.1). */
export function targetKey(t: any): string {
  if (t?.kind === "check") return `check|${t.step || ""}|${t.criteria || ""}|${t.scenario || ""}|${t.check || ""}`;
  if (t?.kind === "step") return `step|${t.step}`;
  return String(t?.kind || "run");
}

const GITHUB_COLORS: Record<string, string> = { gray: "#59636e", blue: "#0969da", green: "#1a7f37", yellow: "#9a6700",
  orange: "#bc4c00", red: "#cf222e", pink: "#bf3989", purple: "#8250df" };

/** The colour of an issue's type: the tool's (a GitLab type:: label, a GitHub issue type), else by what the type is like
 * (a bug red, an improvement blue, a task purple); none for an issue of no known type. */
export function typeColor(issue: any): string | undefined {
  const c = GITHUB_COLORS[String(issue?.type_color || "").toLowerCase()] || issue?.type_color;
  if (c && /^#[0-9a-f]{3,8}$/i.test(c)) return c;
  const t = String(issue?.type || "");
  if (/bug|defect|incident|problem|fault/i.test(t)) return "#cf222e";
  if (/improv|enhanc|feature|story|request/i.test(t)) return "#0969da";
  if (/task|chore|epic/i.test(t)) return "#8250df";
  return undefined;
}

/** An issue: the icon its tool gives its type when there is one (Jira), else a circle with a small dot (any kind of
 * issue, not only a bug) in the colour of its type, else green when open and grey when closed; faded when closed. */
export function IssueIcon({ state, size = 14, issue }: { state?: string; size?: number; issue?: any }) {
  const [broken, setBroken] = useState(false);
  const st = state ?? issue?.state;
  const color = typeColor(issue);
  const title = issue?.type ? `${issue.type}${st ? ` (${st})` : ""}` : undefined;
  if (issue?.type_icon && !broken)
    return <img className={classes("issue-icon", st, "typed")} src={issue.type_icon} width={size} height={size} alt={issue.type}
      title={title} onError={() => setBroken(true)} />;
  return (
    <svg className={classes("issue-icon", st, color && "typed")} viewBox="0 0 16 16" width={size} height={size}
      style={color ? { color } : undefined} aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title && <title>{title}</title>}
      <circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="8" cy="8" r="1.25" fill="currentColor" />
    </svg>
  );
}

/** What a source's test found of issue types: those a new issue can have and where they come from, the ones proposed
 * for a failure and for a green run, and the types of the issues read. */
export function IssueTypes({ types }: { types?: any }) {
  if (!types) return null;
  const seen = Object.entries(types.seen || {}) as [string, number][];
  const read = seen.length > 0 && <> Of the issues read: {seen.map(([k, n], i) => <span key={k}>{i ? ", " : ""}{k || "no type"} {n}</span>)}.</>;
  if (types.error) return <div className="small warn">Issue types: {types.error}.{read}</div>;
  const list: any[] = types.list || [];
  return (
    <div className="small issue-types">
      <div>Issue types{types.origin ? <span className="muted"> ({types.origin})</span> : ""}:{" "}
        {list.length ? list.map((t) => <span key={t.name} className="issue-type" title={t.label || t.name}>
          <IssueIcon issue={{ type: t.name, type_color: t.color, type_icon: t.icon, state: "open" }} size={12} />{t.name}</span>)
          : <span className="muted">none, no choice when creating an issue</span>}
      </div>
      {list.length > 0 && <div className="muted">Proposed: <strong>{types.failure}</strong> for a failure, <strong>{types.improvement}</strong> for a green run.{read}</div>}
      {!list.length && read && <div className="muted">{read}</div>}
    </div>
  );
}

export function IssueChip({ issue, via, onUnbind }: { issue: any; via?: string[]; onUnbind?: () => void }) {
  if (!issue) return null;
  const title = `${issue.title || ""}${issue.assignees?.length ? `\nassigned to ${issue.assignees.join(", ")}` : ""}${via?.length ? `\n${via.join(", ")}` : ""}`;
  return (
    <span className={classes("issue-chip", issue.state)} title={title.trim()}>
      <IssueIcon issue={issue} /> {issue.url ? <a href={issue.url} target="_blank" rel="noreferrer">{issue.key}</a> : issue.key}
      <span className="issue-state">{issue.state}</span>
      {onUnbind && <button className="link small" title="Unbind this issue (with the reason why)" onClick={onUnbind}>×</button>}
    </span>
  );
}

/** The known issues of a run: its sources, its failures with their known issues, the bindings of its test definition. */
export function useRunIssues(runId: string | null, ended: boolean) {
  return useResource<any>(ended && runId ? `runs/${runId}/issues` : null, ["issue", "review"]);
}

/** The known issues of a target of a run, and Issue… (report a new one, or bind one). */
export function TargetIssues({ runId, known, target, label, onChanged }: {
  runId: string; known: any; target: any; label: string; onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [unbind, setUnbind] = useState<any>(null);
  if (!known) return null;
  const t = (known.targets || []).find((x: any) => x.key === targetKey(target));
  const issues: any[] = t?.issues || [];
  if (!t && !issues.length) return null;
  return (
    <span className="target-issues">
      {issues.map((b) => <IssueChip key={b.id} issue={b.issue} onUnbind={() => setUnbind(b)} />)}
      {(known.sources || []).length > 0 && (
        <button className="small" title="Report this failure as a new issue (pre-filled), or bind an existing issue to it: it is then a known issue of every run of this test definition that fails here"
          onClick={() => setOpen(true)}><IssueIcon /> Issue…</button>
      )}
      {open && <IssueDialog runId={runId} target={target} label={label} onClose={() => setOpen(false)} onDone={() => { setOpen(false); onChanged(); }} />}
      {unbind && <UnbindDialog binding={unbind} onClose={() => setUnbind(null)} onDone={() => { setUnbind(null); onChanged(); }} />}
    </span>
  );
}

function UnbindDialog({ binding, onClose, onDone }: { binding: any; onClose: () => void; onDone: () => void }) {
  const [by, setBy] = useState(who());
  const [reason, setReason] = useState("");
  const ok = by.trim() && reason.trim();
  return (
    <Dialog title={`Unbind ${binding.issue?.key}`} onClose={onClose}
      footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={!ok} onClick={async () => {
        remember(by.trim());
        if (await attempt(() => post(`issues/bindings/${binding.id}/unbind`, { by: by.trim(), reason }), "Issue unbound")) onDone();
      }}>Unbind</button></>}>
      <p className="muted small">The issue stays in its tool; it is no longer a known issue of this failure. The binding is kept in the history.</p>
      <Field label="Who"><Text value={by} onChange={setBy} invalid={!by.trim()} /></Field>
      <Field label="Why"><Text value={reason} onChange={setReason} multiline invalid={!reason.trim()} placeholder="not the cause, fixed elsewhere…" /></Field>
    </Dialog>
  );
}

/** Report a failure as a new issue, pre-filled (its source, project, title, body, labels: all editable), or bind an
 *  existing issue (searched, or its reference or URL). */
export function IssueDialog({ runId, target, label, onClose, onDone }: {
  runId: string; target: any; label: string; onClose: () => void; onDone: () => void;
}) {
  const [mode, setMode] = useState<"new" | "bind">("new");
  const [source, setSource] = useState<string | null>(null);
  const [draft, setDraft] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [labels, setLabels] = useState("");
  const [kind, setKind] = useState("");
  const [project, setProject] = useState("");
  const [token, setToken] = useState("");
  const [by, setBy] = useState(who());
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<any[] | null>(null);
  const [ref, setRef] = useState("");
  const [note, setNote] = useState("");
  const [searchIn, setSearchIn] = useState("");          // the source searched ("": every source)
  useEffect(() => {
    let live = true;
    setError(null);
    post(`runs/${runId}/issues/draft`, { target, source, by: who() })
      .then((d: any) => {
        if (!live) return;
        setDraft(d); setTitle(d.title); setBody(d.body); setLabels((d.labels || []).join(", ")); setKind(d.type || "");
        setProject(d.projects?.[0] || "");
      })
      .catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [runId, source]);    // eslint-disable-line react-hooks/exhaustive-deps
  const card = draft?.sources?.find((c: any) => c.name === draft?.source);
  const create = async () => {
    setBusy(true);
    remember(by.trim());
    const done = await attempt(() => post(`runs/${runId}/issues`, {
      action: "create", target, source: draft.source, project: project || undefined, ...(kind ? { type: kind } : {}), title, body, by: by.trim(),
      labels: labels.split(",").map((x) => x.trim()).filter(Boolean), ...(token.trim() ? { token: token.trim() } : {}),
    }), "Issue created and bound");
    setBusy(false);
    if (done) onDone();
  };
  const bind = async (issue: string) => {
    setBusy(true);
    remember(by.trim());
    const done = await attempt(() => post(`runs/${runId}/issues`, { action: "bind", target, issue, note, by: by.trim() }), "Issue bound");
    setBusy(false);
    if (done) onDone();
  };
  const search = async () => {
    setFound(null);
    const qs = new URLSearchParams({ q, state: "all" });
    if (searchIn) qs.set("source", searchIn);
    const r = await attempt(() => get<any[]>(`runs/${runId}/issues/search?${qs}`));
    setFound(r || []);
  };
  const already = [...(draft?.known || []).map((b: any) => b.issue), ...(draft?.duplicates || [])];
  return (
    <Dialog wide title={`Issue for ${label}`} onClose={onClose}
      footer={<>
        <button onClick={onClose}>Cancel</button>
        {mode === "new"
          ? <button className="primary" disabled={!draft || busy || !title.trim() || !by.trim() || (card?.group && !project)} onClick={create}>Create the issue</button>
          : <button className="primary" disabled={busy || !ref.trim() || !by.trim()} onClick={() => bind(ref.trim())}>Bind</button>}
      </>}>
      <div className="tabs subtabs">
        <button className={classes(mode === "new" && "selected")} onClick={() => setMode("new")}>New issue</button>
        <button className={classes(mode === "bind" && "selected")} onClick={() => setMode("bind")}>Existing issue</button>
      </div>
      {error && <div className="error-box">{error}</div>}
      {!draft && !error && <Spinner text="Preparing the issue…" />}
      {already.length > 0 && (
        <div className="warn small">
          Already about this failure: {already.map((i: any) => <span key={i.key}><IssueChip issue={i} />{mode === "new" && !(draft?.known || []).some((b: any) => b.issue.key === i.key) &&
            <button className="small" onClick={() => bind(i.key)} title="Bind this issue instead of creating another">Bind it</button>}</span>)}
        </div>
      )}
      <Field label="Who" help="Your name, kept with the binding (an authenticating reverse proxy's user wins over it)."><Text value={by} onChange={setBy} invalid={!by.trim()} /></Field>
      {mode === "new" && draft && (<>
        <Field label="Where" help="The issue source (issues.yml): proposed by its routes, else the default one.">
          <select value={draft.source} onChange={(e) => setSource(e.target.value)}>
            {(draft.sources || []).map((c: any) => <option key={c.name} value={c.name} disabled={!c.capabilities?.includes("create")}>{c.label || c.name} ({c.backend} {c.project})</option>)}
          </select>
        </Field>
        {card?.group && (
          <Field label="Project" help="The project of the group where the issue goes.">
            <select value={project} onChange={(e) => setProject(e.target.value)}>
              {(draft.projects || []).map((p: string) => <option key={p} value={p}>{p}</option>)}
            </select>
          </Field>
        )}
        {(draft.types || []).length > 0 && (
          <Field label="Type" help="The kind of issue, as its tool has them: GitLab's scoped labels type::<name> of the project or its group (else GitLab's own types), the GitHub organization's issue types, the Jira project's issue types, the Redmine project's trackers. Proposed: the source's default (else a bug) for a failure, an improvement for a green run.">
            <span className="issue-type">
              {(() => { const t = (draft.types || []).find((x: any) => x.name === kind);
                return t && <IssueIcon key={t.name} issue={{ type: t.name, type_color: t.color, type_icon: t.icon, state: "open" }} />; })()}
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                {(draft.types || []).map((t: any) => <option key={t.name} value={t.name}>{t.name}{t.label && t.label !== t.name ? ` (${t.label})` : ""}</option>)}
              </select>
            </span>
          </Field>
        )}
        <Field label="Title"><Text value={title} onChange={setTitle} invalid={!title.trim()} /></Field>
        <Field label="Text" help="Pre-filled from the failure: the run and where it ran, links to its report, exploit report and log, the requirements, the lines of its log that look like errors. Check that nothing secret is in it before creating the issue.">
          <textarea className="mono issue-body" value={body} onChange={(e) => setBody(e.target.value)} rows={14} />
        </Field>
        <Field label="Labels" help="Comma-separated; a label per requirement of the failure is proposed (requirements: labels in issues.yml). Redmine has no labels: they are not sent."><Text value={labels} onChange={setLabels} /></Field>
        {draft.requirements?.length > 0 && <div className="muted small">Requirements of this failure: {draft.requirements.join(", ")}</div>}
        <Field label="Your token" help="Optional: create the issue in your own name with your token for this tool (never kept; Jira Cloud: email:token; Redmine: your API key). Without it, the sequencer's token creates it, and the text says who reported it.">
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="optional" autoComplete="off" />
        </Field>
      </>)}
      {mode === "bind" && (<>
        <Field label="Issue" help="A reference (bench#42, #42, BENCH-12) or the URL of the issue; or search below and pick one.">
          <Text value={ref} onChange={setRef} placeholder="bench#42, or its URL" invalid={!ref.trim()} />
        </Field>
        <Field label="Note" help="Optional: why it is bound."><Text value={note} onChange={setNote} placeholder="optional" /></Field>
        <div className="issue-search">
          <select value={searchIn} onChange={(e) => setSearchIn(e.target.value)} title="Where to search">
            <option value="">every source</option>
            {(draft?.sources || []).map((c: any) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
          </select>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the issues: words of their title or text…"
            onKeyDown={(e) => e.key === "Enter" && search()} />
          <button className="small" onClick={search}>Search</button>
        </div>
        {found && (found.length === 0 ? <p className="muted small">No issue found.</p> : (
          <table className="grid compact">
            <tbody>{found.map((i) => (
              <tr key={i.key} className={classes(ref === i.key && "selected")} onClick={() => setRef(i.key)} style={{ cursor: "pointer" }}>
                <td><IssueChip issue={i} /></td><td>{i.title}</td><td className="muted small">{time(i.updated)}</td>
              </tr>))}</tbody>
          </table>
        ))}
      </>)}
    </Dialog>
  );
}

/** A run's known issues (its Issues tab): its failures with their known issues, the other issues bound to its test
 *  definition. */
export function RunIssuesTab({ runId, known, onChanged }: { runId: string; known: any; onChanged: () => void }) {
  const [unbind, setUnbind] = useState<any>(null);
  const [refreshing, setRefreshing] = useState(false);
  if (!known) return <Spinner className="block" text="Reading the issues…" />;
  const others = (known.bindings || []).filter((b: any) => !b.failed_here);
  return (
    <div className="run-issues">
      <div className="synthesis-head">
        <div className="muted small">
          {(known.sources || []).length
            ? <>Sources: {known.sources.map((c: any) => <code key={c.name} title={`${c.backend} ${c.url || ""}`}>{c.label || c.name} ({c.project}){c.default ? " default" : ""}</code>)}</>
            : <>This run's datapack names no issue source: add a <code>issues.yml</code> to its tests repository (Datapacks, Issues) and generate it again.</>}
        </div>
        <span className="spacer" />
        <button className="small" disabled={refreshing} onClick={async () => { setRefreshing(true); await attempt(() => get(`runs/${runId}/issues?refresh=true`)); setRefreshing(false); onChanged(); }}
          title="Read the state of the issues again from their tools">{refreshing ? "Refreshing…" : "↻ Refresh"}</button>
      </div>
      {known.error && <div className="error-box">{known.error}</div>}
      {(known.targets || []).length === 0 ? <p className="muted">Nothing failed in this run.</p> : (
        <table className="grid compact">
          <thead><tr><th>What failed</th><th>Status</th><th>Known issues</th></tr></thead>
          <tbody>{known.targets.map((t: any) => (
            <tr key={t.key}>
              <td><code>{t.label}</code>{t.route && <div className="muted small">new issues go to {t.route}</div>}</td>
              <td><span className="badge bad">{t.status}</span></td>
              <td><TargetIssues runId={runId} known={known} target={t.target} label={t.label} onChanged={onChanged} /></td>
            </tr>))}</tbody>
        </table>
      )}
      {others.length > 0 && (<>
        <h4>Other issues bound to {known.testdef}</h4>
        <p className="muted small">Bound to what did not fail in this run.</p>
        <table className="grid compact">
          <tbody>{others.map((b: any) => (
            <tr key={b.id}><td><code>{b.label}</code></td><td><IssueChip issue={b.issue} onUnbind={() => setUnbind(b)} /></td>
              <td className="muted small">bound by {b.by || "?"} {time(b.at)} from run {b.run}{b.note ? `: ${b.note}` : ""}</td></tr>))}</tbody>
        </table>
      </>)}
      {unbind && <UnbindDialog binding={unbind} onClose={() => setUnbind(null)} onDone={() => { setUnbind(null); onChanged(); }} />}
    </div>
  );
}

// ── Datapacks, Issues ──────────────────────────────────────────────────────

const INTRO = `Where the issues of this tests repository go (**issues.yml**): GitLab, GitHub, Jira or Redmine projects. A failure of a run (a check, a step, its verdict) can be reported as an issue, pre-filled, or bound to an existing one from its Run page (**Issue…**); it is then a **known issue** of every run of the test definition that fails there. An issue is about the requirements its failure verifies, and those it names (a label \`req:<id>\`, or their id in its text): the Requirements subtab shows them.`;

const STARTER = `# Where the issues of these tests go (sequencer SPEC, section 11.3)
default: bench
sources:
  bench:
    backend: gitlab                      # gitlab, github, jira or redmine
    options: {url: https://gitlab.com, project: my-group/bench}
    credentials: {token: env:GITLAB_TOKEN}    # env:<VARIABLE> or file:<path> on the sequencer's host
    labels: [tsq]
#  jira: {backend: jira, options: {url: https://acme.atlassian.net, project: BENCH},
#         credentials: {email: env:JIRA_EMAIL, token: env:JIRA_TOKEN}}
#  lab: {backend: redmine, options: {url: https://redmine.example.com, project: lab, tracker: Bug},
#        credentials: {api_key: env:REDMINE_KEY}}    # markup: markdown if the server formats in Markdown
#routes:                                 # where a new issue goes: the first that matches, else default
#  - {source: jira, step: "firmware:*"}
requirements:
  labels: "req:{id}"                     # a label per requirement
  text: true                             # the ids of the requirements named in its title or text
`;

export function IssuesTab({ sid }: { sid: string }) {
  const res = useResource<any>(`edit/sessions/${sid}/issues`);
  const [source, setSource] = useState<string>("");
  const [state, setState] = useState("open");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [find, setFind] = useState("");          // words among the issues listed (the server filters, a page at a time)
  const [found, setFound] = useState("");
  const [only, setOnly] = useState("");
  const [offset, setOffset] = useState(0);
  const [tests, setTests] = useState<Record<string, any>>({});
  useEffect(() => { const t = setTimeout(() => setFound(find.trim()), 300); return () => clearTimeout(t); }, [find]);
  useEffect(() => setOffset(0), [source, state, query, found, only]);
  const d = res.data;
  const qs = new URLSearchParams({ state, ...(source ? { source } : {}), ...(query ? { q: query } : {}), ...(found ? { find: found } : {}),
    ...(only ? { only } : {}), offset: String(offset), limit: String(PAGE) });
  const issues = useResource<any>(d?.sources?.length ? `edit/sessions/${sid}/issues/list?${qs}` : null, ["issue"]);
  const edit = () => d?.file && openInNewTab({ path: `edit/${sid}`, query: { file: d.file } });
  const test = async (name: string) => {
    setTests((t) => ({ ...t, [name]: { running: true } }));
    const r = await attempt(() => post(`edit/sessions/${sid}/issues/test`, { source: name }));
    setTests((t) => ({ ...t, [name]: r || { error: "the test failed" } }));
  };
  const list: any[] = issues.data?.issues || [];
  const total: number = issues.data?.total ?? 0;
  const reading: Record<string, any> = issues.data?.loading || {};
  const busy = Object.keys(reading).length > 0;
  useEffect(() => {                                 // a list still read in the background: its progress, every second
    if (!busy) return;
    const t = setTimeout(() => issues.refresh(), 1000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issues.data]);
  return (
    <div className="issues-tab">
      <Markdown text={INTRO} className="intro" />
      {res.error && <div className="error-box">{res.error}</div>}
      {!d && !res.error && <Spinner className="block" text="Reading issues.yml…" />}
      {d && !d.file && (
        <div className="muted">
          <p>No <code>issues.yml</code> in this repository: write one at its root with the GitLab, GitHub, Jira or Redmine projects its issues go to.</p>
          <button className="primary" onClick={async () => {
            const ok = await attempt(() => createFile(sid, "issues.yml", undefined, STARTER), "issues.yml created");
            if (ok !== undefined) { res.refresh(); openInNewTab({ path: `edit/${sid}`, query: { file: "issues.yml" } }); }
          }}>Create issues.yml…</button>
        </div>
      )}
      {d?.error && <div className="error-box">{d.error} <button className="small" onClick={edit}>Edit issues.yml…</button></div>}
      {d?.sources?.length > 0 && (<>
        <div className="req-sources issue-sources">
          {d.sources.map((c: any) => {
            const t = tests[c.name];
            return (
              <div key={c.name} className={classes("testdef-item", source === c.name && "active")} onClick={() => setSource(source === c.name ? "" : c.name)}
                title={source === c.name ? "Show every source again" : `Only the issues of '${c.name}'`}>
                <strong>{c.label || c.name}</strong>
                <span className="muted small mono">{c.backend} · {c.project}{c.group ? " (group)" : ""}</span>
                {c.default && <span className="muted small">default source</span>}
                {c.error && <span className="warn small">{c.error}</span>}
                <span className="issue-test">
                  <button className="small" onClick={(e) => { e.stopPropagation(); test(c.name); }} disabled={t?.running}
                    title="Read its open issues now: whether the server answers and the token works">{t?.running ? "Testing…" : "Test connection"}</button>
                  {t && !t.running && (t.error ? <span className="warn small" title={t.error}>✗ {t.error.slice(0, 120)}</span>
                    : <span className="ok small">✓ {t.count} open issue(s) read in {t.seconds} s</span>)}
                </span>
                {t && !t.running && !t.error && <IssueTypes types={t.types} />}
                {t?.calls?.length > 0 && (
                  <details className="small" onClick={(e) => e.stopPropagation()}>
                    <summary>{t.calls.length} call(s) to the server</summary>
                    {t.calls.map((x: any, i: number) => <div key={i} className="mono">{x.method} {x.url} → {x.status ?? x.error}</div>)}
                  </details>
                )}
              </div>
            );
          })}
        </div>
        <div className="cov-actions">
          <select style={{ flex: "none" }} value={state} onChange={(e) => setState(e.target.value)}>
            <option value="open">open</option><option value="closed">closed</option><option value="all">all</option>
          </select>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && setQuery(q)}
            placeholder="Search the tools (Enter)…" title="Searched by the tools themselves: words of the title or the text of every issue, listed or not" />
          <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Filter: key, title, label, assignee…"
            title="Among the issues listed (fast, as you type): every word must be in the key, the title, a label or an assignee" />
          <select style={{ flex: "none" }} value={only} onChange={(e) => setOnly(e.target.value)} title="Only some of the issues listed">
            <option value="">every issue</option><option value="reqs">naming requirements</option><option value="bound">bound to results</option>
          </select>
          <span className="spacer" />
          <button onClick={() => { issues.setData(null); get(`edit/sessions/${sid}/issues/list?${qs}&refresh=true`).then(issues.setData).catch(() => issues.refresh()); }}
            title="Read the whole list again from the tools (otherwise, every few minutes, only the issues updated since are read)">↻ Refresh</button>
          <button className="edit-testdef" onClick={edit} title="Edit issues.yml in the Editor, in a new browser tab"><NavIcon name="edit" size={16} />Edit</button>
        </div>
        {Object.entries(issues.data?.errors || {}).map(([name, e]: any) => <div key={name} className="error-box">{name}: {e}</div>)}
        {!issues.data && !issues.error && <Spinner className="block" text="Reading the issues…" />}
        {Object.entries(reading).map(([name, r]: any) => <ListProgress key={name} name={name} r={r} />)}
        {issues.error && <div className="error-box">{issues.error}</div>}
        {issues.data && (<>
          <Pager offset={offset} total={total} page={PAGE} onOffset={setOffset}>
            {total.toLocaleString()} issue(s){issues.data.listed !== total ? ` of ${issues.data.listed.toLocaleString()} listed` : ""}
            {issues.data.with_requirements ? `, ${issues.data.with_requirements.toLocaleString()} naming requirements` : ""}
            {issues.data.bound ? `, ${issues.data.bound.toLocaleString()} bound` : ""}
          </Pager>
          <table className="grid compact issues-list">
            <thead><tr><th>Issue</th><th>Title</th><th>Requirements</th><th>Bound to</th><th>Assignees</th><th>Updated</th></tr></thead>
            <tbody>{list.map((i) => (
              <tr key={i.key}>
                <td><IssueChip issue={i} /></td>
                <td>{i.title}{(i.labels || []).map((l: string) => <span key={l} className="tag">{l}</span>)}</td>
                <td>{Object.entries(i.requirements || {}).map(([r, via]: any) => <span key={r} className="mono small req-ref" title={via}>{r} </span>)}</td>
                <td className="small">{(i.bindings || []).map((b: any) => <div key={b.id}><a href={`#/runs/${encodeURIComponent(b.run)}`}>{b.testdef}</a> <span className="muted">{targetKey(b.target).split("|").filter(Boolean).slice(-1)[0]}</span></div>)}</td>
                <td className="small">{(i.assignees || []).join(", ")}</td>
                <td className="muted small nowrap">{time(i.updated)}</td>
              </tr>))}</tbody>
          </table>
          {total > PAGE && <Pager offset={offset} total={total} page={PAGE} onOffset={setOffset} />}
        </>)}
      </>)}
    </div>
  );
}

const PAGE = 100;

/** A list read in the background: how many issues, of how many when the tool says. */
function ListProgress({ name, r }: { name: string; r: { read: number; total?: number | null; exact?: boolean; mode?: string } }) {
  const total = r.total ?? null;
  const since = r.mode === "since";
  const pct = total ? Math.min(100, Math.round((100 * r.read) / Math.max(1, total))) : null;
  return (
    <div className="upload-progress issues-progress small muted">
      <Spinner />
      <span className="nowrap">{r.mode === "first" ? "Reading" : since ? "Refreshing" : "Reading again"} <b>{name}</b>: {r.read.toLocaleString()}
        {total && !since ? ` of ${r.exact === false ? "about " : ""}${total.toLocaleString()}` : ""} issue(s){since ? " changed since the last reading" : ""}
        {r.mode === "first" && r.read > 0 ? " (shown as they come)" : r.mode === "whole" ? " (the list shown until done)" : ""}</span>
      {pct !== null && !since && <div className="progress"><div style={{ width: `${pct}%` }} /></div>}
    </div>
  );
}

/** Pages of a long list: where it is, first, previous, next and last. */
function Pager({ offset, total, page, onOffset, children }: { offset: number; total: number; page: number; onOffset: (n: number) => void; children?: ReactNode }) {
  const last = Math.max(0, Math.floor((total - 1) / page) * page);
  return (
    <div className="pager muted small">
      <span>{children}</span>
      {total > page && (
        <span className="pager-nav">
          <button className="icon" disabled={offset === 0} onClick={() => onOffset(0)} title="First page">«</button>
          <button className="icon" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - page))} title="Previous page">‹</button>
          <span>{(offset + 1).toLocaleString()}–{Math.min(total, offset + page).toLocaleString()} of {total.toLocaleString()}</span>
          <button className="icon" disabled={offset >= last} onClick={() => onOffset(offset + page)} title="Next page">›</button>
          <button className="icon" disabled={offset >= last} onClick={() => onOffset(last)} title="Last page">»</button>
        </span>
      )}
    </div>
  );
}

// ── the issues of requirements ──────────────────────────────────────────────

/** How many open issues are about a requirement (◉ n), their keys and why on hover. */
export function IssueBadge({ entries }: { entries?: any[] }) {
  if (!entries?.length) return null;
  const open = entries.filter((e) => e.issue?.state === "open").length;
  const title = entries.map((e) => `${e.issue.key} [${e.issue.state}] ${e.issue.title || ""} (${(e.via || []).join(", ")})`).join("\n");
  return <span className={classes("issue-badge", !open && "closed")} title={title}><IssueIcon state={open ? "open" : "closed"} size={12} /> {open || entries.length}</span>;
}

/** The issues of a requirement, each with why it is about it. */
export function RequirementIssues({ entries }: { entries?: any[] }) {
  if (!entries?.length) return null;
  return (
    <div className="requirement-issues small">
      <strong>Issues</strong>
      {entries.map((e) => (
        <div key={e.issue.key}><IssueChip issue={e.issue} via={e.via} /> {e.issue.title} <span className="muted">({(e.via || []).join(", ")})</span></div>
      ))}
    </div>
  );
}

