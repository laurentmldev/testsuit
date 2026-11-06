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
import { attempt, Markdown, navigate, size, Spinner } from "../util";
import { useSession } from "./state";

export function GenerateDialog({ onClose }: { onClose: () => void }) {
  const { sid, ctx, info } = useSession();
  if (info.kind === "lab")
    return (
      <Dialog title="Generate a datapack" onClose={onClose}>
        <p>Datapacks come from the test definitions of a tests repository: open an editing session on it. A datapack generated there is checked against the lab as it runs now.</p>
      </Dialog>
    );
  return <GenerateDatapack sid={sid} workspace={ctx.workspace} onClose={onClose} />;
}

/** Generate a datapack of a session's test definitions; testdef and config preselect one (Datapacks page). */
export function GenerateDatapack({ sid, workspace, testdef: initial, config: initialConfig, onClose }: {
  sid: string; workspace: any; testdef?: string; config?: string | null; onClose: () => void;
}) {
  const testdefs: any[] = workspace?.testdefs || [];
  const [testdef, setTestdef] = useState<string>(initial || testdefs[0]?.path || "");
  const td = testdefs.find((t) => t.path === testdef);
  const setup = (workspace?.setups || []).find((s: any) => s.path === td?.setup_file);
  const configs = Object.keys(setup?.configurations || {});
  const [config, setConfig] = useState<string | null>(initialConfig && initialConfig !== td?.setup_config ? initialConfig : null);
  const [check, setCheck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
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
          () => post(`edit/sessions/${sid}/run_problems`, body), () => "against the lab as it is now");
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
      const { job } = await post(`edit/sessions/${sid}/datapack/jobs`, { testdef, setup_config: config, nocheck: !check, lenient });
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
      <button className="primary" disabled={!testdef || busy || checking} onClick={request}>{busy ? "Generating…" : checking ? "Checking…" : "Generate"}</button>
    </>
  );
  return (
    <Dialog title="Generate a datapack" onClose={onClose} footer={footer} wide>
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
          <Field label="Check against the lab" help="Also check that the lab has the lab stages and roles the setup asks for, and run the checks of each slot type, as the queue does on posting.">
            <Toggle value={check} onChange={setCheck} label="check (slower)" />
          </Field>
        </>
      )}
      {result && !result.ok && (
        <>
          <pre className="error-box">{result.error}</pre>
          {result.log && <details open><summary>Log</summary><LiveLog lines={result.log.split("\n")} /></details>}
          <button onClick={() => setResult(null)}>Back</button>
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
