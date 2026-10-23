// Generate a datapack from the session's files (sequencer README, sections 9 and 13.2), look into it, then
// download it or post it to the queue: a way to try a change before pushing it.
import { useState } from "react";
import { post, url } from "../api";
import { Choices, Dialog, Field, Toggle } from "../fields";
import { FileBrowser } from "../files";
import { attempt, Markdown, navigate, size } from "../util";
import { useSession } from "./state";

export function GenerateDialog({ onClose }: { onClose: () => void }) {
  const { sid, ctx, info } = useSession();
  const testdefs: any[] = ctx.workspace?.testdefs || [];
  const [testdef, setTestdef] = useState<string>(testdefs[0]?.path || "");
  const td = testdefs.find((t) => t.path === testdef);
  const setup = (ctx.workspace?.setups || []).find((s: any) => s.path === td?.setup_file);
  const configs = Object.keys(setup?.configurations || {});
  const [config, setConfig] = useState<string | null>(null);
  const [check, setCheck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [priority, setPriority] = useState("");
  const [posted, setPosted] = useState<any>(null);

  const generate = async () => {
    setBusy(true);
    setResult(null);
    setPosted(null);
    try {
      setResult(await post(`edit/sessions/${sid}/datapack`, { testdef, setup_config: config, nocheck: !check }));
    } catch (e: any) {
      setResult({ ok: false, error: e.message, log: "" });
    } finally {
      setBusy(false);
    }
  };
  const submit = async () => {
    const run = await attempt(() => post(`edit/sessions/${sid}/datapack/${result.name}/submit`, priority ? { priority: Number(priority) } : {}), "posted to the queue");
    if (run) setPosted(run);
  };

  if (info.kind === "lab")
    return (
      <Dialog title="Generate a datapack" onClose={onClose}>
        <p>Datapacks come from the test definitions of a tests repository: open an editing session on it. A datapack generated there is checked against the lab as it runs now.</p>
      </Dialog>
    );
  const footer = result?.ok ? (
    <>
      <a className="button" href={url(`edit/sessions/${sid}/datapack/${result.name}`)} download>Download {result.zip}</a>
      <input className="priority" type="number" min={0} max={100} placeholder="priority" value={priority} onChange={(e) => setPriority(e.target.value)} />
      <button className="primary" onClick={submit} disabled={!!posted}>Post to the queue</button>
      <button onClick={generate} disabled={busy}>Generate again</button>
    </>
  ) : (
    <button className="primary" disabled={!testdef || busy} onClick={generate}>{busy ? "Generating…" : "Generate"}</button>
  );
  return (
    <Dialog title="Generate a datapack" onClose={onClose} footer={footer} wide>
      {!result && (
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
          {result.log && <details><summary>Log</summary><pre className="log">{result.log}</pre></details>}
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
