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

// Datapacks (sequencer README, section 13.2): the test definitions of the tests repositories, what each one
// is made of (logical configuration, input files, dictionaries), its baseline, and generating its datapack.
import { Fragment, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { del, get, post, url, useResource } from "../api";
import { GenerateDatapack } from "../editor/Generate";
import { CampaignsTab } from "./Campaigns";
import { RequirementsTab } from "./Requirements";
import { NavIcon } from "../navicons";
import { KeysExplorer } from "../editor/KeysExplorer";
import { CredentialsFields, RepoPushDialog, splitPatch, useCredentials } from "../editor/Publish";
import { DiffView } from "../monaco";
import { WorkspacePicker } from "../editor/workspace";
import { RunGraph } from "./Run";
import { Named } from "../kinds";
import { Dialog, Field, Section, Text } from "../fields";
import { attempt, classes, Markdown, navigate, notify, openInNewTab, setQuery, Spinner, type Route } from "../util";

const INTRO = `Each **test definition** gives one datapack: pick one to see what it is made of, update the versions it is built from, and generate it. The files are read and changed where the sequencer reads them (in place); **Edit workspace files…** (on the right of the workspace bar) opens them in the Editor in a new browser tab, where they can be reviewed and pushed; **Edit** beside **Generate…** opens the test definition picked there.`;

/** The in-place editing session of a tests repository: opened once, then reused. */
function useWorkspace(repo: string | null) {
  const [sid, setSid] = useState<string | null>(null);
  const [ctx, setCtx] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);       // read again (after a pull, or files changed meanwhile)
  useEffect(() => {
    setSid(null);
    setCtx(null);
    setError(null);
    if (!repo) return;
    let live = true;
    post("edit/sessions", { repo, local: true })
      .then(async (s: any) => {
        const c = await get(`edit/sessions/${s.id}/context`);
        if (live) {
          setSid(s.id);
          setCtx(c);
        }
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [repo, version]);
  return { sid, ctx, error, reread: () => setVersion((v) => v + 1) };
}

export function DatapacksPage({ route }: { route: Route }) {
  const home = useResource<any>("edit");
  const repos: any[] = (home.data?.repositories || []).filter((r: any) => r.kind !== "lab");
  const repo = route.query.get("repo") || repos[0]?.id || null;
  const { sid, ctx, error, reread } = useWorkspace(repo);
  const current = repos.find((r) => r.id === repo);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [syncSlot, setSyncSlot] = useState<HTMLElement | null>(null);   // where the git state and actions go, in the bar
  const testdefs: any[] = ctx?.workspace?.testdefs || [];
  const selected = route.query.get("def") || testdefs[0]?.path || null;
  const [config, setConfig] = useState<string | null>(null);
  useEffect(() => setConfig(null), [selected]);
  const view = ["campaigns", "requirements"].includes(route.query.get("view") || "") ? route.query.get("view") as string : "testdefs";
  const campaigns: any[] = ctx?.workspace?.campaigns || [];

  if (home.error) return <div className="page"><div className="error-box">{home.error}</div></div>;
  return (
    <div className="page datapacks-page">
      <div className="page-head">
        <h2>Datapacks</h2>
        <button className="small" onClick={() => setAdding(true)} title="Clone another tests repository from its git URL, next to those of server.workspaces">Add repository…</button>
      </div>
      {repos.length > 0 && <WorkspacePicker repositories={repos} value={repo} onChange={(id) => navigate("datapacks", { repo: id })} />}
      {current && (
        <div className="workspace-actions" title={`What these act on: the workspace '${current.id}' only`}>
          <a className="button small" href={url(`edit/repositories/${encodeURIComponent(current.id)}/archive`)} download
             title="Download this workspace's folder as it is now, local changes included, as a zip (without its git history)">Download files</a>
          {current.added
            ? <button className="small" onClick={() => setRemoving(true)} title="Remove this repository, added from its URL: its clone on the sequencer host. Only once it is clean (nothing to commit or push).">Remove…</button>
            : <button className="small" disabled title="This workspace is set in the server configuration (server.workspaces of tsq.yml): remove it there. Only the repositories added with Add repository… are removed from here.">Remove…</button>}
          <span className="repo-sync-slot" ref={setSyncSlot} />
          <button className="small edit-workspace" disabled={!sid} onClick={() => openInNewTab({ path: `edit/${sid}` })} title="Open this workspace's test definitions, setups, dictionaries and templates in the Editor, in a new browser tab">
            <NavIcon name="edit" size={14} />Edit workspace files…</button>
        </div>
      )}
      {current && (
        <div className="tabs subtabs datapacks-subtabs">
          <button className={classes(view === "testdefs" && "selected")} onClick={() => setQuery("view", null)}
            title="One test definition at a time: what it is made of, its baseline, generating and posting its datapack">
            <NavIcon name="testdefs" size={18} />Test definitions{ctx && <span className="count">{testdefs.length}</span>}</button>
          <button className={classes(view === "campaigns" && "selected")} onClick={() => setQuery("view", "campaigns")}
            title="Lists of test definitions generated with the same dataset versions and run together">
            <NavIcon name="campaigns" size={18} />Campaigns{ctx && <span className="count">{campaigns.length}</span>}</button>
          <button className={classes(view === "requirements" && "selected")} onClick={() => setQuery("view", "requirements")}
            title="The requirements of this repository (requirements.yml) and how its test definitions cover them">
            <NavIcon name="requirements" size={18} />Requirements</button>
        </div>
      )}
      {view === "testdefs" && <Markdown text={INTRO} className="intro" />}
      {repo && sid && ctx && <RepositoryBanners key={repo} repo={repo} onReread={reread} slot={syncSlot} />}
      {!home.data && <Spinner className="block" text="Reading the repositories…" />}
      {home.data && repos.length === 0 && <p className="muted">No tests repository: <strong>Add repository…</strong> clones one from its git URL, or list its folder in <code>server.workspaces</code> in the configuration.</p>}
      {adding && <AddRepositoryDialog onClose={() => setAdding(false)} onAdded={(id) => { setAdding(false); home.refresh(); navigate("datapacks", { repo: id }); }} />}
      {removing && current && <RemoveRepositoryDialog repo={current} onClose={() => setRemoving(false)} onRemoved={() => { setRemoving(false); home.refresh(); navigate("datapacks", {}); }} />}
      {error && <div className="error-box">{error}</div>}
      {repo && !ctx && !error && <Spinner className="block" text="Reading the test definitions…" />}
      {ctx && sid && view === "campaigns" && <CampaignsTab sid={sid} ctx={ctx} route={route} reread={reread} />}
      {ctx && sid && view === "requirements" && <RequirementsTab sid={sid} ctx={ctx} />}
      {ctx && view === "testdefs" && (
        <div className="datapacks">
          <nav className="testdef-list">
            {testdefs.length === 0 && <p className="muted small">No test definition in this repository.</p>}
            {testdefs.map((t) => (
              <button key={t.path} className={classes("testdef-item", t.path === selected && "active")} onClick={() => setQuery("def", t.path)}>
                <strong>{t.name || t.id || t.path}</strong>
                <span className="muted small mono">{t.path}</span>
                {t.setup_config && <span className="muted small">configuration {t.setup_config}</span>}
              </button>
            ))}
          </nav>
          {sid && selected && <Synthesis key={`${sid}:${selected}`} sid={sid} ctx={ctx} testdef={selected} config={config} setConfig={setConfig} />}
        </div>
      )}
    </div>
  );
}

function Synthesis({ sid, ctx, testdef, config, setConfig }: {
  sid: string; ctx: any; testdef: string; config: string | null; setConfig: (c: string | null) => void;
}) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [generate, setGenerate] = useState(false);
  // the versions of the baseline are listed after the rest, one source at a time (a remote can be slow)
  const [listing, setListing] = useState<Record<string, { importer: string; source: string; started: number; seconds?: number; done?: boolean }>>({});
  const [started, setStarted] = useState(Date.now());
  const [, setTick] = useState(0);
  const generation = useRef(0);
  const load = async () => {
    const mine = ++generation.current;
    setStarted(Date.now());
    try {
      const found: any = await post(`edit/sessions/${sid}/synthesis`, { testdef, setup_config: config, versions: false });
      if (mine !== generation.current) return;
      setData(found);
      setError(null);
      const sources: Record<string, { importer: string; source: string; started: number }> = {};
      for (const d of found.baseline || []) if (d.versions_pending) sources[`${d.importer}|${d.source}`] = { importer: d.importer, source: d.source, started: Date.now() };
      setListing(sources);
      await Promise.all(Object.entries(sources).map(async ([key, src]) => {
        const r: any = await post(`edit/sessions/${sid}/versions`, { importer: src.importer, source: src.source }).catch((e: any) => ({ versions: null, error: e.message }));
        if (mine !== generation.current) return;
        setListing((all) => ({ ...all, [key]: { ...src, seconds: r.seconds, done: true } }));
        setData((cur: any) => cur && { ...cur, baseline: cur.baseline.map((d: any) => `${d.importer}|${d.source}` === key
          ? { ...d, versions: r.versions, versions_error: r.error, versions_pending: false } : d) });
      }));
    } catch (e: any) {
      if (mine === generation.current) setError(e.message);
    }
  };
  useEffect(() => {
    setData(null);
    setListing({});
    load();
  }, [sid, testdef, config]);
  const waiting = !data || Object.values(listing).some((l) => !l.done);
  useEffect(() => {                                  // the seconds shown while something is read
    if (!waiting) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [waiting]);
  const secs = (from: number) => `${Math.round((Date.now() - from) / 1000)} s`;
  const sources = Object.values(listing);
  const edit = (file: string) => navigate(`edit/${sid}`, { file });
  const td = data?.testdef;
  return (
    <div className="synthesis">
      <div className="synthesis-head">
        <div>
          <h3>{td?.name || testdef}</h3>
          {td && <div className="muted small"><code>{td.id}</code> · <button className="link" onClick={() => edit(testdef)}>{testdef}</button></div>}
          {td?.description && <p className="small">{td.description}</p>}
        </div>
        <span className="spacer" />
        {td && td.configurations.length > 1 && (
          <label className="small">configuration{" "}
            <select value={config || td.setup_config} onChange={(e) => setConfig(e.target.value === td.setup_config && td.own_config ? null : e.target.value)}
              title="The setup configuration the datapack is generated for (datapack --setup-config)">
              {td.configurations.map((c: string) => <option key={c} value={c}>{c}{c === td.setup_config && td.own_config ? " (its own)" : ""}</option>)}
            </select>
          </label>
        )}
        <div className="synthesis-actions">
          <button className="edit-testdef" onClick={() => openInNewTab({ path: `edit/${sid}`, query: { file: testdef } })}
            title={`Open this test definition (${testdef}) in the Editor, in a new browser tab`}><NavIcon name="edit" size={16} />Edit</button>
          <button className="primary" disabled={!data} onClick={() => setGenerate(true)}
            title="Generate the datapack of this test definition"><NavIcon name="datapacks" size={16} />Generate…</button>
        </div>
      </div>
      {error && <pre className="error-box">{error}</pre>}
      {data?.problems?.length > 0 && (
        <div className="banner warn list">
          <strong>{data.problems.length === 1 ? "A problem" : `${data.problems.length} problems`} to fix before generating</strong>
          <ul className="problems">{data.problems.map((p: string, i: number) => <li key={i}><code>{p}</code></li>)}</ul>
        </div>
      )}
      {!data && !error && <Spinner className="block" text={`Reading the definition, its setup, its dictionaries and its inputs… ${secs(started)}`} />}
      {data && sources.some((l) => !l.done) && (
        <div className="hint-box small listing">
          <Spinner text={`Listing the versions of the baseline: ${sources.filter((l) => l.done).length} of ${sources.length} sources done`} />
          <ul>
            {sources.map((l) => (
              <li key={`${l.importer}|${l.source}`} className={l.done ? "muted" : ""}>
                <span className="badge">{l.importer}</span> <code>{l.source}</code> {l.done ? `listed in ${l.seconds ?? "?"} s` : `listing… ${secs(l.started)}`}
              </li>
            ))}
          </ul>
        </div>
      )}
      {data && (
        <>
          <Baseline datasets={data.baseline} edit={edit} onChange={() => setGenerate(true)} />
          <CoveredRequirements reqs={data.requirements} edit={() => edit(testdef)} />
          <LogicalConfig data={data} edit={edit} sid={sid} config={config} />
          <PipelineGraph sid={sid} testdef={testdef} config={config} edit={edit} />
          <Criteria sid={sid} testdef={testdef} />
          <Inputs data={data} edit={edit} />
          <Dictionaries data={data} edit={edit} />
        </>
      )}
      {generate && <GenerateDatapack sid={sid} workspace={ctx.workspace} repository={ctx.repository} testdef={testdef} config={config || data?.testdef?.setup_config} onClose={() => setGenerate(false)} />}
    </div>
  );
}

/** The requirements the test definition covers (its `reqs`, and those of the checks of its criteria), as generation
 *  will snapshot them: each with its text, and what verifies it. */
function CoveredRequirements({ reqs, edit }: { reqs: any; edit: () => void }) {
  if (!reqs) return null;
  const items: any[] = reqs.items || [];
  const issues = (reqs.problems?.length || 0) + (reqs.warnings?.length || 0);
  return (
    <Section title={<>Requirements <span className="muted small">{items.length} covered{issues ? ` · ${issues} problem(s)` : ""}</span></>}
      actions={<>
        <button className="small" onClick={edit} title="Edit the requirements of the test definition">Edit…</button>
        <button className="small" onClick={() => setQuery("view", "requirements")} title="The coverage of every requirement by the test definitions and their last runs">Coverage…</button>
      </>}>
      {items.length > 0 && (
        <table className="grid compact covered-reqs">
          <thead><tr><th>Requirement</th><th>Title</th><th>Path</th><th>Status</th><th>Verified by</th></tr></thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.key} className={classes(!r.found && "bad")}>
                <td>{r.url ? <a href={r.url} target="_blank" rel="noreferrer"><code>{r.key}</code></a> : <code>{r.key}</code>}</td>
                <td>{r.found ? r.title : <span className="bad-text">not found in its source</span>}</td>
                <td className="small"><code>{r.path}</code></td>
                <td className="small">{r.status}</td>
                <td className="small">{r.by.join("; ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {[...(reqs.problems || []).map((p: string) => ["error", p]), ...(reqs.warnings || []).map((w: string) => ["warning", w])].map(([kind, text], i) => (
        <div key={i} className={kind === "error" ? "error-text small" : "warn-text small"}>{text}</div>
      ))}
    </Section>
  );
}

/** The criteria (mexploit scenarios) the test definition checks: those its exploit steps name, with the data each
 *  step reads, their unit tests and last coverage; then the scenarios of its folder that no step uses. */
function Criteria({ sid, testdef }: { sid: string; testdef: string }) {
  const [ov, setOv] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get(`edit/sessions/${sid}/criteria`).then(setOv).catch((e) => setError(e.message));
  }, [sid, testdef]);
  const folder = testdef.split("/").slice(0, -1).join("/");
  const open = (file?: string) => navigate(`criteria/${sid}`, { scope: folder || undefined, file });
  if (error) return <Section title="Criteria"><pre className="error-box">{error}</pre></Section>;
  const steps: any[] = (ov?.exploit_steps || []).filter((s: any) => s.testdef === testdef);
  const scenarios: any[] = ov?.scenarios || [];
  // the scenarios a criteria entry names: its folder, or the scenario folders under it
  const scenariosOf = (c: any) => scenarios.filter((s) => s.path === c.folder || s.path.startsWith(c.folder + "/"));
  const used = new Set(steps.flatMap((st) => st.criteria.flatMap((c: any) => scenariosOf(c).map((s) => s.path))));
  const unused = scenarios.filter((s) => s.testdef === testdef && !used.has(s.path));
  if (ov && !steps.length && !unused.length) return null;
  const where = (st: any) => st.subpipeline ? `sub-pipeline ${st.subpipeline}` : `${st.phase}${st.part === "finally" ? " (finally)" : ""}`;
  return (
    <Section title={<>Criteria <span className="muted small">{ov ? `${used.size} scenario(s) checked by ${steps.length} exploit step(s)` : ""}</span></>}
      actions={<button className="link small" onClick={() => open()}>open the criteria editor</button>}>
      {!ov && <Spinner text="Reading the criteria…" />}
      {ov && !steps.length && (
        <p className="hint-box small">No step of the pipeline checks criteria: add a <strong>check criteria</strong> step (<code>exploit</code>) after the steps that collect the outputs to check.
          The datapack holds only the criteria such steps name.</p>
      )}
      {steps.length > 0 && (
        <table className="grid compact">
          <thead><tr><th>Step</th><th>Criteria</th><th>Unit tests</th><th>Data</th></tr></thead>
          <tbody>
            {steps.flatMap((st: any) => st.criteria.map((c: any, i: number) => {
              const found = scenariosOf(c);
              return (
                <tr key={`${st.phase}|${st.subpipeline}|${st.id}|${c.path}`}>
                  {i === 0 && <td rowSpan={st.criteria.length}><strong>{st.id}</strong><div className="muted small">{where(st)}</div></td>}
                  <td>
                    {!c.exists && <span className="badge bad" title="The datapack cannot be generated: no such folder next to the test definition">not found</span>}{" "}
                    {found.length === 1 ? <button className="link mono small" onClick={() => open(found[0].files[0])}>{c.path}</button> : <code className="small">{c.path}</code>}
                    {found.length > 1 && <div className="small">{found.map((s) => <button key={s.path} className="link small" onClick={() => open(s.files[0])}>{s.name}</button>)}</div>}
                    {c.exists && !found.length && <div className="muted small">no *.mexploit.yml file there</div>}
                  </td>
                  <td>{found.map((s) => <UtestBadge key={s.path} ov={ov} scenario={s} />)}</td>
                  {i === 0 && <td rowSpan={st.criteria.length}>{st.data.map((d: string) => <div key={d}><code className="small">{d}</code></div>)}</td>}
                </tr>
              );
            }))}
          </tbody>
        </table>
      )}
      {unused.length > 0 && (
        <p className="muted small">Not checked by this pipeline (left out of the datapack): {unused.map((s, i) => (
          <span key={s.path}>{i > 0 && ", "}<button className="link small" onClick={() => open(s.files[0])}>{s.path.slice(folder.length + 1) || s.name}</button></span>
        ))}</p>
      )}
    </Section>
  );
}

/** A scenario's unit tests and the last result of them in this server. */
function UtestBadge({ ov, scenario }: { ov: any; scenario: any }) {
  const utests = (ov.utests || []).filter((u: any) => scenario.utests.includes(u.path));
  const last = utests.find((u: any) => u.last)?.last;
  if (!utests.length) return <span className="badge warn" title="No utests.yml for this scenario: its checks are not tested">no unit tests</span>;
  if (!last) return <span className="badge">{utests.reduce((n: number, u: any) => n + (u.cases?.length || 0), 0)} unit-test case(s), not run in this server</span>;
  return <span className={classes("badge", last.ok && last.fully_covered ? "ok" : last.ok ? "warn" : "bad")}>
    {last.cases_ok}/{last.cases} case(s) as expected, {last.fully_covered ? "every check covered" : `${Object.values(last.coverage).filter((c) => c !== "covered").length} check(s) not covered`}</span>;
}

function FileLink({ path, edit }: { path?: string | null; edit: (f: string) => void }) {
  if (!path) return null;
  if (path.startsWith("/") || path.startsWith("<")) return <code className="small">{path}</code>;
  return <button className="link mono small" onClick={() => edit(path)}>{path}</button>;
}

/** The pipeline of the test definition as the sequencer will run it (sub-pipelines expanded), read-only. */
function PipelineGraph({ sid, testdef, config, edit }: { sid: string; testdef: string; config: string | null; edit: (f: string) => void }) {
  const [flat, setFlat] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setFlat(null);
    setError(null);
    post(`edit/sessions/${sid}/flatten`, { testdef, setup_config: config }).then(setFlat).catch((e) => setError(e.message));
  }, [sid, testdef, config]);
  // the shape of a run's pipeline: each step's target and condition at its top level
  const pipeline = flat && {
    phases: flat.phases.map((ph: any) => ({
      ...ph, steps: ph.steps.map((s: any) => ({ ...s, slot: s.step.slot, for_each: s.step.for_each, if: s.step.if })),
    })),
  };
  const steps = pipeline ? pipeline.phases.reduce((n: number, ph: any) => n + ph.steps.length, 0) : 0;
  return (
    <Section title={<>Pipeline <span className="muted small">{pipeline ? `${pipeline.phases.length} phase(s), ${steps} steps, sub-pipelines expanded` : ""}</span></>}
      actions={<button className="link small" onClick={() => edit(testdef)}>edit</button>}>
      {error && <pre className="error-box">{error}</pre>}
      {!flat && !error && <Spinner text="Expanding the pipeline…" />}
      {pipeline && <div className="pipeline-preview"><RunGraph pipeline={pipeline} records={[]} selected={null} onSelect={() => {}} /></div>}
    </Section>
  );
}

function LogicalConfig({ data, edit, sid, config }: { data: any; edit: (f: string) => void; sid: string; config: string | null }) {
  const td = data.testdef;
  const [keysOf, setKeysOf] = useState<string | null>(null);
  return (
    <Section title={<>Logical configuration <span className="muted small">{td.setup_config} of {data.setup?.name || data.setup?.id}</span></>}
      actions={<FileLink path={td.setup_file} edit={edit} />}>
      {Object.keys(data.stage_slots).length > 0 && (
        <p className="small">Stage slots: {Object.entries(data.stage_slots).map(([s, t]: [string, any]) => <span key={s} className="chip"><Named kind="stage_slot" name={s} />: <Named kind="stage_type" name={t} /></span>)}</p>
      )}
      <table className="grid compact">
        <thead><tr><th><Named kind="role" name="">Role</Named></th><th><Named kind="slot" name="">Slot</Named></th><th><Named kind="slot_class" name="">Slot class</Named></th><th>Configuration</th><th><Named kind="stage_slot" name="">Stage slot</Named></th><th>Needs</th><th /></tr></thead>
        <tbody>
          {data.slots.map((c: any) => (
            <tr key={c.id}>
              <td>{c.role ? <Named kind="role" name={c.role} /> : <span className="muted">none</span>}</td>
              <td><strong>{c.id}</strong><div className="muted small">{c.name}</div></td>
              <td><code>{c.slot_class}</code></td>
              <td>{c.configuration}</td>
              <td>{c.stage_slot || <span className="muted small">ad hoc</span>}</td>
              <td>{c.needs.join(", ")}</td>
              <td><button className="small" onClick={() => setKeysOf(c.id)}
                title="Browse the keys its commands and its template files can use, with the lab component it would be bound to now">Keys…</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {keysOf && <KeysExplorer sid={sid} testdef={td.path} config={config} slot={keysOf} onClose={() => setKeysOf(null)} />}
    </Section>
  );
}

function Inputs({ data, edit }: { data: any; edit: (f: string) => void }) {
  const count = data.slots.reduce((n: number, c: any) => n + c.inputs.length, 0);
  return (
    <Section title={<>Template and input files <span className="muted small">{count} inputs</span></>}>
      <table className="grid compact">
        <thead><tr><th>Slot</th><th>Target</th><th>Method</th><th>Source</th></tr></thead>
        <tbody>
          {data.slots.map((c: any) => c.inputs.map((i: any, n: number) => (
            <tr key={`${c.id}:${n}`}>
              {n === 0 && <td rowSpan={c.inputs.length}><strong>{c.id}</strong></td>}
              <td className="mono small">{i.target}</td>
              <td><span className={classes("badge", i.method === "template" && "info")}>{i.method}{i.generator ? `: ${i.generator}` : ""}</span>{i.optional && <span className="muted small"> optional</span>}</td>
              <td>
                {i.dataset ? <span className="small">dataset <strong>{i.dataset}</strong> <code>{i.source}</code></span>
                  : i.files.length ? i.files.map((f: string) => <div key={f}><FileLink path={f} edit={edit} /></div>)
                    : <span className="small"><code>{i.source}</code> <span className="badge warn">not found</span></span>}
              </td>
            </tr>
          )))}
        </tbody>
      </table>
    </Section>
  );
}

function Dictionaries({ data, edit }: { data: any; edit: (f: string) => void }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <Section title={<>Dictionaries <span className="muted small">lowest precedence first</span></>}>
      <table className="grid compact">
        <thead><tr><th>Level</th><th>File</th><th>Keys</th></tr></thead>
        <tbody>
          {data.dictionaries.map((d: any) => (
            <Fragment key={d.path}>
              <tr>
                <td>{d.layer === "testdef" ? "test definition" : d.layer}</td>
                <td><FileLink path={d.path} edit={edit} /></td>
                <td>
                  <button className="link small" onClick={() => setOpen({ ...open, [d.path]: !open[d.path] })}>
                    {open[d.path] ? "▾" : "▸"} {d.keys.length} keys
                  </button>
                  {d.keys.some((k: any) => k.overridden_by) && <span className="muted small"> ({d.keys.filter((k: any) => k.overridden_by).length} overridden)</span>}
                </td>
              </tr>
              {open[d.path] && (
                <tr><td /><td colSpan={2}>
                  <table className="grid compact keys">
                    <tbody>
                      {d.keys.map((k: any) => (
                        <tr key={k.key} className={k.overridden_by ? "off" : ""} title={k.overridden_by ? `overridden by ${k.overridden_by}` : ""}>
                          <td className="mono small">{k.key}</td><td className="mono small">{k.value}</td>
                          <td className="muted small">{k.overridden_by && `overridden by ${k.overridden_by}`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

/** The baseline of the test definition, read-only: a version is changed for one generation in the Generate form,
 *  in the files in the Editor. */
function Baseline({ datasets, edit, onChange }: { datasets: any[]; edit: (f: string) => void; onChange: () => void }) {
  return (
    <Section title={<>Baseline <span className="muted small">versions of the data it is built from</span></>}
      actions={datasets.length > 0 ? <button className="link small" onClick={onChange}>generate with other versions…</button> : undefined}>
      {datasets.length === 0 && <p className="muted small">This setup imports no dataset.</p>}
      {datasets.length > 0 && (
        <table className="grid compact baseline">
          <thead><tr><th>Dataset</th><th>From</th><th>Version</th><th>Written in</th></tr></thead>
          <tbody>
            {datasets.map((d) => (
              <tr key={d.id}>
                <td><strong>{d.id}</strong><div className="muted small">{d.name}</div></td>
                <td className="small"><span className="badge">{d.importer}</span> <code title={d.source}>{d.source}</code>
                  {d.versions_pending && <div className="muted small"><Spinner text="listing its versions…" /></div>}
                  {d.versions_error && <div className="muted small" title={d.versions_error}>versions not listed: {d.versions_error}</div>}</td>
                <td>
                  <code>{d.version ?? d.written}</code>
                  {d.versions && <div className="muted small">{d.versions.length} available{d.versions[0] && d.versions[0] !== d.version ? `, latest ${d.versions[0]}` : ""}</div>}
                </td>
                <td className="small">
                  {d.key ? <>key <code>{d.key}</code> in </> : null}<FileLink path={d.defined_in} edit={edit} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted small">To generate with other versions, use <strong>Generate…</strong> (<strong>Baseline versions</strong>): for that generation only; the datapack stays conform when each is a tag of its source. To change a version in the files, open them in the Editor (<strong>Edit</strong> beside <strong>Generate…</strong>, <strong>Edit workspace files…</strong>, or the file under <em>Written in</em>).</p>
    </Section>
  );
}

// ── tests repositories: added by git URL, and the changes made meanwhile ──

/** What changed in the repository's folder since this page read it, asked every 2 s while shown, as the Lab page
 * does for the lab files: files changed elsewhere (another browser, a hand edit, a pull), local changes to commit and
 * push, and a newer revision upstream to pull. */
/** The git state of a workspace and its actions (show changes, commit and push, pull), in the workspace bar (slot);
 *  and, as a banner, files changed since the page read them. */
function RepositoryBanners({ repo, onReread, slot }: { repo: string; onReread: () => void; slot: HTMLElement | null }) {
  const [status, setStatus] = useState<any>(null);
  const [seen, setSeen] = useState<string | null>(null);      // the stamp of the files as this page read them
  const [pushing, setPushing] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [showing, setShowing] = useState(false);
  useEffect(() => {
    let stop = false;
    const check = () => {
      if (document.visibilityState !== "visible") return;
      get(`edit/repositories/${encodeURIComponent(repo)}/status`).then((s) => {
        if (stop) return;
        setStatus(s);
        setSeen((old) => old ?? s.stamp);
      }).catch(() => undefined);
    };
    check();
    const t = setInterval(check, 2000);
    document.addEventListener("visibilitychange", check);
    return () => { stop = true; clearInterval(t); document.removeEventListener("visibilitychange", check); };
  }, [repo]);
  if (!status) return null;
  const reread = () => {
    setSeen(status.stamp);
    onReread();
  };
  const changes: any[] = status.changes || [];
  const branch = status.git?.branch;
  const newer = status.published?.newer ? status.published : null;
  return (
    <>
      {seen && status.stamp !== seen && (
        <div className="banner info">
          The files of this repository changed since this page read them (another browser, a hand edit, a pull).
          <button className="small" onClick={reread}>Read them again</button>
        </div>
      )}
      {slot && createPortal(
        <>
          {changes.length > 0 && (
            <span className="chip warn" title={changes.map((c) => c.path).join("\n")}>
              {changes.length} local change{changes.length > 1 ? "s" : ""} not committed
            </span>
          )}
          {status.ahead > 0 && <span className="chip warn">{status.ahead} commit{status.ahead > 1 ? "s" : ""} not pushed</span>}
          {newer
            ? <span className="chip info" title={`by ${newer.author}: “${newer.message}”`}>newer revision pushed: {newer.tag || newer.commit.slice(0, 10)}</span>
            : status.behind > 0 && <span className="chip info" title={`The upstream branch ${branch} has commits this folder does not`}>{status.behind} new upstream commit{status.behind > 1 ? "s" : ""}</span>}
          <span className="muted small" title={`The revision of the workspace's folder${branch ? ` (branch ${branch})` : " (on no branch)"}`}>
            {!branch && <>no branch, </>}at <code>{status.git?.tag || status.git?.commit?.slice(0, 10)}</code>
          </span>
          {changes.length > 0 && <button className="small" onClick={() => setShowing(true)} title="See the local changes file by file, download them as a patch, or revert them">Show changes…</button>}
          {branch && changes.length > 0 &&
            <button className="small" onClick={() => setPushing(true)} title="Review the changes, then commit, tag and push them in your name, with your own git credentials (used for this push only, never stored)">Commit and push…</button>}
          {!branch && changes.length > 0 && <span className="muted small" title="Check a branch out to push">(no branch: cannot push)</span>}
          <button className={classes("small", (newer || status.behind > 0) && "primary")} onClick={() => setPulling(true)}
                  title={"Fetch the repository and bring the folder to the latest commit of its branch (commits pushed from elsewhere are only known after a fetch)"
                    + (changes.length ? ". The local changes stay; if upstream changed one of those files, nothing is pulled: commit and push, or revert, it first" : "")}>Pull…</button>
        </>, slot)}
      {showing && <LocalChangesDialog repo={repo} onClose={() => setShowing(false)} onChanged={reread}
                                      onPush={branch ? () => { setShowing(false); setPushing(true); } : undefined} />}
      {pushing && <RepoPushDialog repo={repo} onClose={() => { setPushing(false); reread(); }} />}
      {pulling && <PullDialog repo={repo} onClose={() => setPulling(false)} onPulled={() => { setPulling(false); reread(); }} />}
    </>
  );
}

function PullDialog({ repo, onClose, onPulled }: { repo: string; onClose: () => void; onPulled: () => void }) {
  const creds = useCredentials(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const pull = async () => {
    setBusy(true);
    try {
      const r = await post(`edit/repositories/${encodeURIComponent(repo)}/pull`, creds.value);
      if (r.ok) {
        notify(r.updated ? `pulled: now on ${r.git?.tag || r.commit.slice(0, 10)}` : "already on the latest commit");
        onPulled();
      } else setResult(r);
    } catch (e: any) {
      setResult({ ok: false, error: e.message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title="Pull the repository" onClose={onClose} footer={
      <button className="primary" disabled={busy || !creds.complete} onClick={pull}>{busy ? "Pulling…" : "Pull"}</button>
    }>
      <p>Fetches the branch of the repository and brings its folder to its latest commit. Refused while it has changes not committed or not pushed.</p>
      {result && !result.ok && <div className="banner error"><strong>Not pulled:</strong> {result.error}{result.hint && <div>{result.hint}.</div>}</div>}
      <CredentialsFields creds={creds} help="Your own, if the repository needs them to be read: used for this pull only, never stored." />
    </Dialog>
  );
}

function AddRepositoryDialog({ onClose, onAdded }: { onClose: () => void; onAdded: (id: string) => void }) {
  const [gitUrl, setGitUrl] = useState("");
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [path, setPath] = useState("");
  const creds = useCredentials(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const author = (() => {
    try {
      return JSON.parse(localStorage.getItem("tsq.author") || "{}").name || null;
    } catch {
      return null;
    }
  })();
  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      const repo = await post("edit/repositories", { url: gitUrl, name: name || null, branch: branch.trim(), path: path || null, by: author, ...creds.value });
      notify(`repository ${repo.id} added`);
      onAdded(repo.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title="Add a tests repository" onClose={onClose} footer={
      <button className="primary" disabled={busy || !gitUrl.trim() || !branch.trim() || !creds.complete} onClick={add}>{busy ? "Cloning…" : "Clone and add"}</button>
    }>
      <Markdown text="Clones a repository of datapack definitions (test definitions, setups, dictionaries, templates) on the sequencer host, next to those of `server.workspaces`. Its changes are pushed back from the Editor or from this page, always with your own credentials." />
      {error && <div className="banner error"><strong>Not added:</strong> {error}</div>}
      <Field label="Git URL" help="HTTPS or SSH URL of the repository. Give no password or token in it: those go to the credentials below." wide>
        <Text mono value={gitUrl} onChange={setGitUrl} placeholder="https://git.example.com/team/tests.git" />
      </Field>
      <div className="inline-fields">
        <Field label="Name" help="How the repository is named here (its id). Default: the last part of the URL."><Text value={name} onChange={setName} placeholder="from the URL" /></Field>
        <Field label="Branch" help="Required: the one branch cloned (single-branch), pulled and pushed to. Other branches of the repository are not fetched."><Text mono value={branch} onChange={setBranch} placeholder="main" /></Field>
        <Field label="Folder" help="The folder of the repository holding the datapack definitions. Default: the whole repository."><Text mono value={path} onChange={setPath} placeholder="whole repository" /></Field>
      </div>
      <CredentialsFields creds={creds} help="Your own, if the repository needs them to be read: used for this clone only, never stored (neither in the clone's configuration)." />
    </Dialog>
  );
}

function RemoveRepositoryDialog({ repo, onClose, onRemoved }: { repo: any; onClose: () => void; onRemoved: () => void }) {
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    get(`edit/repositories/${encodeURIComponent(repo.id)}/status`).then(setStatus).catch((e) => setError(e.message));
  }, [repo.id]);
  const clean = status && !status.changes.length && !status.ahead;
  const remove = async () => {
    setBusy(true);
    try {
      await del(`edit/repositories/${encodeURIComponent(repo.id)}`);
      notify(`repository ${repo.id} removed`);
      onRemoved();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title={`Remove ${repo.id}`} onClose={onClose} footer={
      <button className="primary" disabled={busy || !clean} onClick={remove}>Remove</button>
    }>
      <p>Removes the clone of <code>{repo.url}</code> from the sequencer host, and the editing sessions on it. The remote repository is not changed.</p>
      {!status && !error && <Spinner text="Looking for local changes…" />}
      {status && !clean && (
        <div className="banner warn">
          Not clean: {status.changes.length ? `${status.changes.length} local change${status.changes.length > 1 ? "s" : ""} not committed` : ""}
          {status.changes.length && status.ahead ? ", " : ""}{status.ahead ? `${status.ahead} commit${status.ahead > 1 ? "s" : ""} not pushed` : ""}.
          Commit and push them, or revert them, first: a repository is removed only when it is clean.
        </div>
      )}
      {error && <div className="banner error">{error}</div>}
    </Dialog>
  );
}

/** The local changes of the workspace's folder, file by file (what is not committed, against its last commit): downloaded
 * as a patch, reverted file by file or all at once. Commits are never touched here: no merge, no reset. */
function LocalChangesDialog({ repo, onClose, onChanged, onPush }: {
  repo: string; onClose: () => void; onChanged: () => void; onPush?: () => void;
}) {
  const [diff, setDiff] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);   // what a second click reverts: a file, or "all"
  const [busy, setBusy] = useState(false);
  const base = `edit/repositories/${encodeURIComponent(repo)}`;
  const load = () => get(`${base}/diff`).then((d) => {
    setDiff(d);
    setFile((f) => (d.files.some((x: any) => x.path === f) ? f : d.files[0]?.path || null));
  }).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repo]);
  const files = diff ? splitPatch(diff.patch) : {};
  // the files are given from the root of the repository; reverting takes them from the workspace's folder
  const folderOf = (path: string) => (diff?.folder && path.startsWith(`${diff.folder}/`) ? path.slice(diff.folder.length + 1) : path);
  const revert = async (what: string) => {
    if (confirm !== what) {
      setConfirm(what);
      return;
    }
    setBusy(true);
    setConfirm(null);
    const r = await attempt(() => post(`${base}/revert`, what === "all" ? {} : { paths: [folderOf(what)] }));
    setBusy(false);
    if (r) {
      notify(`reverted: ${r.reverted.length} file${r.reverted.length === 1 ? "" : "s"}`);
      onChanged();
      load();
    }
  };
  return (
    <Dialog title={`Local changes of ${repo}`} onClose={onClose} wide footer={
      <>
        {diff?.files.length > 0 && <a className="button" href={url(`${base}/diff?download=true`)} download title="The changes as a patch file, to apply at the root of the repository with git apply">Download patch</a>}
        {diff?.files.length > 0 && <button className="danger" disabled={busy} onClick={() => revert("all")}>
          {confirm === "all" ? `Click again: revert all ${diff.files.length} files` : "Revert all…"}</button>}
        {onPush && diff?.files.length > 0 && <button className="primary" onClick={onPush}>Commit and push…</button>}
      </>
    }>
      <p className="muted small">What is not committed in the workspace's folder, against its last commit{diff?.base ? <> (<code>{diff.base.slice(0, 10)}</code>{diff.branch ? <> on <code>{diff.branch}</code></> : null})</> : null}. Reverting puts a file back as committed, and deletes a new one: it cannot be undone.{diff?.ahead ? ` ${diff.ahead} commit${diff.ahead > 1 ? "s are" : " is"} not pushed: not shown here, and never reverted.` : ""}</p>
      {error && <div className="error-box">{error}</div>}
      {!diff && !error && <Spinner text="Reading the local changes…" />}
      {diff && !diff.files.length && <p>No local change: the folder is as committed.</p>}
      {diff && diff.files.length > 0 && (
        <div className="publish">
          <div className="diff-files">
            {diff.files.map((f: any) => (
              <div key={f.path} className={classes("tree-row", file === f.path && "selected")} onClick={() => { setFile(f.path); setConfirm(null); }}>
                <span className={classes("status", `st-${f.status[0]}`)}>{f.status[0]}</span> {f.path}
              </div>
            ))}
          </div>
          <div className="diff-view">
            {file && files[file] ? <DiffView key={file} original={files[file].before} modified={files[file].after} height={300} /> : <p className="muted">{file ? "binary file" : ""}</p>}
            {file && <button className="small danger" disabled={busy} onClick={() => revert(file)}>
              {confirm === file ? `Click again: revert ${file.split("/").pop()}` : "Revert this file…"}</button>}
          </div>
        </div>
      )}
    </Dialog>
  );
}
