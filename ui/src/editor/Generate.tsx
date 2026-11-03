// Generate a datapack from the session's files (sequencer README, sections 9 and 13.2), look into it, then
// download it or post it to the queue: a way to try a change before pushing it.
import { useEffect, useRef, useState } from "react";
import { get, post, url } from "../api";
import { Choices, Dialog, Field, Toggle } from "../fields";
import { FileBrowser } from "../files";
import { attempt, Markdown, navigate, size } from "../util";
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
  const generate = async () => {
    setBusy(true);
    setResult(null);
    setPosted(null);
    setLines([]);
    setStopping(false);
    setStarted(Date.now());
    setLastLine(Date.now());
    try {
      // in the background: its log comes line by line, subprocess errors included
      const { job } = await post(`edit/sessions/${sid}/datapack/jobs`, { testdef, setup_config: config, nocheck: !check });
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
    if (run) setPosted(run);
  };

  const footer = result?.ok ? (
    <>
      <a className="button" href={url(`edit/sessions/${sid}/datapack/${result.name}`)} download>Download {result.zip}</a>
      <input className="priority" type="number" min={0} max={100} placeholder="priority" value={priority} onChange={(e) => setPriority(e.target.value)} />
      <button className="primary" onClick={submit} disabled={!!posted}>Post to the queue</button>
      <button onClick={generate} disabled={busy}>Generate again</button>
    </>
  ) : (
    <>
      {busy && <button onClick={stop} disabled={!job || stopping} title="Stop the generation: at its next line of output; a command it runs is killed">{stopping ? "Stopping…" : "Stop"}</button>}
      <button className="primary" disabled={!testdef || busy} onClick={generate}>{busy ? "Generating…" : "Generate"}</button>
    </>
  );
  return (
    <Dialog title="Generate a datapack" onClose={onClose} footer={footer} wide>
      {busy && (
        <>
          <LiveLog lines={lines} />
          <Elapsed started={started} lastLine={lastLine} />
        </>
      )}
      {!result && !busy && (
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
          <Field label="Check against the lab" help="Also check that the lab has the stages and roles the setup asks for, and run the checks of each component type, as the queue does on posting.">
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
