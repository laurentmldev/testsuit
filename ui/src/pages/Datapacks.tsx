// Datapacks (sequencer README, section 13.2): the test definitions of the tests repositories, what each one
// is made of (logical configuration, input files, dictionaries), its baseline, and generating its datapack.
import { Fragment, useEffect, useRef, useState } from "react";
import { get, post, useResource } from "../api";
import { GenerateDatapack } from "../editor/Generate";
import { Section } from "../fields";
import { attempt, classes, Markdown, navigate, setQuery, Spinner, type Route } from "../util";

const INTRO = `Each **test definition** gives one datapack: pick one to see what it is made of, update the versions it is built from, and generate it. The files are read and changed where the sequencer reads them (in place); **Edit datapack definitions** opens them in the Editor, where they can be reviewed and pushed.`;

/** The in-place editing session of a tests repository: opened once, then reused. */
function useWorkspace(repo: string | null) {
  const [sid, setSid] = useState<string | null>(null);
  const [ctx, setCtx] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
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
  }, [repo]);
  return { sid, ctx, error };
}

export function DatapacksPage({ route }: { route: Route }) {
  const home = useResource<any>("edit");
  const repos: any[] = (home.data?.repositories || []).filter((r: any) => r.kind !== "lab");
  const repo = route.query.get("repo") || repos[0]?.id || null;
  const { sid, ctx, error } = useWorkspace(repo);
  const testdefs: any[] = ctx?.workspace?.testdefs || [];
  const selected = route.query.get("def") || testdefs[0]?.path || null;
  const [config, setConfig] = useState<string | null>(null);
  useEffect(() => setConfig(null), [selected]);

  if (home.error) return <div className="page"><div className="error-box">{home.error}</div></div>;
  return (
    <div className="page datapacks-page">
      <div className="page-head">
        <h2>Datapacks</h2>
        {repos.length > 1 && (
          <select value={repo || ""} onChange={(e) => navigate("datapacks", { repo: e.target.value })} title="Tests repository">
            {repos.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
        )}
        <span className="spacer" />
        <button disabled={!sid} onClick={() => navigate(`edit/${sid}`)} title="Open the test definitions, setups, dictionaries and templates in the Editor">Edit datapack definitions</button>
      </div>
      <Markdown text={INTRO} className="intro" />
      {!home.data && <Spinner className="block" text="Reading the repositories…" />}
      {home.data && repos.length === 0 && <p className="muted">No tests repository: add one to <code>server.workspaces</code> in the configuration.</p>}
      {error && <div className="error-box">{error}</div>}
      {repo && !ctx && !error && <Spinner className="block" text="Reading the test definitions…" />}
      {ctx && (
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
        <button className="primary" disabled={!data} onClick={() => setGenerate(true)}>Generate…</button>
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
          <Baseline sid={sid} testdef={testdef} config={config} datasets={data.baseline} onChanged={load} edit={edit} />
          <LogicalConfig data={data} edit={edit} />
          <Inputs data={data} edit={edit} />
          <Dictionaries data={data} edit={edit} />
        </>
      )}
      {generate && <GenerateDatapack sid={sid} workspace={ctx.workspace} testdef={testdef} config={config || data?.testdef?.setup_config} onClose={() => setGenerate(false)} />}
    </div>
  );
}

function FileLink({ path, edit }: { path?: string | null; edit: (f: string) => void }) {
  if (!path) return null;
  if (path.startsWith("/") || path.startsWith("<")) return <code className="small">{path}</code>;
  return <button className="link mono small" onClick={() => edit(path)}>{path}</button>;
}

function LogicalConfig({ data, edit }: { data: any; edit: (f: string) => void }) {
  const td = data.testdef;
  return (
    <Section title={<>Logical configuration <span className="muted small">{td.setup_config} of {data.setup?.name || data.setup?.id}</span></>}
      actions={<FileLink path={td.setup_file} edit={edit} />}>
      {Object.keys(data.stages).length > 0 && (
        <p className="small">Stages: {Object.entries(data.stages).map(([s, t]: [string, any]) => <span key={s} className="chip">{s}: {t}</span>)}</p>
      )}
      <table className="grid compact">
        <thead><tr><th>Role</th><th>Component</th><th>Datapack type</th><th>Configuration</th><th>Stage</th><th>Needs</th></tr></thead>
        <tbody>
          {data.components.map((c: any) => (
            <tr key={c.id}>
              <td>{c.role || <span className="muted">none</span>}</td>
              <td><strong>{c.id}</strong><div className="muted small">{c.name}</div></td>
              <td><code>{c.type}</code></td>
              <td>{c.configuration}</td>
              <td>{c.stage || <span className="muted small">ad hoc</span>}</td>
              <td>{c.needs.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

function Inputs({ data, edit }: { data: any; edit: (f: string) => void }) {
  const count = data.components.reduce((n: number, c: any) => n + c.inputs.length, 0);
  return (
    <Section title={<>Template and input files <span className="muted small">{count} inputs</span></>}>
      <table className="grid compact">
        <thead><tr><th>Component</th><th>Target</th><th>Method</th><th>Source</th></tr></thead>
        <tbody>
          {data.components.map((c: any) => c.inputs.map((i: any, n: number) => (
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

function Baseline({ sid, testdef, config, datasets, onChanged, edit }: {
  sid: string; testdef: string; config: string | null; datasets: any[]; onChanged: () => void; edit: (f: string) => void;
}) {
  const [wanted, setWanted] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const set = async (d: any) => {
    setBusy(d.id);
    const done = await attempt(() => post(`edit/sessions/${sid}/baseline`, { testdef, dataset: d.id, version: wanted[d.id], setup_config: config }),
      `${d.id}: version ${wanted[d.id]}${d.key ? ` (key ${d.key})` : ""}`);
    setBusy(null);
    if (done) {
      setWanted({ ...wanted, [d.id]: "" });
      onChanged();
    }
  };
  return (
    <Section title={<>Baseline <span className="muted small">versions of the data it is built from</span></>}>
      {datasets.length === 0 && <p className="muted small">This setup imports no dataset.</p>}
      {datasets.length > 0 && (
        <table className="grid compact baseline">
          <thead><tr><th>Dataset</th><th>From</th><th>Version</th><th>Written in</th></tr></thead>
          <tbody>
            {datasets.map((d) => {
              const value = wanted[d.id] ?? "";
              const changed = value.trim() !== "" && value.trim() !== d.version;
              return (
                <tr key={d.id}>
                  <td><strong>{d.id}</strong><div className="muted small">{d.name}</div></td>
                  <td className="small"><span className="badge">{d.importer}</span> <code title={d.source}>{d.source}</code>
                    {d.versions_pending && <div className="muted small"><Spinner text="listing its versions…" /></div>}
                    {d.versions_error && <div className="muted small" title={d.versions_error}>versions not listed: {d.versions_error}</div>}</td>
                  <td>
                    <div className="version-edit">
                      <input list={`versions-${d.id}`} placeholder={d.version || d.written} value={value} disabled={!d.editable}
                        onChange={(e) => setWanted({ ...wanted, [d.id]: e.target.value })}
                        onKeyDown={(e) => e.key === "Enter" && changed && set(d)}
                        title={d.editable ? "Pick one of the versions available, or type one" : "Not written in a file of this repository"} />
                      <datalist id={`versions-${d.id}`}>
                        {(d.versions || []).map((v: string) => <option key={v} value={v}>{v === d.version ? "current" : ""}</option>)}
                      </datalist>
                      <button className="small primary" disabled={!changed || busy === d.id} onClick={() => set(d)}>Set</button>
                    </div>
                    <div className="small">current <code>{d.version ?? d.written}</code>
                      {d.versions && <span className="muted"> · {d.versions.length} available{d.versions[0] && d.versions[0] !== d.version ? `, latest ${d.versions[0]}` : ""}</span>}</div>
                  </td>
                  <td className="small">
                    {d.key ? <>key <code>{d.key}</code> in </> : null}<FileLink path={d.defined_in} edit={edit} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <p className="muted small">A version is changed in the file that writes it; a datapack generated from uncommitted files is a draft until the change is committed and pushed.</p>
    </Section>
  );
}
