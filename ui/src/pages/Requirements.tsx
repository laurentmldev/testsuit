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
import { Fragment, useMemo, useState } from "react";
import { url, useResource } from "../api";
import { createFile } from "../editor/Session";
import { attempt, classes, Markdown, openInNewTab, Spinner } from "../util";

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
      {changed && <span className="cov-changed" title="The requirement changed since the version this test verified">changed since</span>}
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

function RequirementDetail({ req }: { req: any }) {
  return (
    <div className="req-detail">
      {req.title && <strong>{req.title}</strong>}
      <Markdown text={req.description} />
      {req.rationale && <p className="small"><span className="muted">Rationale:</span> {req.rationale}</p>}
      {req.comments && <p className="small"><span className="muted">Comments:</span> {req.comments}</p>}
      <div className="small muted req-facts">
        {req.source && <span>source <code>{req.source}</code></span>}
        {req.version && <span>version <code>{req.version}</code></span>}
        {req.pinned && <span>pinned <code>{req.pinned}</code></span>}
        {req.status && <span>status {req.status}</span>}
        {(req.tags || []).length > 0 && <span>{req.tags.map((t: string) => <span key={t} className="chip">{t}</span>)}</span>}
        {req.url && <a href={req.url} target="_blank" rel="noreferrer">open in its tool ↗</a>}
      </div>
    </div>
  );
}

function testHref(t: any) {
  return t.run ? `#/runs/${encodeURIComponent(t.run)}?tab=requirements` : null;
}

/** A coverage: its counts, both matrices. node / children: only the requirements under a node of the tree. */
export function CoverageView({ data, node = "", children = true, showTests = true }: { data: any; node?: string; children?: boolean; showTests?: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const tests: any[] = data.tests || [];
  const rows: any[] = useMemo(() => (data.requirements || []).filter((r: any) => under(r.path, node, children)), [data, node, children]);
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
  return (
    <div className="coverage">
      <CoverageTiles rows={rows} />
      {(data.problems || []).length > 0 && <ul className="warn-list">{data.problems.map((p: string) => <li key={p}>{p}</li>)}</ul>}
      {rows.length === 0 ? <p className="muted">No requirement here.</p> : (
        <div className="scroll">
          <table className="grid compact cov-matrix">
            <thead>
              <tr>
                <th>Requirement</th><th>Title</th><th>Coverage</th>
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
                    <tr className="cov-node"><td colSpan={3 + tests.length}><code>{path || "(no path)"}</code> <span className="muted small">{STATUSES.filter((s) => c[s]).map((s) => `${s} ${c[s]}`).join(" · ")}</span></td></tr>
                    {reqs.map((r) => (
                      <Fragment key={r.key}>
                        <tr className={classes("cov-row", open === r.key && "open")} onClick={() => setOpen(open === r.key ? null : r.key)} title="Show the requirement">
                          <td className="mono">{r.key}</td>
                          <td>{r.title || String(r.description || "").split("\n")[0]}</td>
                          <td><CoverageBadge status={r.coverage} /></td>
                          {tests.map((t) => <td key={t.key} className="c"><CoverageBadge status={r.tests?.[t.key]?.status} changed={r.tests?.[t.key]?.changed} /></td>)}
                        </tr>
                        {open === r.key && <tr className="cov-detail"><td colSpan={3 + tests.length}><RequirementDetail req={r} /></td></tr>}
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
                          <td><code className="small">{where}</code>{report && <> · <a href={url(`runs/${encodeURIComponent(t.run)}/files/${report}`)} target="_blank" rel="noreferrer">exploit report</a></>}</td>
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

function TreeNode({ node, prefix, rows, selected, onSelect, depth }: { node: any; prefix: string; rows: any[]; selected: string; onSelect: (p: string) => void; depth: number }) {
  const path = prefix ? `${prefix}/${node.id}` : node.id;
  const mine = rows.filter((r) => under(r.path, path, true));
  const c = counts(mine);
  const worst = STATUSES.find((s) => ["failed", "error", "uncovered", "partial", "skipped", "not run"].includes(s) && c[s]) || (mine.length ? "passed" : null);
  return (
    <>
      <button className={classes("req-tree-item", selected === path && "active")} style={{ paddingLeft: 8 + depth * 14 }} onClick={() => onSelect(path)}
        title={`${path}: ${STATUSES.filter((s) => c[s]).map((s) => `${s} ${c[s]}`).join(", ") || "no requirement"}`}>
        <span>{node.label && node.label !== node.id ? node.label : node.id}</span>
        <span className="muted small">{mine.length}</span>
        {worst && <span className={classes("cov-dot", worst.replace(" ", "-"))} />}
      </button>
      {(node.children || []).map((ch: any) => <TreeNode key={ch.id} node={ch} prefix={path} rows={rows} selected={selected} onSelect={onSelect} depth={depth + 1} />)}
    </>
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
  const [children, setChildren] = useState(true);
  const q = new URLSearchParams();
  if (source) q.set("source", source);
  if (campaign) q.set("campaign", campaign);
  const path = `edit/sessions/${sid}/requirements${q.toString() ? "?" + q : ""}`;
  const res = useResource<any>(path, ["run"]);
  const campaigns: any[] = ctx?.workspace?.campaigns || [];
  const d = res.data;
  const rows: any[] = d?.coverage?.requirements || [];
  return (
    <div className="requirements-tab">
      <Markdown text={INTRO} className="intro" />
      {res.error && <div className="error-box">{res.error}</div>}
      {!d && !res.error && <Spinner className="block" text="Reading the requirements…" />}
      {d && !d.sources_file && (
        <div className="muted">
          <p>No <code>requirements.yml</code> in this repository: write one at its root, with the requirements themselves (<code>requirements:</code>) or the sources they come from (<code>sources:</code>). See the sequencer README, section 10.3.</p>
          <button className="primary" onClick={async () => {
            const ok = await attempt(() => createFile(sid, "requirements.yml", undefined, STARTER), "requirements.yml created");
            if (ok !== undefined) { res.refresh(); openInNewTab({ path: `edit/${sid}`, query: { file: "requirements.yml" } }); }
          }}>Create requirements.yml…</button>
        </div>
      )}
      {d?.error && <div className="error-box">{d.error}</div>}
      {d?.coverage && (
        <div className="requirements-layout">
          <nav className="req-tree">
            {d.sources.length > 1 && (
              <select value={d.source} onChange={(e) => { setSource(e.target.value); setNode(""); }} title="The source of the requirements">
                {d.sources.map((s: string) => <option key={s} value={s}>{s}</option>)}
              </select>
            )}
            <button className={classes("req-tree-item", node === "" && "active")} onClick={() => setNode("")}>
              <span>All requirements</span><span className="muted small">{rows.length}</span>
            </button>
            {d.tree.map((n: any) => <TreeNode key={n.id} node={n} prefix="" rows={rows} selected={node} onSelect={setNode} depth={1} />)}
            <p className="muted small">From <code>{d.sources_file}</code>
              {!d.sources_file.startsWith("/") && <> <button className="link small" title="Edit the sources and the requirements in the Editor, in a new browser tab"
                onClick={() => openInNewTab({ path: `edit/${sid}`, query: { file: d.sources_file } })}>✎ Edit…</button></>}</p>
          </nav>
          <div className="req-main">
            <div className="cov-actions">
              <label title="Only the test definitions of a campaign">Tests <select value={campaign} onChange={(e) => setCampaign(e.target.value)}>
                <option value="">every test definition</option>
                {campaigns.map((c) => <option key={c.path} value={c.path}>campaign {c.name || c.id || c.path}</option>)}
              </select></label>
              {node && <label title="The requirements of the descendants of the node too"><input type="checkbox" checked={children} onChange={(e) => setChildren(e.target.checked)} /> with sub-nodes</label>}
              <span className="spacer" />
              <a className="button small" href={url(`edit/sessions/${sid}/requirements/report${q.toString() ? "?" + q : ""}`)} target="_blank" rel="noreferrer" title="Both matrices as a page of their own">Report</a>
            </div>
            <h3 className="mono">{node || "All requirements"}</h3>
            <CoverageView data={d.coverage} node={node} children={node ? children : true} />
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
