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

// Test campaigns (sequencer README, Campaigns): the Campaigns subtab of the Datapacks page (a campaign's test
// definitions, the dataset versions it pins, its routing tags; generating and posting it whole), and the page of a
// campaign run (its runs at a glance, the synthetic report).
import { useEffect, useRef, useState } from "react";
import { get, post, put, url, useResource } from "../api";
import { Dialog, Field, IdText, Section, Text, Toggle } from "../fields";
import { LiveLog, logWarnings } from "../editor/Generate";
import { ago, attempt, Badge, classes, duration, Markdown, navigate, setQuery, Spinner, time, type Route } from "../util";

const CAMPAIGNS_INTRO = `A **campaign** is a list of test definitions generated and run together: one datapack each, all with the **same version of every dataset** they import (the campaign lists each one, no test definition chooses on its own), queued under one campaign run with their **priority** and the **tags** the lab resources must carry. Its runs are grouped under it in **All runs**, with a synthetic report.`;

/** The Campaigns subtab: the campaign files of the repository, and the one picked. */
export function CampaignsTab({ sid, ctx, route, reread }: { sid: string; ctx: any; route: Route; reread: () => void }) {
  const campaigns: any[] = ctx?.workspace?.campaigns || [];
  const selected = route.query.get("campaign") || campaigns[0]?.path || null;
  const [creating, setCreating] = useState(false);
  return (
    <>
      <Markdown text={CAMPAIGNS_INTRO} className="intro" />
      <div className="datapacks">
        <nav className="testdef-list">
          {campaigns.length === 0 && <p className="muted small">No campaign in this repository yet: a campaign is a <code>*.campaign.yml</code> file.</p>}
          {campaigns.map((c) => (
            <button key={c.path} className={classes("testdef-item", c.path === selected && "active")} onClick={() => setQuery("campaign", c.path)}>
              <strong>{c.name || c.id || c.path}</strong>
              <span className="muted small mono">{c.path}</span>
              <span className="muted small">{c.testdefs} test definition{c.testdefs === 1 ? "" : "s"}</span>
            </button>
          ))}
          <button className="small" onClick={() => setCreating(true)} title="Write a new campaign file in this repository, with the test definitions you pick">New campaign…</button>
        </nav>
        {selected && <CampaignSynthesis key={`${sid}:${selected}`} sid={sid} campaign={selected} />}
      </div>
      {creating && <NewCampaignDialog sid={sid} ctx={ctx} onClose={() => setCreating(false)}
        onCreated={(path) => { setCreating(false); reread(); setQuery("campaign", path); }} />}
    </>
  );
}

/** A new campaign file: its id and name, and the test definitions it runs. The dataset versions are read from the
 *  test definitions as they are written, to start from: every one is listed, to be changed in the file. */
function NewCampaignDialog({ sid, ctx, onClose, onCreated }: { sid: string; ctx: any; onClose: () => void; onCreated: (path: string) => void }) {
  const testdefs: any[] = ctx?.workspace?.testdefs || [];
  const taken = (ctx?.workspace?.campaigns || []).map((c: any) => c.id);
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [folder, setFolder] = useState("campaigns");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const path = `${folder.replace(/^\/+|\/+$/g, "")}/${id}.campaign.yml`.replace(/^\//, "");
  const depth = folder.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean).length;
  const create = async () => {
    setBusy(true);
    try {
      // the versions the test definitions write today, to start from (one per dataset: the first one met)
      const versions: Record<string, string> = {};
      for (const t of picked) {
        const found: any = await post(`edit/sessions/${sid}/synthesis`, { testdef: t, versions: false, run_checks: false }).catch(() => null);
        for (const d of found?.baseline || []) if (!(d.id in versions) && (d.version ?? d.written)) versions[d.id] = String(d.version ?? d.written);
      }
      const datasets = Object.entries(versions);
      const lines = [
        `# Test campaign: its test definitions are generated with these dataset versions and run together.`,
        `id: ${id}`,
        `name: ${JSON.stringify(name || id)}`,
        `description: ""`,
        `priority: 50                       # of its runs; a test definition may give its own`,
        `testdefs:                          # relative to this file`,
        ...picked.map((t) => `  - ${"../".repeat(depth)}${t}`),
        `routing:                           # tags the lab resources of its runs must carry (a stage's id is one of its tags)`,
        `  stage_tags: []`,
        `  component_tags: {}               # role: [tags]`,
        datasets.length ? `datasets:                          # the version of every dataset its test definitions import`
          : `datasets: {}                       # the version of every dataset its test definitions import`,
        ...datasets.map(([k, v]) => `  ${k}: ${JSON.stringify(v)}`),
      ];
      await put(`edit/sessions/${sid}/files/${path}`, { content: lines.join("\n") + "\n" });
      onCreated(path);
    } catch (e: any) {
      await attempt(() => Promise.reject(e));
    } finally {
      setBusy(false);
    }
  };
  const problem = !id ? "give it an id" : taken.includes(id) ? "a campaign has this id already" : !picked.length ? "pick at least one test definition" : null;
  return (
    <Dialog title="New campaign" onClose={onClose} footer={
      <>
        <span className="muted small">{problem || <>writes <code>{path}</code></>}</span>
        <button className="primary" disabled={!!problem || busy} onClick={create}>{busy ? "Reading the versions…" : "Create"}</button>
      </>
    }>
      <Field label="Id" help="The campaign's id: its file is <id>.campaign.yml"><IdText value={id} onChange={setId} taken={taken} placeholder="nightly" /></Field>
      <Field label="Name"><Text value={name} onChange={setName} placeholder="Nightly" /></Field>
      <Field label="Folder" help="Where the file goes, in the repository"><Text value={folder} onChange={setFolder} /></Field>
      <Field label="Test definitions">
        <div className="campaign-pick">
          {testdefs.map((t) => (
            <label key={t.path} className="small">
              <input type="checkbox" checked={picked.includes(t.path)}
                onChange={(e) => setPicked(e.target.checked ? [...picked, t.path] : picked.filter((p) => p !== t.path))} />{" "}
              <strong>{t.name || t.id}</strong> <span className="muted mono">{t.path}</span>
            </label>
          ))}
        </div>
      </Field>
    </Dialog>
  );
}

/** A campaign at a glance: what it runs, the versions it pins against those its test definitions write, its routing. */
function CampaignSynthesis({ sid, campaign }: { sid: string; campaign: string }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    post(`edit/sessions/${sid}/campaign/synthesis`, { campaign })
      .then((d) => live && setData(d)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [sid, campaign]);
  const edit = (file: string) => navigate(`edit/${sid}`, { file });
  const c = data?.campaign;
  const testdefs: any[] = data?.testdefs || [];
  const routing = data?.routing || {};
  const stageTags = routing.stage_tags || [];
  return (
    <div className="synthesis campaign">
      <div className="synthesis-head">
        <div>
          <h3>{c?.name || c?.id || campaign}</h3>
          <div className="muted small">{c?.id && <><code>{c.id}</code> · </>}<button className="link" onClick={() => edit(campaign)}>{campaign}</button>
            {data?.git && <> · {data.git.tag ? <>tag <code>{data.git.tag}</code></> : "not on a tag"}{data.git.clean === false && ", local changes"}</>}</div>
          {c?.description && <Markdown text={c.description} className="small" />}
        </div>
        <span className="spacer" />
        <button className="primary" disabled={!data || data.problems.length > 0} onClick={() => setRunning(true)}
          title={data?.problems?.length ? "Fix the problems first" : "Generate the datapack of every test definition, then post them to the queue as one campaign run"}>Generate and run…</button>
      </div>
      {error && <pre className="error-box">{error}</pre>}
      {!data && !error && <Spinner className="block" text="Reading the campaign and its test definitions…" />}
      {data?.problems?.length > 0 && (
        <div className="banner warn list">
          <strong>{data.problems.length === 1 ? "A problem" : `${data.problems.length} problems`} to fix before generating</strong>
          <ul className="problems">{data.problems.map((p: string, i: number) => <li key={i}><code>{p}</code></li>)}</ul>
        </div>
      )}
      {data?.warnings?.length > 0 && (
        <div className="banner info list"><ul className="problems">{data.warnings.map((p: string, i: number) => <li key={i}>{p}</li>)}</ul></div>
      )}
      {data && (
        <>
          <Section title={<>Test definitions <span className="muted small">one datapack and one run each, in this order</span></>}>
            <table className="grid compact">
              <thead><tr><th>Test definition</th><th>Configuration</th><th>Priority</th><th>Checks</th></tr></thead>
              <tbody>
                {testdefs.map((t) => (
                  <tr key={`${t.testdef}:${t.setup_config || ""}`}>
                    <td>
                      <button className="link" onClick={() => { setQuery("view", null); setQuery("campaign", null); setQuery("def", t.path); }}
                        title="Open this test definition in the Test definitions subtab"><strong>{t.name || t.id || t.testdef}</strong></button>
                      <div className="muted small mono">{t.testdef}</div>
                    </td>
                    <td className="small">{t.configuration || t.setup_config || <span className="muted">?</span>}{t.setup_config && <span className="muted"> (the campaign's)</span>}</td>
                    <td className="small">{t.priority ?? <span className="muted" title="queue.default_priority of the sequencer">default</span>}</td>
                    <td className="small">{t.problems?.length ? <span className="badge warn" title={t.problems.join("\n")}>{t.problems.length} problem(s)</span> : <span className="badge ok">ok</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
          <Section title={<>Dataset versions <span className="muted small">one version of each dataset for the whole campaign</span></>}>
            {data.datasets.length === 0 && <p className="muted small">Its test definitions import no dataset.</p>}
            {data.datasets.length > 0 && (
              <table className="grid compact baseline">
                <thead><tr><th>Dataset</th><th>Pinned by the campaign</th>{testdefs.map((t) => <th key={t.testdef} title={t.testdef}>{t.id || t.testdef}</th>)}</tr></thead>
                <tbody>
                  {data.datasets.map((d: any) => (
                    <tr key={d.id} className={classes(d.unused && "off")}>
                      <td><strong>{d.id}</strong>{d.importer && <div className="muted small"><span className="badge">{d.importer}</span> <code title={d.source}>{d.source}</code></div>}</td>
                      <td>{d.pinned ? <code>{d.pinned}</code> : <span className="badge bad" title="Every dataset its test definitions import must be listed under datasets: in the campaign file">missing</span>}
                        {d.unused && <div className="muted small">imported by none of its test definitions</div>}</td>
                      {testdefs.map((t) => {
                        const u = (d.used_by || []).find((x: any) => x.testdef === t.testdef);
                        return (
                          <td key={t.testdef} className="small">
                            {u ? <><span className="muted" title={`written in ${u.defined_in || "the setup"}`}>writes</span> <code className={classes(d.pinned && u.written !== d.pinned && "replaced")}>{u.written}</code></> : <span className="muted">—</span>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="muted small">The pinned version replaces the one each test definition writes, for this campaign only; the datapacks record it (<code>versions_pinned_by_campaign</code>) and stay conform when it is a tag of its source and the campaign file is on a clean tag. Change them in the campaign file.</p>
          </Section>
          <Section title={<>Routing <span className="muted small">tags the lab resources of its runs must carry</span></>}>
            <dl className="facts small">
              <dt>Stages</dt>
              <dd>{(Array.isArray(stageTags) ? stageTags.length : Object.keys(stageTags).length) === 0 ? <span className="muted">any (no tag)</span>
                : Array.isArray(stageTags) ? stageTags.map((t: string) => <span key={t} className="chip">{t}</span>)
                : Object.entries(stageTags).map(([type, tags]: any) => <div key={type}><code>{type}</code>: {tags.map((t: string) => <span key={t} className="chip">{t}</span>)}</div>)}</dd>
              <dt>Components</dt>
              <dd>{Object.keys(routing.component_tags || {}).length === 0 ? <span className="muted">any (no tag)</span>
                : Object.entries(routing.component_tags).map(([role, tags]: any) => <div key={role}>role <code>{role}</code>: {tags.map((t: string) => <span key={t} className="chip">{t}</span>)}</div>)}</dd>
            </dl>
            <p className="muted small">Added to the tags of the slots and stage slots of every run (a stage's or component's id is one of its tags: <code>stage_tags: [bench_2]</code> runs everything on that stage). The sequencer refuses the campaign when the lab has nothing that fits.</p>
          </Section>
        </>
      )}
      {running && data && <RunCampaignDialog sid={sid} campaign={campaign} data={data} onClose={() => setRunning(false)} />}
    </div>
  );
}

/** Generate every datapack of the campaign (all of them or none), then post them as one campaign run. */
function RunCampaignDialog({ sid, campaign, data, onClose }: { sid: string; campaign: string; data: any; onClose: () => void }) {
  const [check, setCheck] = useState(false);
  const [priority, setPriority] = useState("");
  const [busy, setBusy] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [job, setJob] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  const generate = async () => {
    setBusy(true);
    setResult(null);
    setLines([]);
    try {
      const { job } = await post(`edit/sessions/${sid}/campaign/jobs`, { campaign, nocheck: !check });
      setJob(job);
      let since = 0;
      for (;;) {
        if (!live.current) return;
        const r: any = await get(`edit/sessions/${sid}/datapack/jobs/${job}?since=${since}`);
        if (r.lines.length) setLines((l) => [...l, ...r.lines]);
        since = r.next;
        if (r.done) {
          setResult(r.result);
          break;
        }
        await new Promise((ok) => setTimeout(ok, 400));
      }
    } catch (e: any) {
      setResult({ ok: false, error: e.message, log: "" });
    } finally {
      setBusy(false);
      setJob(null);
    }
  };
  const stop = () => job && attempt(() => post(`edit/sessions/${sid}/datapack/jobs/${job}/stop`, {}));
  const submit = async () => {
    const view = await attempt(() => post(`edit/sessions/${sid}/campaign/${result.name}/submit`, priority ? { priority: Number(priority) } : {}),
      "campaign posted to the queue");
    if (view) {
      onClose();
      navigate(`campaigns/${view.id}`);
    }
  };
  const manifest = result?.manifest;
  const footer = result?.ok ? (
    <>
      <input className="priority" type="number" min={0} max={100} placeholder="priority" value={priority} onChange={(e) => setPriority(e.target.value)}
        title="A priority for all its runs, instead of the campaign's (and its test definitions')" />
      <button className="primary" onClick={submit}>Post the campaign to the queue</button>
      <button onClick={generate}>Generate again</button>
    </>
  ) : (
    <>
      {busy && <button onClick={stop}>Stop</button>}
      <button className="primary" disabled={busy} onClick={generate}>{busy ? "Generating…" : "Generate"}</button>
    </>
  );
  return (
    <Dialog title={`Generate and run ${data.campaign?.name || data.campaign?.id || campaign}`} onClose={onClose} footer={footer} wide>
      {!result && !busy && (
        <>
          <p className="small">Generates the {data.testdefs.length} datapack(s) of the campaign with the dataset versions it pins. If one cannot be generated, none is kept.</p>
          <Field label="Check against the lab" help="Without it (draft), the datapacks are generated even from files that are not on a clean git tag: they are non-conform.">
            <Toggle value={check} onChange={setCheck} label="clean git tags required (conform datapacks)" />
          </Field>
        </>
      )}
      {(busy || (result && !result.ok)) && <LiveLog lines={busy ? lines : (result.log || "").split("\n")} />}
      {result && !result.ok && <pre className="error-box">{result.error}</pre>}
      {result?.ok && manifest && (
        <>
          <p>
            <strong>{manifest.datapacks.length}</strong> datapack(s) generated{" "}
            {manifest.conform ? <span className="badge ok">conform</span> : <span className="badge warn">non-conform</span>}
          </p>
          <table className="grid compact">
            <thead><tr><th>Test definition</th><th>Datapack</th><th>Priority</th><th>Conform</th><th></th></tr></thead>
            <tbody>
              {manifest.datapacks.map((d: any) => (
                <tr key={d.zip}>
                  <td className="small mono">{d.testdef}{d.setup_config && <span className="muted"> · {d.setup_config}</span>}</td>
                  <td className="small"><code>{d.name}</code></td>
                  <td className="small">{d.priority ?? <span className="muted">default</span>}</td>
                  <td><Badge value={d.conform ? "conform" : "non-conform"} /></td>
                  <td><a className="small" href={url(`edit/sessions/${sid}/campaign/${result.name}/files/${d.zip}`)} download>download</a></td>
                </tr>
              ))}
            </tbody>
          </table>
          <details><summary>Log of the datapack tool{logWarnings(result.log)}</summary><LiveLog lines={result.log.split("\n")} /></details>
        </>
      )}
    </Dialog>
  );
}

// ── campaign runs ──────────────────────────────────────────────────────────

/** A campaign run's verdict, or its state while it runs. */
export function CampaignBadge({ view }: { view: any }) {
  return view.state === "done" ? <Badge value={view.verdict} /> : <Badge value={view.state} />;
}

/** "3 of 5 done: 2 succeeded, 1 failed", as a bar and its text. */
export function CampaignProgress({ counts }: { counts: any }) {
  const done = counts.succeeded + counts.failed + counts.error;
  const part = (n: number) => `${(100 * n) / Math.max(1, counts.total)}%`;
  return (
    <span className="campaign-progress" title={`${counts.succeeded} succeeded, ${counts.failed} failed, ${counts.error} error, ${counts.running} running, ${counts.waiting} waiting`}>
      <span className="bar">
        <span className="succeeded" style={{ width: part(counts.succeeded) }} />
        <span className="failed" style={{ width: part(counts.failed) }} />
        <span className="error" style={{ width: part(counts.error) }} />
        <span className="running" style={{ width: part(counts.running) }} />
      </span>
      <span className="small">{done} of {counts.total} done</span>
    </span>
  );
}

/** The page of a campaign run (#/campaigns/<id>): its runs at a glance, with links to go deeper, and its report. */
export function CampaignRunPage({ id }: { id: string }) {
  const view = useResource<any>(`campaigns/${id}`, ["run", "phase", "campaign"]);
  const v = view.data;
  if (view.error) return <div className="page"><div className="error-box">{view.error}</div></div>;
  if (!v) return <div className="page"><Spinner className="block" text="Reading the campaign run…" /></div>;
  const routing = v.routing || {};
  const stageTags = routing.stage_tags || [];
  const tags = [
    ...(Array.isArray(stageTags) ? stageTags : Object.entries(stageTags).flatMap(([type, t]: any) => t.map((x: string) => `${type}: ${x}`))),
    ...Object.entries(routing.component_tags || {}).flatMap(([role, t]: any) => t.map((x: string) => `${role}: ${x}`)),
  ];
  const cancel = (mode: string) => attempt(() => post(`campaigns/${v.id}/${mode}`, {}), `${mode} requested`).then(() => view.refresh());
  return (
    <div className="page campaign-page">
      <div className="page-head">
        <div>
          <div className="muted small"><a href="#/runs">All runs</a> › campaign run <code>{v.id}</code></div>
          <h2>{v.title || v.campaign} <CampaignBadge view={v} /> {!v.conform && <Badge value="non-conform" />}</h2>
          <div className="muted small">
            campaign <code>{v.campaign}</code> · submitted {ago(v.submitted_at)} <span title={time(v.submitted_at)}>({time(v.submitted_at)})</span>
            {v.state === "done" && v.started_at && <> · ran {duration(v.started_at, v.last_ended_at)}</>}
          </div>
        </div>
        <span className="spacer" />
        {v.state !== "done" && <button onClick={() => cancel("cancel")} title="Cancel every run of the campaign that has not ended">Cancel all</button>}
        <a className="button" href={url(`campaigns/${v.id}/report`)} target="_blank" rel="noreferrer" title="The synthetic report as a page of its own">Report</a>
        <a className="button" href={url(`campaigns/${v.id}/report?download=true`)} title="The synthetic report as a self-contained HTML file">Download</a>
      </div>
      {v.description && <Markdown text={v.description} className="intro" />}
      <div className="campaign-tiles">
        {([["total", "runs"], ["succeeded", "succeeded"], ["failed", "failed"], ["error", "error"], ["running", "running"], ["waiting", "waiting"]] as const).map(([k, label]) => (
          <div key={k} className={classes("tile", k)}><b>{v.counts[k]}</b>{label}</div>
        ))}
      </div>
      <table className="grid campaign-runs">
        <thead><tr><th>Test definition</th><th>Run</th><th>Verdict</th><th>Duration</th><th>Ran on</th><th>Checks met</th><th>Details</th></tr></thead>
        <tbody>
          {v.runs.map((r: any) => {
            const f = r.failed_step;
            const step = f ? `${f.phase}:${f.id}${f.slot ? `[${f.slot}]` : ""}` : null;
            return (
              <tr key={r.run}>
                <td><strong>{r.testdef}</strong><div className="muted small mono">{r.testdef_file}{r.setup_config && ` · ${r.setup_config}`}</div></td>
                <td><a href={`#/runs/${r.run}`}>{r.run}</a><div className="muted small">priority {r.priority}</div></td>
                <td>{r.state === "done" || r.state === "cancelled" ? <Badge value={r.verdict} /> : <Badge value={r.state} />}</td>
                <td className="small">{r.activated_at ? duration(r.activated_at, r.ended_at) : ""}</td>
                <td className="small">{(r.stages?.length ? r.stages : [...new Set(Object.values(r.components || {}))]).map((s: any) => <span key={s} className="chip">{s}</span>)}</td>
                <td className="small">
                  {r.checks?.total ? <span className={classes("badge", r.checks.met === r.checks.total ? "ok" : "bad")}>{r.checks.met} / {r.checks.total}</span> : <span className="muted">none</span>}
                  {(r.reports || []).map((x: any) => <div key={x.path}><a href={url(`runs/${r.run}/files/${x.path}`)} target="_blank" rel="noreferrer">exploit report</a></div>)}
                </td>
                <td className="small">
                  {f && <div><Badge value={f.state} /> <a href={`#/runs/${r.run}?tab=steps`}><code>{step}</code></a>{f.reason && <span className="muted"> {f.reason}</span>}
                    {" · "}<a href={url(`runs/${r.run}/logs/${step}`)} target="_blank" rel="noreferrer">log</a></div>}
                  {(r.checks?.not_met || []).slice(0, 3).map((c: any, i: number) => <div key={i} className="muted">✗ <code>{c.criteria}</code> {c.check || c.scenario}</div>)}
                  {!f && r.reason && <div className="muted">{r.reason}</div>}
                  {(r.nonconform || []).slice(0, 2).map((n: string, i: number) => <div key={i} className="warn-text">non-conform: {n}</div>)}
                  {r.results && <div><a href={url(`runs/${r.run}/files/results.zip?results=true`)}>results zip</a></div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="campaign-facts">
        <Section title="Dataset versions">
          {Object.keys(v.datasets || {}).length === 0 ? <p className="muted small">The campaign pins no dataset.</p> : (
            <table className="grid compact"><tbody>
              {Object.entries(v.datasets).map(([k, ver]: any) => <tr key={k}><td><code>{k}</code></td><td><code>{ver}</code></td></tr>)}
            </tbody></table>
          )}
        </Section>
        <Section title="Baseline and routing">
          <dl className="facts small">
            <dt>Campaign file</dt><dd><code>{v.file}</code></dd>
            <dt>Git</dt><dd>{v.git?.repo} <code>{v.git?.tag || v.git?.commit?.slice(0, 12) || "not in git"}</code>{v.git && v.git.clean === false && <span className="warn-text"> (local changes)</span>}</dd>
            <dt>Tags</dt><dd>{tags.length ? tags.map((t) => <span key={t} className="chip">{t}</span>) : <span className="muted">none</span>}</dd>
          </dl>
        </Section>
      </div>
    </div>
  );
}
