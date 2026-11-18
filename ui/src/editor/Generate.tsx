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

// Generate a datapack from the session's files (sequencer README, sections 9 and 13.2), look into it, then
// download it or post it to the queue: a way to try a change before pushing it.
import { useEffect, useRef, useState } from "react";
import { get, post, url } from "../api";
import { Choices, Dialog, Field, Toggle } from "../fields";
import { FileBrowser } from "../files";
import { handKeys, KeysByHand, latestRememberedKeys, rememberedKeys, rememberKeys, statusAmong, type HandKey } from "../keysbyhand";
import { attempt, classes, Markdown, navigate, size, Spinner } from "../util";
import { useSession } from "./state";
import { WorkspaceTag, type Repository } from "./workspace";

export function GenerateDialog({ onClose }: { onClose: () => void }) {
  const { sid, ctx, info } = useSession();
  if (info.kind === "lab")
    return (
      <Dialog title="Generate a datapack" onClose={onClose}>
        <p>Datapacks come from the test definitions of a tests repository: open an editing session on it. A datapack generated there is checked against the lab as it runs now.</p>
      </Dialog>
    );
  return <GenerateDatapack sid={sid} workspace={ctx.workspace} repository={ctx.repository} onClose={onClose} />;
}

const GENERATE_KEYS = "tsq.generateKeys";
const USER_KEYS = "tsq.userKeys";                       // the values given to the user_keys of each test definition
const USER_KEY_ORIGIN = "<user key>";                   // the placeholder of such a key, before it is given

/** Generate a datapack of a session's test definitions; testdef and config preselect one (Datapacks page). */
export function GenerateDatapack({ sid, workspace, repository, testdef: initial, config: initialConfig, onClose }: {
  sid: string; workspace: any; repository?: Repository | null; testdef?: string; config?: string | null; onClose: () => void;
}) {
  const testdefs: any[] = workspace?.testdefs || [];
  const [testdef, setTestdef] = useState<string>(initial || testdefs[0]?.path || "");
  const td = testdefs.find((t) => t.path === testdef);
  const setup = (workspace?.setups || []).find((s: any) => s.path === td?.setup_file);
  const configs = Object.keys(setup?.configurations || {});
  const [config, setConfig] = useState<string | null>(initialConfig && initialConfig !== td?.setup_config ? initialConfig : null);
  const [check, setCheck] = useState(false);
  // keys set by hand for this generation (datapack --set), and the keys the dictionaries define, to say what each does
  // remembered in this browser for each test definition of the repository: the last ones set for it come back
  // (none remembered for it: those set the most recently for another test definition of the repository)
  const memoryPrefix = `${repository?.id || ""}:`;
  const memory = memoryPrefix + testdef;
  const recall = (): { rows: HandKey[]; from: string | null } => {
    const own = rememberedKeys(GENERATE_KEYS, memory);
    if (own.length) return { rows: own, from: null };
    const other = latestRememberedKeys(GENERATE_KEYS, memoryPrefix, memory);
    return other ? { rows: other.rows, from: other.id.slice(memoryPrefix.length) } : { rows: [], from: null };
  };
  const [recalled, setRecalled] = useState(recall);
  useEffect(() => setRecalled(recall()), [memory]);
  const rows = recalled.rows;
  const setRows = (next: HandKey[]) => {
    setRecalled({ rows: next, from: null });
    rememberKeys(GENERATE_KEYS, memory, next);
  };
  const [defined, setDefined] = useState<any[] | null>(null);
  // the keys the test definition asks at each generation (user_keys), and the values given, remembered per test definition
  const [userKeys, setUserKeys] = useState<Record<string, { description?: string; choices?: any[] | null }>>({});
  const [given, setGiven] = useState<Record<string, string>>({});
  useEffect(() => setGiven(Object.fromEntries(rememberedKeys(USER_KEYS, memory).map((r) => [r.key, r.value]))), [memory]);
  const give = (name: string, value: string) => {
    const next = { ...given, [name]: value };
    setGiven(next);
    rememberKeys(USER_KEYS, memory, Object.entries(next).map(([key, v]) => ({ key, value: v })));
  };
  useEffect(() => {
    if (!testdef) return;
    let current = true;
    setDefined(null);
    post(`edit/sessions/${sid}/keys`, { testdef, setup_config: config }).then((r) => {
      if (!current) return;
      setDefined((r.keys || []).filter((k: any) => k.origin !== USER_KEY_ORIGIN));
      setUserKeys(r.user_keys || {});
    }).catch(() => current && setDefined([]));
    return () => { current = false; };
  }, [sid, testdef, config]);
  const keyStatus = defined ? statusAmong(defined, (origin) => origin === "<datapack>") : () => undefined;
  const ungiven = Object.keys(userKeys).filter((k) => !String(given[k] ?? "").trim());
  // a row with no value yet (a key proposed as undefined) is not set
  const byHand = handKeys(rows.filter((r) => r.value !== ""));
  const manual = Object.keys(userKeys).length
    ? { ...(byHand || {}), ...Object.fromEntries(Object.keys(userKeys).filter((k) => given[k] !== undefined && given[k] !== "").map((k) => [k, given[k]])) }
    : byHand;
  const overridden = Object.keys(byHand || {}).filter((k) => keyStatus(k)?.overrides && !keyStatus(k)?.blocked);
  const blockedKeys = Object.keys(byHand || {}).filter((k) => keyStatus(k)?.blocked);
  // dataset versions for this generation only (datapack --dataset-version): dataset id -> version
  const [versions, setVersions] = useState<Record<string, string>>({});
  useEffect(() => setVersions({}), [testdef, config]);
  const versionsByHand = Object.fromEntries(Object.entries(versions).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [failed, setFailed] = useState("");            // the error of the last failed generation: its undefined keys are proposed
  const [commandKeys, setCommandKeys] = useState<string[]>([]);   // the undefined keys the commands of the steps read: proposed too
  useEffect(() => {                                     // known before generating: their rows are there from the start
    if (!testdef) return;
    let current = true;
    setCommandKeys([]);
    post(`edit/sessions/${sid}/run_problems`, { testdef, setup_config: config }).then((r) => current && setCommandKeys(r.missing_keys || [])).catch(() => {});
    return () => { current = false; };
  }, [sid, testdef, config]);
  const missingKeys = [...new Set([...undefinedIn(failed), ...commandKeys])].filter((k) => !(k in userKeys));
  useEffect(() => {                                     // a row for each undefined key, to fill in (not remembered empty)
    const absent = missingKeys.filter((k) => !rows.some((r) => r.key.trim() === k));
    if (absent.length) setRecalled((r) => ({ ...r, rows: [...r.rows.filter((x) => x.key.trim() || x.value), ...absent.map((key) => ({ key, value: "" }))] }));
  }, [missingKeys.join(" ")]);
  const [priority, setPriority] = useState("");
  const [posted, setPosted] = useState<any>(null);

  const [lines, setLines] = useState<string[]>([]);
  const [job, setJob] = useState<string | null>(null);
  const [started, setStarted] = useState(0);
  const [lastLine, setLastLine] = useState(0);
  const [stopping, setStopping] = useState(false);
  const live = useRef(true);
  useEffect(() => () => {
    live.current = false;
  }, []);
  // the problems the configuration shows before generating: the user confirms, or goes back to fix them
  const [problems, setProblems] = useState<string[] | null>(null);
  const [checking, setChecking] = useState(false);
  // the checks before generating, one at a time: what each looks at, how long it took, what it found
  const [checks, setChecks] = useState<Check[]>([]);
  const request = async () => {
    setChecking(true);
    setResult(null);
    setPosted(null);
    setChecks([]);
    const found: string[] = [];
    const step = async (label: string, call: () => Promise<any>, detail: (r: any) => string): Promise<boolean> => {
      const started = Date.now();
      setChecks((c) => [...c, { label, state: "running" }]);
      const end = (patch: Partial<Check>) => setChecks((c) => c.map((x, i) => i === c.length - 1 ? { ...x, seconds: (Date.now() - started) / 1000, ...patch } : x));
      try {
        const r = await call();
        found.push(...(r.problems || []));
        end({ state: r.problems?.length ? "problems" : "ok", detail: detail(r) + (r.problems?.length ? ` · ${r.problems.length} problem(s)` : "") });
        return true;
      } catch (e: any) {
        end({ state: "error", detail: e.message });
        found.push(`The configuration cannot be read: ${e.message}`);
        return false;
      }
    };
    try {
      const body = { testdef, setup_config: config };
      const read = await step("Setup, dictionaries and inputs",
        () => post(`edit/sessions/${sid}/synthesis`, { ...body, versions: false, run_checks: false }),
        (r) => {
          const inputs = (r.slots || []).flatMap((c: any) => c.inputs || []);
          return `${(r.slots || []).length} slots, ${inputs.length} inputs looked up, ${(r.dictionaries || []).length} dictionaries`;
        });
      if (read)
        await step("Pipeline and lab: phases, stage slots, slots, step targets",
          () => post(`edit/sessions/${sid}/run_problems`, { ...body, keys: manual }).then((r) => {
            setCommandKeys(r.missing_keys || []);
            return r;
          }), (r) => "against the lab as it is now" + ((r.missing_keys || []).length ? ` · keys to set: ${r.missing_keys.join(", ")}` : ""));
      if (!live.current) return;
      if (found.length) {
        setProblems(found);
        return;
      }
    } finally {
      setChecking(false);
    }
    generate(false);
  };
  const generate = async (lenient: boolean) => {
    setProblems(null);
    setBusy(true);
    setResult(null);
    setPosted(null);
    setLines([]);
    setStopping(false);
    setStarted(Date.now());
    setLastLine(Date.now());
    try {
      // in the background: its log comes line by line, subprocess errors included
      if (recalled.from) rememberKeys(GENERATE_KEYS, memory, rows);   // taken from another test definition: now its own
      const { job } = await post(`edit/sessions/${sid}/datapack/jobs`, { testdef, setup_config: config, nocheck: !check, lenient, keys: manual, versions: versionsByHand });
      setJob(job);
      let since = 0;
      let failures = 0;
      for (;;) {
        if (!live.current) return;
        let r: any;
        try {
          r = await get(`edit/sessions/${sid}/datapack/jobs/${job}?since=${since}`);
          failures = 0;
        } catch (e: any) {
          // the server is unreachable for a moment: try again, a few times only
          if (++failures >= 5 || /no generation/.test(e.message)) throw e;
          await new Promise((ok) => setTimeout(ok, 1000));
          continue;
        }
        if (r.lines.length) {
          setLines((l) => [...l, ...r.lines]);
          setLastLine(Date.now());
        }
        since = r.next;
        if (r.done) {
          setResult(r.result || { ok: false, error: "The generation ended without a result.", log: "" });
          break;
        }
        await new Promise((ok) => setTimeout(ok, 400));
      }
    } catch (e: any) {
      setResult({ ok: false, error: `The generation cannot be followed: ${e.message}`, log: "" });
    } finally {
      setBusy(false);
      setJob(null);
    }
  };
  const stop = async () => {
    setStopping(true);
    await attempt(() => post(`edit/sessions/${sid}/datapack/jobs/${job}/stop`, {}));
  };
  const submit = async () => {
    const run = await attempt(() => post(`edit/sessions/${sid}/datapack/${result.name}/submit`, priority ? { priority: Number(priority) } : {}), "posted to the queue");
    if (run) {
      setPosted(run);
      navigate(`/runs/${run.id}`);          // follow the run it became
      onClose();
    }
  };

  const footer = result?.ok ? (
    <>
      <a className="button" href={url(`edit/sessions/${sid}/datapack/${result.name}`)} download>Download {result.zip}</a>
      <input className="priority" type="number" min={0} max={100} placeholder="priority" value={priority} onChange={(e) => setPriority(e.target.value)} />
      <button className="primary" onClick={submit} disabled={!!posted}>Post to the queue</button>
      <button onClick={request} disabled={busy || checking}>Generate again</button>
    </>
  ) : problems ? (
    <>
      <button onClick={() => setProblems(null)}>Back</button>
      <button className="primary" onClick={() => generate(true)} title="Inputs that cannot be found or are empty are left out, with a warning; the datapack is a draft">Generate anyway</button>
    </>
  ) : (
    <>
      {busy && <button onClick={stop} disabled={!job || stopping} title="Stop the generation: at its next line of output; a command it runs is killed">{stopping ? "Stopping…" : "Stop"}</button>}
      <button className="primary" disabled={!testdef || busy || checking || blockedKeys.length > 0 || ungiven.length > 0}
        title={ungiven.length ? `Give ${ungiven.join(", ")} first` : undefined} onClick={request}>{busy ? "Generating…" : checking ? "Checking…" : "Generate"}</button>
    </>
  );
  return (
    <Dialog title={repository ? `Generate a datapack from ${repository.id}` : "Generate a datapack"} onClose={onClose} footer={footer} wide>
      {repository && <div className="workspace-banner"><WorkspaceTag repository={repository} />
        {(repository as any).workspaces > 1 && <span className="muted small">only the test definitions of this workspace are listed</span>}</div>}
      {!result && checks.length > 0 && <Checks checks={checks} />}
      {busy && (
        <>
          <LiveLog lines={lines} />
          <Elapsed started={started} lastLine={lastLine} />
        </>
      )}
      {problems && (
        <div className="banner warn list">
          <p><strong>This configuration has {problems.length === 1 ? "a problem" : `${problems.length} problems`}</strong>: generate anyway, and the inputs concerned are left out with a warning (the datapack is an incomplete draft); or go back and fix {problems.length === 1 ? "it" : "them"} first.</p>
          <ul className="problems">{problems.map((p, i) => <li key={i}><code>{p}</code></li>)}</ul>
          {commandKeys.length > 0 && <p className="small">Undefined keys read by the commands of the steps: {commandKeys.map((k) => <code key={k}>{k} </code>)}: go back to set {commandKeys.length === 1 ? "it" : "them"} by hand (proposed under <strong>Keys set by hand</strong>), or define {commandKeys.length === 1 ? "it" : "them"} in a dictionary.</p>}
        </div>
      )}
      {!result && !busy && !problems && (
        <>
          <Markdown text="Runs the datapack tool on the files of this session, as `datapack <testdef>` would: nothing is pushed. A datapack generated from uncommitted files is a **draft**: the queue accepts it, its report says so." />
          <Field label="Test definition">
            <Choices value={testdef} onChange={(v) => {
              setTestdef(v || "");
              setConfig(null);
            }} choices={testdefs.map((t) => ({ value: t.path, label: t.name || t.id || t.path, description: `${t.path} · ${t.setup_config || ""}` }))} />
          </Field>
          {configs.length > 1 && (
            <Field label="Setup configuration" help="The test definition names one; another can be tried here (`datapack --setup-config`).">
              <Choices value={config} onChange={setConfig} allowNone={`its own (${td?.setup_config})`} choices={configs.filter((c) => c !== td?.setup_config).map((c) => ({ value: c }))} />
            </Field>
          )}
          {Object.keys(userKeys).length > 0 && (
            <Field label="Keys to give" help="Keys this test definition asks at each generation (its `user_keys`): no dictionary defines them. The values are remembered in this browser for the test definition.">
              <div className="user-keys">
                {Object.entries(userKeys).map(([name, spec]) => (
                  <div key={name} className="user-key">
                    <code>{name}</code>
                    {spec.choices?.length
                      ? <select value={given[name] ?? ""} onChange={(e) => give(name, e.target.value)}>
                          <option value="">choose…</option>
                          {spec.choices.map((c) => <option key={String(c)} value={String(c)}>{String(c)}</option>)}
                        </select>
                      : <input className="mono" value={given[name] ?? ""} placeholder="value" onChange={(e) => give(name, e.target.value)} />}
                    {spec.description && <span className="muted small">{spec.description}</span>}
                    {!String(given[name] ?? "").trim() && <span className="warn-text small">to give</span>}
                  </div>
                ))}
              </div>
            </Field>
          )}
          {testdef && <BaselineVersions sid={sid} testdef={testdef} config={config} versions={versions} onChange={setVersions} />}
          <Field label="Check against the lab" help="Also check that the lab has the lab stages and roles the setup asks for, and run the checks of each slot type, as the queue does on posting.">
            <Toggle value={check} onChange={setCheck} label="check (slower)" />
          </Field>
          <Field label="Keys set by hand" help="Values given for this generation only, over the dictionaries (`datapack --set key=value`): nothing is written to the files. Setting a key no dictionary defines keeps the datapack conform. Overriding a defined key makes the datapack **non-conform**. Both are recorded in its `datapack.yml` and `baseline.yml`. A value is read as YAML: `3` is a number, `true` a boolean; quote it to keep it as text. The keys set here are remembered in this browser for each test definition (one with none remembered starts from those last set for another): check them before generating.">
            {recalled.from && rows.length > 0 && <div className="muted small">None remembered for this test definition: these are the keys last set for <code>{recalled.from}</code>.</div>}
            <KeysByHand rows={rows} onChange={setRows} status={keyStatus} missing={missingKeys}
              known={(defined || []).filter((d) => d.origin !== "<datapack>").map((d) => ({ value: d.key, description: `\`${d.value}\`${d.origin ? ` · ${d.origin}` : ""}` }))}
              setNote="the datapack stays conform." overrideNote="the datapack will be non-conform."
              emptyNote="read and not defined: give it a value (not set until then)" />
            {overridden.length > 0 && <div className="banner warn">Overriding {overridden.length === 1 ? "a defined key" : `${overridden.length} defined keys`} makes this datapack <strong>non-conform</strong>: the queue accepts it, and its run and results are flagged.</div>}
          </Field>
        </>
      )}
      {result && !result.ok && (
        <>
          <pre className="error-box">{result.error}</pre>
          {result.log && <details open><summary>Log</summary><LiveLog lines={result.log.split("\n")} /></details>}
          {undefinedIn(result.error).length > 0 && <p className="small">Undefined: {undefinedIn(result.error).map((k) => <code key={k}>{k} </code>)}: go back to define {undefinedIn(result.error).length === 1 ? "it" : "them"} in a dictionary, or to set {undefinedIn(result.error).length === 1 ? "it" : "them"} by hand for this generation.</p>}
          <button onClick={() => {
            setFailed(result.error || "");
            setResult(null);
          }}>Back</button>
        </>
      )}
      {result?.ok && (
        <>
          {result.datapack?.skipped?.length > 0 && (
            <div className="banner warn list">
              Incomplete: {result.datapack.skipped.length} input(s) left out.
              <ul className="problems">{result.datapack.skipped.map((p: string, i: number) => <li key={i}><code>{p}</code></li>)}</ul>
            </div>
          )}
          {result.datapack?.versions_set_by_hand?.length > 0 && (
            <div className={classes("banner list", result.datapack.versions_set_by_hand.some((v: any) => v.overrides && !v.tag) ? "warn" : "info")}>
              {result.datapack.versions_set_by_hand.some((v: any) => v.overrides && !v.tag)
                ? <><strong>Non-conform</strong>: a dataset version set for this generation is not a tag of its source.</>
                : <>Dataset versions set for this generation (each is a tag of its source, or the version written: the datapack stays conform).</>}
              <ul className="problems">{result.datapack.versions_set_by_hand.map((v: any) => (
                <li key={v.dataset}><code>{v.dataset} {v.version}</code>{v.overrides ? <> instead of <code>{v.was}</code>{v.tag ? " (a tag)" : " (not a tag)"}</> : <> (the version written)</>}</li>
              ))}</ul>
            </div>
          )}
          {result.datapack?.keys_set_by_hand?.length > 0 && (
            <div className={classes("banner list", result.datapack.keys_set_by_hand.some((k: any) => k.overrides) ? "warn" : "info")}>
              {result.datapack.keys_set_by_hand.some((k: any) => k.overrides)
                ? <><strong>Non-conform</strong>: defined keys were overridden by hand.</>
                : <>Keys set by hand (none overrides a defined key: this does not make the datapack non-conform).</>}
              <ul className="problems">{result.datapack.keys_set_by_hand.map((k: any) => (
                <li key={k.key}><code>{k.key} = {text(k.value)}</code>{k.overrides ? <> overrides <code>{text(k.was)}</code>{k.origin && <> from {k.origin}</>}</> : <> (not defined by a dictionary)</>}</li>
              ))}</ul>
            </div>
          )}
          <p>
            <strong>{result.zip}</strong>: {result.files.length} files, {size(result.files.reduce((n: number, f: any) => n + f.size, 0))}.
            {result.datapack?.draft && <span className="badge warn" title="Generated from uncommitted files"> draft</span>}
          </p>
          {result.baseline && (
            <details className="small">
              <summary>Baseline: the revisions and versions it is built from</summary>
              <pre className="log">{typeof result.baseline === "string" ? result.baseline : JSON.stringify(result.baseline.datapack?.sources || result.baseline, null, 2)}</pre>
            </details>
          )}
          {posted && (
            <div className="banner ok">
              Posted as <button className="link" onClick={() => navigate(`/runs/${posted.id}`)}>{posted.id}</button>
              {posted.conform === false && <> (non-conform: {(posted.problems || []).join("; ")})</>}
            </div>
          )}
          <div className="dialog-browser"><FileBrowser base={`edit/sessions/${sid}/datapack/${result.name}/files`} initial="datapack.yml" /></div>
          <details><summary>Log of the datapack tool</summary><pre className="log">{result.log}</pre></details>
        </>
      )}
    </Dialog>
  );
}

/** The versions of the baseline of a test definition, to change for one generation (datapack --dataset-version):
 *  nothing is written to the files (that is done in the Editor). */
function BaselineVersions({ sid, testdef, config, versions, onChange }: {
  sid: string; testdef: string; config: string | null; versions: Record<string, string>; onChange: (v: Record<string, string>) => void;
}) {
  const [datasets, setDatasets] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    setDatasets(null);
    (async () => {
      try {
        const found: any = await post(`edit/sessions/${sid}/synthesis`, { testdef, setup_config: config, versions: false, run_checks: false });
        if (!current) return;
        const baseline: any[] = found.baseline || [];
        setDatasets(baseline);
        setError(null);
        // the versions of each source, one source at a time (a remote can be slow)
        const pending = [...new Map(baseline.filter((d) => d.versions_pending).map((d) => [`${d.importer}|${d.source}`, d])).values()];
        await Promise.all(pending.map(async (src) => {
          const r: any = await post(`edit/sessions/${sid}/versions`, { importer: src.importer, source: src.source }).catch((e: any) => ({ versions: null, error: e.message }));
          if (!current) return;
          setDatasets((cur) => cur && cur.map((d) => d.importer === src.importer && d.source === src.source
            ? { ...d, versions: r.versions, versions_error: r.error, versions_pending: false } : d));
        }));
      } catch (e: any) {
        if (current) setError(e.message);
      }
    })();
    return () => { current = false; };
  }, [sid, testdef, config]);
  if (datasets && datasets.length === 0 && !error) return null;
  const changed = (datasets || []).filter((d) => (versions[d.id] ?? "").trim() && versions[d.id].trim() !== d.version);
  return (
    <Field label="Baseline versions" help="The version of each dataset the datapack is built from. A version picked here is used **for this generation only** (`datapack --dataset-version`): nothing is written to the files, and a version other than the one written keeps the datapack conform when it is a **tag** of its source (a git tag, a release), and makes it **non-conform** otherwise (a branch, a commit). Both are recorded in its `datapack.yml` and `baseline.yml`. To change a version in the files, edit them in the Editor.">
      {error && <pre className="error-box">{error}</pre>}
      {!datasets && !error && <Spinner text="Reading the baseline…" />}
      {datasets && datasets.length > 0 && (
        <table className="grid compact baseline">
          <thead><tr><th>Dataset</th><th>Version for this generation</th></tr></thead>
          <tbody>
            {datasets.map((d) => {
              const value = versions[d.id] ?? "";
              const overrides = value.trim() !== "" && value.trim() !== d.version;
              return (
                <tr key={d.id}>
                  <td><strong>{d.id}</strong>{d.name && <div className="muted small">{d.name}</div>}
                    {d.versions_pending && <div className="muted small"><Spinner text="listing its versions…" /></div>}
                    {d.versions_error && <div className="muted small" title={d.versions_error}>versions not listed: {d.versions_error}</div>}</td>
                  <td>
                    <div className="version-edit">
                      <input list={`gen-versions-${d.id}`} placeholder={d.version || d.written} value={value}
                        onChange={(e) => onChange({ ...versions, [d.id]: e.target.value })}
                        title="Pick one of the versions available, or type one: for this generation only" />
                      <datalist id={`gen-versions-${d.id}`}>
                        {(d.versions || []).map((v: string) => <option key={v} value={v}>{v === d.version ? "written" : ""}</option>)}
                      </datalist>
                      {value && <button className="small" onClick={() => onChange({ ...versions, [d.id]: "" })} title="Use the version written">×</button>}
                    </div>
                    <div className="small">{overrides ? <span className="warn-text">instead of <code>{d.version ?? d.written}</code></span> : <>written <code>{d.version ?? d.written}</code></>}
                      {d.versions && <span className="muted"> · {d.versions.length} available{d.versions[0] && d.versions[0] !== d.version ? `, latest ${d.versions[0]}` : ""}</span>}</div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {changed.length > 0 && <div className="banner info"><span>{changed.length === 1 ? "A dataset version" : `${changed.length} dataset versions`} other than the one written: the datapack stays conform if {changed.length === 1 ? "it is a tag" : "each is a tag"} of its source (a git tag, a release); otherwise (a branch, a commit) it is <strong>non-conform</strong>. Checked when the dataset is imported.</span></div>}
    </Field>
  );
}

const text = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value);

/** The keys an error of the datapack tool says are undefined ('name' is undefined). */
function undefinedIn(error: string): string[] {
  return [...new Set([...(error || "").matchAll(/'([A-Za-z_]\w*(?:\.\w+)*)' is undefined/g)].map((m) => m[1]))];
}

type Check = { label: string; state: "running" | "ok" | "problems" | "error"; detail?: string; seconds?: number };

/** The checks before generating, as they go: running, then what each found and how long it took. */
function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="gen-checks">
      {checks.map((c, i) => (
        <li key={i} className={c.state}>
          {c.state === "running" ? <Spinner text={`${c.label}…`} /> : <><span className="mark">{c.state === "ok" ? "✓" : "!"}</span> {c.label}</>}
          {c.detail && <span className="muted small"> · {c.detail}</span>}
          {c.seconds !== undefined && <span className="muted small"> · {c.seconds.toFixed(1)} s</span>}
        </li>
      ))}
    </ul>
  );
}

/** How long the generation has been running, and since when its log has not moved. */
function Elapsed({ started, lastLine }: { started: number; lastLine: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const quiet = Math.round((now - lastLine) / 1000);
  return (
    <p className="muted small">
      Running for {Math.round((now - started) / 1000)} s
      {quiet >= 10 && <> · no output for {quiet} s: a long copy or command, or a stuck one (Stop ends it)</>}
    </p>
  );
}

/** The log of the datapack tool, scrolled to its last line while it grows; error lines stand out. */
function LiveLog({ lines }: { lines: string[] }) {
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);
  return (
    <pre className="log live-log" ref={box}>
      {lines.length === 0 ? <span className="muted">Starting…</span> : lines.map((line, i) => (
        <div key={i} className={/\b(ERROR|Error|error:|fatal:|Traceback|failed)\b/.test(line) ? "log-error" : /WARNING|warning:/.test(line) ? "log-warn" : undefined}>{line || "\u00a0"}</div>
      ))}
    </pre>
  );
}
