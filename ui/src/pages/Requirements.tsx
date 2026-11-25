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

// Requirements coverage (sequencer README, section 10.3; SPEC §5.5): which requirements the tests verify, and how.
// One view of a coverage (the /requirements endpoints): counts, requirements × tests rolled up per path node, and
// tests × requirements. Used by the Run page (one run), the campaign run page (its runs) and the Requirements subtab
// of Datapacks (the test definitions of a tests repository, with the tree of the requirements).
import { htmlToText, RichText } from "../richtext";
import { Fragment, useEffect, useMemo, useState, type CSSProperties } from "react";
import { get, url, useResource } from "../api";
import { createFile } from "../editor/Session";
import { IssueBadge, RequirementIssues } from "./Issues";
import { attempt, classes, Markdown, notify, openInNewTab, Spinner } from "../util";
import { NavIcon } from "../navicons";

export const STATUSES = ["passed", "failed", "error", "partial", "skipped", "not run", "uncovered"];
const MARK: Record<string, string> = { passed: "✓", failed: "✗", error: "!", partial: "◐", skipped: "–", "not run": "·", uncovered: "∅" };
const HELP: Record<string, string> = {
  passed: "Every link passed: its checks met (or accepted on review), the run succeeded for a link of the test definition",
  failed: "A link failed: a check not met (or rejected on review), or the run failed for a link of the test definition",
  error: "A link in error (a check, its exploit step or the run), none failed",
  partial: "Some links passed, the others were skipped or did not run",
  skipped: "No link passed, and one was skipped (its exploit step did not run)",
  "not run": "Linked, but no result yet: a test definition not run, or a run that did not reach it",
  uncovered: "No test definition or check lists it",
};

export function CoverageBadge({ status, changed }: { status?: string | null; changed?: boolean }) {
  if (!status) return <span className="muted">–</span>;
  return (
    <span className="cov-cell">
      <span className={classes("cov-badge", status.replace(" ", "-"))} title={HELP[status]}>{MARK[status]} {status}</span>
      {changed && <span className="cov-changed" title="Changed since: the requirement changed since the version this test verified">⟳</span>}
    </span>
  );
}

function under(path: string, node: string, children: boolean) {
  const p = (path || "").replace(/^\/+|\/+$/g, "");
  if (!node) return children || !p;
  return p === node || (children && p.startsWith(node + "/"));
}

function counts(rows: any[]) {
  const out: Record<string, number> = {};
  for (const r of rows) out[r.coverage] = (out[r.coverage] || 0) + 1;
  return out;
}

export function CoverageTiles({ rows }: { rows: any[] }) {
  const c = counts(rows);
  return (
    <div className="cov-tiles">
      <div className="cov-tile"><b>{rows.length}</b>requirements</div>
      {STATUSES.filter((s) => c[s]).map((s) => <div key={s} className={classes("cov-tile", s.replace(" ", "-"))} title={HELP[s]}><b>{c[s]}</b>{s}</div>)}
    </div>
  );
}

/** A requirement opened. With a session (the Requirements tab, live): read again in full from its source, as
 *  generation reads it (a REST source's details calls), the row's fields shown meanwhile. */
function RequirementDetail({ req: row, sid, colors = {} }: { req: any; sid?: string; colors?: Record<string, string> }) {
  const full = useResource<any>(sid && row.source && row.id
    ? `edit/sessions/${sid}/requirements/item?source=${encodeURIComponent(row.source)}&id=${encodeURIComponent(row.id)}` : null);
  const req = { ...row, ...(full.data?.requirement || {}), version: row.version };
  return (
    <div className="req-detail">
      {full.loading && !full.data && <Spinner text="Reading it in full…" />}
      {(full.data?.error || full.error) && <div className="warn small">Not read in full: {full.data?.error || full.error}</div>}
      {req.title && <strong>{req.title}</strong>}
      <RichText text={req.description} />
      {req.rationale && <div className="small req-long"><span className="muted">Rationale</span><RichText text={req.rationale} /></div>}
      {req.comments && <div className="small req-long"><span className="muted">Comments</span><RichText text={req.comments} /></div>}
      <div className="small muted req-facts">
        {req.source && <span>source <code>{req.source}</code></span>}
        {req.version && <span>version <code>{req.version}</code></span>}
        {req.pinned && <span>pinned <code>{req.pinned}</code></span>}
        {req.status && <span>status {req.status}</span>}
        {(req.tags || []).length > 0 && <span>{req.tags.map((t: string) => <span key={t} className="chip">{t}</span>)}</span>}
        {(req.dynamic_tags || []).length > 0 && <span>{req.dynamic_tags.map((t: string) => <span key={t} className="dyn-tag" style={tagStyle(colors[t])} title="dynamic tag: its texts hold one of its words">⚑ {t}</span>)}</span>}
        {req.url && <a href={req.url} target="_blank" rel="noreferrer">open in its tool ↗</a>}
      </div>
    </div>
  );
}

/** The Files tab of a run, on a file of its datapack at a line. */
export function inputHref(run: string, file: string, line?: number): string {
  return `#/runs/${encodeURIComponent(run)}?tab=files&file=${encodeURIComponent(file)}${line ? `&line=${line}` : ""}`;
}

function testHref(t: any) {
  return t.run ? `#/runs/${encodeURIComponent(t.run)}?tab=requirements` : null;
}

/** The style of a dynamic tag of a color its source gives (#rrggbb). */
export function tagStyle(color?: string): CSSProperties | undefined {
  return color && /^#([0-9a-f]{3}){1,2}$/i.test(color)
    ? { color, background: `color-mix(in srgb, ${color} 16%, transparent)`, outlineColor: color } : undefined;
}

const FOLD_OVER = 5;     // a path with more requirements than this starts folded in the matrix

/** A coverage: its counts, both matrices. node / children: only the requirements under a node of the tree. */

export function CoverageView({ data, node = "", children = true, showTests = true, sid, issues }: { data: any; node?: string; children?: boolean; showTests?: boolean; sid?: string;
  issues?: Record<string, any[]> }) {
  const [open, setOpen] = useState<string | null>(null);
  const [onlyIssues, setOnlyIssues] = useState(false);       // only the requirements an open issue is about
  const openIssues = (key: string) => (issues?.[key] || []).filter((e: any) => e.issue?.state === "open").length;
  const [tag, setTag] = useState("");                       // only the requirements with this tag (or dynamic tag)
  const [search, setSearch] = useState("");                 // only the requirements holding these words (id, title, texts, path, tags)
  // the paths folded or unfolded by a click; else a path of more than FOLD_OVER requirements is folded (not while filtering)
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const tests: any[] = data.tests || [];
  const colors: Record<string, string> = data.tag_colors || {};
  const inNode: any[] = useMemo(() => (data.requirements || []).filter((r: any) => under(r.path, node, children)), [data, node, children]);
  const tagCounts = useMemo(() => {
    const out = new Map<string, { n: number; dynamic: boolean }>();
    for (const r of inNode) {
      for (const t of r.dynamic_tags || []) out.set(`~${t}`, { n: (out.get(`~${t}`)?.n || 0) + 1, dynamic: true });
      for (const t of r.tags || []) out.set(t, { n: (out.get(t)?.n || 0) + 1, dynamic: false });
    }
    return [...out.entries()].sort(([a, x], [b, y]) => Number(y.dynamic) - Number(x.dynamic) || a.localeCompare(b));
  }, [inNode]);
  const rows: any[] = useMemo(() => {
    const words = search.toLowerCase().split(/\s+/).filter(Boolean);
    return inNode.filter((r: any) => (!onlyIssues || openIssues(r.key) > 0) && (!tag || (tag.startsWith("~") ? (r.dynamic_tags || []).includes(tag.slice(1)) : (r.tags || []).includes(tag)))
      && (!words.length || ((text) => words.every((w) => text.includes(w)))(
        [r.key, r.title, htmlToText(String(r.description || "")), r.rationale, r.path, ...(r.tags || []), ...(r.dynamic_tags || [])].join(" ").toLowerCase())));
  }, [inNode, tag, search, onlyIssues, issues]);   // eslint-disable-line react-hooks/exhaustive-deps
  const withIssues = useMemo(() => inNode.filter((r: any) => openIssues(r.key) > 0).length, [inNode, issues]);   // eslint-disable-line react-hooks/exhaustive-deps
  const groups = useMemo(() => {
    const out: [string, any[]][] = [];
    for (const r of rows) {
      const p = String(r.path || "").replace(/^\/+|\/+$/g, "");
      if (!out.length || out[out.length - 1][0] !== p) out.push([p, []]);
      out[out.length - 1][1].push(r);
    }
    return out;
  }, [rows]);
  const inView = new Set(rows.map((r) => r.key));
  const isFolded = (path: string, reqs: any[]) => toggled[path] ?? (reqs.length > FOLD_OVER && !search.trim() && !tag);
  return (
    <div className="coverage">
      <CoverageTiles rows={rows} />
      <div className="cov-tags small">
        <input type="search" className="cov-search" placeholder="Search the requirements: id, title, text, path, tag…" value={search}
          onChange={(e) => setSearch(e.target.value)} title="Only the requirements holding all these words" />
        {search && <span className="muted">{rows.length} of {inNode.length}</span>}
        {withIssues > 0 && <button className={classes("dyn-tag", onlyIssues && "selected")} onClick={() => setOnlyIssues(!onlyIssues)}
          title="Only the requirements an open issue is about: by the failure it is bound to, or naming them (issues.yml)">🐞 with open issues {withIssues}</button>}
      {tagCounts.length > 0 && (<>
          <select value={tag} onChange={(e) => setTag(e.target.value)} title="Only the requirements with this tag (⚑: a dynamic tag, found in their texts)">
            <option value="">All tags</option>
            {tagCounts.map(([t, c]) => <option key={t} value={t}>{c.dynamic ? `⚑ ${t.slice(1)}` : t} ({c.n})</option>)}
          </select>
          {tagCounts.filter(([, c]) => c.dynamic).map(([t, c]) => (
            <button key={t} className={classes("dyn-tag", tag === t && "selected")} style={tagStyle(colors[t.slice(1)])} onClick={() => setTag(tag === t ? "" : t)}
              title="A dynamic tag: requirements whose texts hold one of its words. Click to show only them.">⚑ {t.slice(1)} {c.n}</button>
          ))}
      </>)}
      </div>
      {(data.problems || []).length > 0 && <ul className="warn-list">{data.problems.map((p: string) => <li key={p}>{p}</li>)}</ul>}
      {rows.length === 0 ? <p className="muted">No requirement here.</p> : (
        <div className="scroll">
          <table className="grid compact cov-matrix">
            <thead>
              <tr>
                <th>Requirement</th><th>Title</th><th>Tags</th><th>Coverage</th>
                {tests.map((t) => {
                  const href = testHref(t);
                  return (
                    <th key={t.key} title={t.origin}>
                      {href ? <a href={href}>{t.testdef || t.key}</a> : t.testdef || t.key}
                      {t.context && <div className="muted small mono" title="The context of its requirements: the mandatory variables of their sources">{contextText(t.context)}</div>}
                      <div className="muted small mono">{t.run || "not run"}</div>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {groups.map(([path, reqs]) => {
                const c = counts(reqs);
                return (
                  <Fragment key={path}>
                    <tr className="cov-node" onClick={() => setToggled({ ...toggled, [path]: !isFolded(path, reqs) })}
                      title={isFolded(path, reqs) ? `Show its ${reqs.length} requirements` : "Fold it"}>
                      <td colSpan={4 + tests.length}><span className="fold">{isFolded(path, reqs) ? "▸" : "▾"}</span> <code>{path || "(no path)"}</code> <span className="muted small">{reqs.length} · {STATUSES.filter((s) => c[s]).map((s) => `${s} ${c[s]}`).join(" · ")}</span></td></tr>
                    {!isFolded(path, reqs) && reqs.map((r) => (
                      <Fragment key={r.key}>
                        <tr className={classes("cov-row", open === r.key && "open")} onClick={() => setOpen(open === r.key ? null : r.key)} title="Show the requirement">
                          <td className="mono nowrap">{r.key}</td>
                          {(() => { const title = r.title || htmlToText(String(r.description || "")).split("\n")[0];
                            return <td className="cov-title" title={title}><IssueBadge entries={issues?.[r.key]} /> {title}</td>; })()}
                          <td className="cov-tags-cell">{(r.dynamic_tags || []).map((t: string) => <span key={t} className="dyn-tag" style={tagStyle(colors[t])} title="dynamic tag: its texts hold one of its words">⚑ {t}</span>)}
                            {(r.tags || []).map((t: string) => <span key={t} className="tag">{t}</span>)}</td>
                          <td><CoverageBadge status={r.coverage} /></td>
                          {tests.map((t) => <td key={t.key} className="c"><CoverageBadge status={r.tests?.[t.key]?.status} changed={r.tests?.[t.key]?.changed} /></td>)}
                        </tr>
                        {open === r.key && <tr className="cov-detail"><td colSpan={4 + tests.length}><RequirementDetail req={r} sid={sid} colors={colors} />
                          <RequirementIssues entries={issues?.[r.key]} /></td></tr>}
                      </Fragment>
                    ))}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {showTests && tests.length > 0 && (
        <>
          <h4>Tests × requirements</h4>
          <div className="scroll">
          <table className="grid compact cov-tests">
            <thead><tr><th>Test</th><th>Verified by</th><th>Requirements</th></tr></thead>
            <tbody>
              {tests.map((t) => {
                const byWhere: [string, any[]][] = [];
                for (const lk of t.links || []) {
                  if (!inView.has(lk.req)) continue;
                  const last = byWhere[byWhere.length - 1];
                  if (last && last[0] === lk.where) last[1].push(lk);
                  else byWhere.push([lk.where, [lk]]);
                }
                if (!byWhere.length) return null;
                const href = testHref(t);
                return (
                  <Fragment key={t.key}>
                    <tr className="cov-node">
                      <td colSpan={2}>{href ? <a href={href}>{t.testdef || t.key}</a> : t.testdef || t.key} <span className="muted small mono">{t.run || t.origin}</span>{t.context && <span className="muted small mono" title="The context of its requirements"> · {contextText(t.context)}</span>}</td>
                      <td>{t.run ? <CoverageBadge status={{ succeeded: "passed", failed: "failed", error: "error" }[t.verdict as string] || "not run"} /> : <span className="muted small">not run</span>}</td>
                    </tr>
                    {byWhere.map(([where, links]) => {
                      const report = t.run && links[0].step && t.reports?.[links[0].step];
                      return (
                        <tr key={where}>
                          <td />
                          <td>{t.run && links[0].level === "input" && links[0].file
                            ? <a href={inputHref(t.run, links[0].file, links[0].line)} title="Open the file of the datapack at this line (Files tab of the run)"><code className="small">{where}</code></a>
                            : <code className="small">{where}</code>}
                            {report && <> · <a href={url(`runs/${encodeURIComponent(t.run)}/files/${report}`)} target="_blank" rel="noreferrer">exploit report</a></>}</td>
                          <td className="cov-links">{links.map((lk, i) => <span key={i}><span className="mono">{lk.req}</span> <CoverageBadge status={lk.status} changed={lk.changed} /></span>)}</td>
                        </tr>
                      );
                    })}
                    {(t.problems || []).map((p: string) => <tr key={p}><td /><td colSpan={2} className="warn">{p}</td></tr>)}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          </div>
        </>
      )}
    </div>
  );
}

/** The Requirements tab of a run, or the requirements section of a campaign run: their coverage, and the report. */
export function RunsCoverage({ path, events, title }: { path: string; events: string[]; title?: string }) {
  const res = useResource<any>(path, events);
  if (res.error) return <div className="error-box">{res.error}</div>;
  if (!res.data) return <Spinner className="block" text="Reading the requirements…" />;
  if (!res.data.linked) return <p className="muted">No requirement is linked: the test definition and the checks of its criteria list none (<code>reqs</code>).</p>;
  return (
    <div className="pad">
      <div className="cov-actions">
        {title && <span className="muted">{title}</span>}
        <span className="spacer" />
        <a className="button small" href={url(`${path}/report`)} target="_blank" rel="noreferrer" title="Both matrices as a page of their own">Report</a>
        <a className="button small" href={url(`${path}/report?download=true`)} title="Both matrices as a self-contained HTML file">Download</a>
      </div>
      <CoverageView data={res.data} />
    </div>
  );
}

function TreeNode({ node, prefix, rows, selected, onSelect, depth, unfolded, onFold }: { node: any; prefix: string; rows: any[]; selected: string; onSelect: (p: string) => void; depth: number;
  unfolded: Set<string>; onFold: (p: string) => void }) {
  const path = prefix ? `${prefix}/${node.id}` : node.id;
  const kids = node.children || [];
  // folded unless unfolded, or on the way to the node picked
  const open = unfolded.has(path) || (selected !== path && selected.startsWith(path + "/"));
  const mine = rows.filter((r) => under(r.path, path, true));
  const c = counts(mine);
  const worst = STATUSES.find((s) => ["failed", "error", "uncovered", "partial", "skipped", "not run"].includes(s) && c[s]) || (mine.length ? "passed" : null);
  return (
    <>
      <button className={classes("req-tree-item", selected === path && "active")} style={{ paddingLeft: 8 + (depth - 1) * 14 }} onClick={() => onSelect(path)}
        title={`${path}: ${STATUSES.filter((s) => c[s]).map((s) => `${s} ${c[s]}`).join(", ") || "no requirement"}`}>
        <span className="req-tree-fold" onClick={(e) => { if (kids.length) { e.stopPropagation(); onFold(path); } }}
          title={kids.length ? (open ? "Fold it" : `Unfold it: ${kids.length} below`) : undefined}>{kids.length ? (open ? "▾" : "▸") : ""}</span>
        <span>{node.label && node.label !== node.id ? node.label : node.id}</span>
        <span className="muted small">{mine.length}</span>
        {worst && <span className={classes("cov-dot", worst.replace(" ", "-"))} />}
      </button>
      {open && kids.map((ch: any) => <TreeNode key={ch.id} node={ch} prefix={path} rows={rows} selected={selected} onSelect={onSelect} depth={depth + 1} unfolded={unfolded} onFold={onFold} />)}
    </>
  );
}

function allPaths(nodes: any[], prefix = ""): string[] {
  return (nodes || []).flatMap((n: any) => { const p = prefix ? `${prefix}/${n.id}` : n.id; return [p, ...allPaths(n.children, p)]; });
}

/** How the reading of the requirements goes, while the page waits for it: the sources read, the calls to their servers. */
function ReadingProgress({ sid }: { sid: string }) {
  const [p, setP] = useState<any>(null);
  useEffect(() => {
    let live = true;
    const tick = () => get(`edit/sessions/${sid}/requirements/progress`).then((r) => { if (live) setP(r); }).catch(() => undefined);
    const first = window.setTimeout(tick, 400);
    const timer = window.setInterval(tick, 800);
    return () => { live = false; window.clearTimeout(first); window.clearInterval(timer); };
  }, [sid]);
  if (!p || p.done || (!p.steps.length && !p.calls)) return null;
  return (
    <div className="req-progress small">
      <Spinner text={`Reading the requirements: ${p.seconds} s, ${p.calls} call(s) to the servers`} />
      <ul className="muted">{p.steps.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
      {p.last_call && <div className="mono muted req-progress-call" title={p.last_call}>{p.last_call}</div>}
    </div>
  );
}

const INTRO = `The requirements of this repository (\`requirements.yml\`), and how its test definitions cover them: each test definition by its latest run on this sequencer, else as linked. Test definitions and the checks of their criteria list the requirements they verify with \`reqs\`.`;

/** Datapacks, Requirements: the tree of the requirements of the repository, and their coverage. */
const STARTER = `# Requirements of the tests (built-in YAML source 'reqs', sequencer README section 10.3).
# Test definitions and checks of the criteria list those they verify: reqs: [REQ-1]
nodes:
  SYS: System
requirements:
  REQ-1:
    path: SYS
    title: A first requirement
    description: What is required.
`;

export function RequirementsTab({ sid, ctx }: { sid: string; ctx: any }) {
  const [source, setSource] = useState<string | null>(null);
  const [campaign, setCampaign] = useState<string>("");
  const [node, setNode] = useState("");
  const [unfolded, setUnfolded] = useState<Set<string>>(new Set());   // the tree nodes unfolded: all folded at first
  const [children, setChildren] = useState(true);
  const q = new URLSearchParams();
  if (source) q.set("source", source);
  if (campaign) q.set("campaign", campaign);
  const path = `edit/sessions/${sid}/requirements${q.toString() ? "?" + q : ""}`;
  const res = useResource<any>(path, ["run"]);
  // Refresh: read the sources again, the rest sources without their cache (even what is still in its time)
  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {
    setRefreshing(true);
    try { res.setData(await get(`${path}${path.includes("?") ? "&" : "?"}refresh=true`)); }
    catch (e: any) { notify(`The refresh failed: ${e.message}`, "error"); }
    finally { setRefreshing(false); }
  };
  const refreshButton = (
    <button disabled={refreshing || res.loading} onClick={refresh}
      title="Read the sources again now, without the cache of the rest sources (even the answers still within their cache time)">
      {refreshing ? "Refreshing…" : "↻ Refresh"}</button>
  );
  const campaigns: any[] = ctx?.workspace?.campaigns || [];
  const d = res.data;
  // the sources, as cards: the one picked (none: every source) shows its tree below them; a click on it again unpicks it
  const cards: any[] = d?.source_cards || [];
  const pick = (name: string | null) => { setSource(name); setNode(""); };
  const picker = cards.length > 0 && (
    <div className="req-sources">
      {cards.length > 1 && (
        <button className={classes("testdef-item", !d.source && "active")} onClick={() => pick(null)} title="The requirements of every source">
          <strong>All sources</strong>
          <span className="muted small">{cards.length} sources</span>
        </button>
      )}
      {cards.map((c: any) => (
        <button key={c.name} className={classes("testdef-item", c.name === d.source && "active")}
          onClick={() => pick(c.name === d.source && cards.length > 1 ? null : c.name)}
          title={c.name === d.source && cards.length > 1 ? "Show every source again" : `Only the requirements of the source '${c.name}'`}>
          <strong>{c.label || c.name}</strong>
          <span className="muted small mono">{c.label && c.label !== c.name ? `${c.name} · ` : ""}{c.backend}</span>
          {c.default && <span className="muted small" title="A reference with no source prefix (REQ-1, not reqs:REQ-1) is to this source">default source</span>}
        </button>
      ))}
    </div>
  );
  // a button to edit the source shown in the Editor (its YAML requirements, or its entry in the sources file)
  const edit = d?.edit && (
    <button className="edit-testdef" onClick={() => openInNewTab({ path: `edit/${sid}`, query: { file: d.edit.file, ...(d.edit.source ? { source: d.edit.source } : {}) } })}
      title={d.edit.source ? `Edit the source '${d.edit.source}' (its backend, options, credentials) in the Editor, in a new browser tab`
        : d.source ? `Edit the requirements of '${d.source}' (${d.edit.file}) in the Editor, in a new browser tab` : `Edit the sources (${d.edit.file}) in the Editor, in a new browser tab`}><NavIcon name="edit" size={16} />Edit</button>
  );
  // the report shows what the page shows: the source (or every source), the campaign, and the node picked
  const rq = new URLSearchParams(q);
  if (node) { rq.set("node", node); if (!children) rq.set("children", "false"); }
  const reportQuery = rq.toString();
  const rows: any[] = d?.coverage?.requirements || [];
  return (
    <div className="requirements-tab">
      <Markdown text={INTRO} className="intro" />
      {res.error && <div className="error-box">{res.error}</div>}
      {!d && !res.error && <Spinner className="block" text="Reading the requirements…" />}
      {(res.loading || refreshing) && <ReadingProgress sid={sid} />}
      {d && !d.sources_file && (
        <div className="muted">
          <p>No <code>requirements.yml</code> in this repository: write one at its root, with the requirements themselves (<code>requirements:</code>) or the sources they come from (<code>sources:</code>). See the sequencer README, section 10.3.</p>
          <button className="primary" onClick={async () => {
            const ok = await attempt(() => createFile(sid, "requirements.yml", undefined, STARTER), "requirements.yml created");
            if (ok !== undefined) { res.refresh(); openInNewTab({ path: `edit/${sid}`, query: { file: "requirements.yml" } }); }
          }}>Create requirements.yml…</button>
        </div>
      )}
      {d?.error && (
        <div className="requirements-layout">
          <nav className="req-tree">{picker}</nav>
          <div className="req-main">
            <div className="synthesis-head"><span className="spacer" /><div className="synthesis-actions">{refreshButton}{edit}</div></div>
            <div className="error-box">{d.error}</div>
          </div>
        </div>
      )}
      {d?.coverage && (
        <div className="requirements-layout">
          <nav className="req-tree">
            {picker}
            <div className="req-tree-head muted small">{d.source ? <>Tree of <code>{d.source}</code></> : "Tree of every source"}
              <span className="grow" />
              <button className="link small" title="Unfold every node" onClick={() => setUnfolded(new Set(allPaths(d.tree)))}>unfold all</button>
              <button className="link small" title="Fold every node" onClick={() => setUnfolded(new Set())}>fold all</button></div>
            <button className={classes("req-tree-item", node === "" && "active")} onClick={() => setNode("")}>
              <span>All requirements</span><span className="muted small">{rows.length}</span>
            </button>
            {d.tree.map((n: any) => <TreeNode key={n.id} node={n} prefix="" rows={rows} selected={node} onSelect={setNode} depth={1}
              unfolded={unfolded} onFold={(p) => setUnfolded((u) => { const n2 = new Set(u); if (n2.has(p)) n2.delete(p); else n2.add(p); return n2; })} />)}
            <p className="muted small">From <code>{d.sources_file}</code>
              {!d.sources_file.startsWith("/") && <> <button className="link small" title="Edit the sources and the requirements in the Editor, in a new browser tab"
                onClick={() => openInNewTab({ path: `edit/${sid}`, query: { file: d.sources_file } })}>✎ Edit…</button></>}</p>
          </nav>
          <div className="req-main">
            <div className="synthesis-head">
              <div>
                <h3 className={node ? "mono" : undefined}>{node || "All requirements"}</h3>
                <div className="muted small">{d.source ? <>source <code>{d.source}</code></> : "every source"}{d.edit && <> · <code>{d.edit.file}</code></>}</div>
              </div>
              <span className="spacer" />
              <div className="synthesis-actions">
                {refreshButton}
                {edit}
                <a className="button primary" href={url(`edit/sessions/${sid}/requirements/report?${reportQuery}`)} target="_blank" rel="noreferrer"
                  title="Both matrices of what is shown here (the source, the campaign and the node picked) as a page of their own, in a new browser tab"><NavIcon name="requirements" size={16} />Report</a>
              </div>
            </div>
            <div className="cov-actions">
              <label title="Only the test definitions of a campaign">Tests <select value={campaign} onChange={(e) => setCampaign(e.target.value)}>
                <option value="">every test definition</option>
                {campaigns.map((c) => <option key={c.path} value={c.path}>campaign {c.name || c.id || c.path}</option>)}
              </select></label>
              {node && <label title="The requirements of the descendants of the node too"><input type="checkbox" checked={children} onChange={(e) => setChildren(e.target.checked)} /> with sub-nodes</label>}
            </div>
            {Object.entries(d.issues_errors || {}).map(([n, e]: any) => <div key={n} className="warn small">issues of {n} not read: {e}</div>)}
            <CoverageView data={d.coverage} node={node} children={node ? children : true} sid={sid} issues={d.issues} />
          </div>
        </div>
      )}
    </div>
  );
}

/** The context of the requirements of a test: `alm: branch=main, version=3.2`, a source after another. */
export function contextText(context: Record<string, Record<string, unknown>>): string {
  return Object.entries(context).map(([source, values]) =>
    `${source}: ${Object.entries(values).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(", ")}`).join("; ");
}
