// Template editor (sequencer README, sections 9 and 13.2): the template text with completion of the datapack
// keys after `{{`, the keys it can use with their values and origins, and a preview rendered as the datapack
// tool would for a component of a test definition.
import { useEffect, useMemo, useState } from "react";
import { post } from "../api";
import { CodeEditor, languageOf } from "../monaco";
import { classes, Markdown } from "../util";
import { useSession, type Doc } from "./state";

export function TemplateEditor({ doc, api }: { doc: Doc; api: { setText: (t: string) => void; flush: () => void } }) {
  const { sid, ctx } = useSession();
  const testdefs: any[] = ctx.workspace?.testdefs || [];
  // a test definition whose setup is near this template is the likely user
  const guess = useMemo(() => {
    const dir = doc.path.split("/templates/")[0];
    return (testdefs.find((t) => t.setup_file && t.setup_file.startsWith(dir)) || testdefs[0])?.path || "";
  }, [doc.path, testdefs]);
  const [testdef, setTestdef] = useState(guess);
  const [component, setComponent] = useState<string | null>(null);
  const [keys, setKeys] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [filter, setFilter] = useState("");
  const [tab, setTab] = useState<"preview" | "keys">("preview");

  useEffect(() => {
    if (!testdef) return;
    setKeys(null);
    post(`edit/sessions/${sid}/keys`, { testdef }).then(setKeys).catch((e) => setKeys({ keys: [], error: e.message }));
  }, [sid, testdef]);

  // the preview follows what is saved: re-render a moment after each change
  useEffect(() => {
    if (!testdef) return;
    const t = window.setTimeout(async () => {
      await api.flush();
      post(`edit/sessions/${sid}/preview`, { testdef, source: doc.path, component }).then(setPreview).catch((e) => setPreview({ ok: false, error: e.message }));
    }, 900);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, testdef, component, doc.content, doc.path]);

  const completions = useMemo(() => {
    if (!keys) return [];
    const list = keys.keys.map((k: any) => ({ label: k.key, detail: k.value, doc: k.origin ? `from ${k.origin}` : "" }));
    for (const [k, v] of Object.entries<string>(keys.datapack || {})) list.push({ label: k, detail: "", doc: v });
    for (const [k, v] of Object.entries<string>(keys.runtime || {})) list.push({ label: k, detail: "at run time", doc: v });
    return list;
  }, [keys]);

  const marker = preview && !preview.ok && preview.line ? [{ line: preview.line, message: preview.error }] : [];
  const shown = (keys?.keys || []).filter((k: any) => !filter || k.key.includes(filter) || String(k.value).includes(filter));
  return (
    <div className="template-editor">
      <div className="template-text">
        <CodeEditor uri={`file:///session/${sid}/${doc.path}`} value={doc.content} onChange={api.setText} onSave={api.flush}
          language={languageOf(doc.path.replace(/\.(j2|jinja2?|tpl|template)$/, ""))} keys={completions} markers={marker} />
      </div>
      <div className="template-side">
        <div className="template-controls">
          <label className="small">for the test definition{" "}
            <select value={testdef} onChange={(e) => setTestdef(e.target.value)}>
              {!testdefs.length && <option value="">none in this session</option>}
              {testdefs.map((t) => <option key={t.path} value={t.path}>{t.name || t.id} ({t.path})</option>)}
            </select>
          </label>
          {preview?.components?.length > 0 && (
            <label className="small">component{" "}
              <select value={component || preview.component || ""} onChange={(e) => setComponent(e.target.value || null)}>
                {preview.components.map((c: string) => <option key={c}>{c}</option>)}
              </select>
            </label>
          )}
          <div className="segmented small">
            <button className={tab === "preview" ? "selected" : ""} onClick={() => setTab("preview")}>Preview</button>
            <button className={tab === "keys" ? "selected" : ""} onClick={() => setTab("keys")}>Keys {keys ? `(${keys.keys.length})` : ""}</button>
          </div>
        </div>
        {!testdefs.length && <p className="muted pad">Keys come from a test definition and its setup: there is none in this session.</p>}
        {tab === "preview" && preview && (
          preview.ok ? (
            <>
              <div className="muted small pad-x">as rendered for <code>{preview.component || "no component"}</code>; keys used: {Object.keys(preview.keys || {}).map((k) => <code key={k} title={preview.keys[k]}>{k} </code>)}</div>
              <pre className="preview">{preview.text}</pre>
            </>
          ) : <pre className="error-box">{preview.error}</pre>
        )}
        {tab === "keys" && keys && (
          <div className="keys-list">
            {keys.error && <div className="banner warn">{keys.error}</div>}
            <input placeholder="filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <Markdown className="small muted" text="Click a key to copy its `{{ }}` form. Values marked *dataset placeholder* are known when the datapack is generated." />
            <table className="grid compact">
              <tbody>
                {shown.map((k: any) => (
                  <tr key={k.key} className={classes("clickable")} onClick={() => navigator.clipboard?.writeText(`{{ ${k.key} }}`)}>
                    <td className="mono">{k.key}</td><td className="mono small">{k.value}</td><td className="muted small">{k.origin}</td>
                  </tr>
                ))}
                {Object.entries<string>(keys.datapack || {}).map(([k, v]) => (
                  <tr key={k}><td className="mono">{k}</td><td colSpan={2} className="muted small">{v}</td></tr>
                ))}
                {Object.entries<string>(keys.runtime || {}).map(([k, v]) => (
                  <tr key={k}><td className="mono">{k}</td><td colSpan={2} className="muted small">at run time: {v}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
