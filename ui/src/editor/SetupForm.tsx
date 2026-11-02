// Setup form (sequencer README, sections 5 and 8): where the inputs come from, the datapack component types
// with their inputs and commands, and the configurations: logical stages and components bound by role.
import { useEffect, useState } from "react";
import { post } from "../api";
import { Choices, Combo, Dialog, Field, helpText, KeyValues, Section, Tags, Text, Toggle, type Choice } from "../fields";
import { attempt, classes, freeName, Markdown, relPath, slug } from "../util";
import { CommandsEditor } from "./commands";
import { clone, roleChoices, stageTypeChoices, typeChoices, useSession, type Doc } from "./state";

type Sel = { kind: "general" } | { kind: "data" } | { kind: "type"; name: string } | { kind: "config"; name: string };

export function SetupForm({ doc, api }: { doc: Doc; api: { setData: (d: any) => void } }) {
  const data = doc.data || {};
  const [sel, setSel] = useState<Sel>({ kind: Object.keys(data.configurations || {}).length ? "config" : "general", name: Object.keys(data.configurations || {})[0] } as Sel);
  const [wizard, setWizard] = useState<string | null>(null);
  const update = (fn: (d: any) => void) => {
    const draft = clone(data);
    fn(draft);
    api.setData(draft);
  };
  const types: Record<string, any> = data.component_types || {};
  const configs: Record<string, any> = data.configurations || {};
  return (
    <div className="form-split">
      <div className="form-nav">
        <div className={classes("nav-item", sel.kind === "general" && "selected")} onClick={() => setSel({ kind: "general" })}>General</div>
        <div className={classes("nav-item", sel.kind === "data" && "selected")} onClick={() => setSel({ kind: "data" })}>Data repositories ({(data.datarepositories || []).length})</div>
        <div className="nav-title">Configurations <button className="small" onClick={() => {
          const name = freeName("configuration", Object.keys(configs));
          update((d) => {
            d.configurations ||= {};
            d.configurations[name] = { stages: {}, components: {} };
          });
          setSel({ kind: "config", name });
        }}>+</button></div>
        {Object.keys(configs).map((c) => (
          <div key={c} className={classes("nav-item", sel.kind === "config" && sel.name === c && "selected")} onClick={() => setSel({ kind: "config", name: c })}>
            <strong>{c}</strong> <span className="muted small">{Object.keys(configs[c]?.components || {}).length} components</span>
          </div>
        ))}
        <div className="nav-title">Component types <button className="small" onClick={() => setWizard("type")}>+</button></div>
        {Object.keys(types).map((t) => (
          <div key={t} className={classes("nav-item", sel.kind === "type" && sel.name === t && "selected")} onClick={() => setSel({ kind: "type", name: t })}>
            <strong>{t}</strong> <span className="muted small">{types[t]?.extends}</span>
          </div>
        ))}
      </div>
      <div className="form-main">
        {sel.kind === "general" && <General doc={doc} data={data} update={update} />}
        {sel.kind === "data" && <DataRepos data={data} update={update} />}
        {sel.kind === "type" && types[sel.name] !== undefined && (
          <DatapackType doc={doc} name={sel.name} value={types[sel.name]} update={update} onRenamed={(n) => setSel({ kind: "type", name: n })} onDeleted={() => setSel({ kind: "general" })} />
        )}
        {sel.kind === "config" && configs[sel.name] !== undefined && (
          <Configuration name={sel.name} data={data} update={update} onAddFromLab={() => setWizard("lab:" + sel.name)}
            onOpenType={(t) => setSel({ kind: "type", name: t })} onRenamed={(n) => setSel({ kind: "config", name: n })} onDeleted={() => setSel({ kind: "general" })} />
        )}
      </div>
      {wizard === "type" && <NewDatapackType data={data} onClose={() => setWizard(null)} onCreate={(name, body) => {
        update((d) => {
          d.component_types ||= {};
          d.component_types[name] = body;
        });
        setSel({ kind: "type", name });
        setWizard(null);
      }} />}
      {wizard?.startsWith("lab:") && <FromLab data={data} config={wizard.slice(4)} onClose={() => setWizard(null)} onApply={(draft) => {
        api.setData(draft);
        setWizard(null);
      }} />}
    </div>
  );
}

function useSetupHelp() {
  const { schemas } = useSession();
  return (path: (string | number)[]) => helpText(schemas.setup, path);
}

function General({ doc, data, update }: { doc: Doc; data: any; update: (fn: (d: any) => void) => void }) {
  const { files } = useSession();
  const help = useSetupHelp();
  const dictionaries = files.filter((f) => f.kind === "dictionary").map((f) => ({ value: relPath(doc.path, f.path), description: f.path }));
  return (
    <div>
      <h3>Setup</h3>
      <Field label="Id" help={help(["id"])}><Text value={data.id} onChange={(v) => update((d) => (d.id = slug(v)))} /></Field>
      <Field label="Name" help={help(["name"])}><Text value={data.name} onChange={(v) => update((d) => (d.name = v || undefined))} /></Field>
      <Field label="Key files" help={help(["dictionaries"])} wide
        hint={<>Templates allowed: <code>{"platforms/{{ testdef.setup_config }}.yml"}</code> picks a file per configuration (what a platform changes, such as addresses).</>}>
        <Tags value={data.dictionaries} onChange={(v) => update((d) => (d.dictionaries = v.length ? v : undefined))}
          choices={[...dictionaries, { value: "platforms/{{ testdef.setup_config }}.yml", description: "one file per setup configuration" }]} />
      </Field>
    </div>
  );
}

function DataRepos({ data, update }: { data: any; update: (fn: (d: any) => void) => void }) {
  const { ctx } = useSession();
  const help = useSetupHelp();
  const repos: any[] = data.datarepositories || [];
  const importers: Choice[] = (ctx.importers || []).map((i: string) => ({ value: i, description: i === "git" ? "a git repository: version is a tag or commit" : "plugin importer" }));
  return (
    <div>
      <h3>Data repositories</h3>
      <Markdown text={help(["datarepositories"])} />
      {repos.map((repo, i) => (
        <Section key={i} title={`Repository ${repo.id || i + 1}`} actions={<button className="small danger" onClick={() => update((d) => d.datarepositories.splice(i, 1))}>Remove</button>}>
          <div className="inline-fields">
            <Field label="Id"><Text value={repo.id} onChange={(v) => update((d) => (d.datarepositories[i].id = slug(v)))} /></Field>
            <Field label="Local folder" help={help(["datarepositories", 0, "path"])}><Text mono value={repo.path} onChange={(v) => update((d) => (d.datarepositories[i].path = v))} /></Field>
          </div>
          {(repo.datasources || []).map((src: any, j: number) => (
            <div key={j} className="sub-form">
              <div className="inline-fields">
                <Field label="Source id"><Text value={src.id} onChange={(v) => update((d) => (d.datarepositories[i].datasources[j].id = slug(v)))} /></Field>
                <Field label="Importer" help={help(["datarepositories", 0, "datasources", 0, "importer"])}><Combo value={src.importer} onChange={(v) => update((d) => (d.datarepositories[i].datasources[j].importer = v))} choices={importers} /></Field>
              </div>
              <Field label="Read from" help={help(["datarepositories", 0, "datasources", 0, "source"])}><Text mono value={src.source} onChange={(v) => update((d) => (d.datarepositories[i].datasources[j].source = v))} /></Field>
              <Field label="Datasets" help="Each dataset gives the keys `<id>.path` and `<id>.version` to the templates.">
                <table className="grid compact">
                  <tbody>
                    {(src.datasets || []).map((ds: any, k: number) => (
                      <tr key={k}>
                        <td><input value={ds.id || ""} placeholder="id" onChange={(e) => update((d) => (d.datarepositories[i].datasources[j].datasets[k].id = slug(e.target.value)))} /></td>
                        <td><input className="mono" value={ds.version || ""} placeholder="version, e.g. {{ sw_version }}" onChange={(e) => update((d) => (d.datarepositories[i].datasources[j].datasets[k].version = e.target.value))} /></td>
                        <td><button className="icon" onClick={() => update((d) => d.datarepositories[i].datasources[j].datasets.splice(k, 1))}>×</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <button className="small" onClick={() => update((d) => ((d.datarepositories[i].datasources[j].datasets ||= []).push({ id: "dataset", version: "" })))}>+ Dataset</button>
              </Field>
            </div>
          ))}
          <button className="small" onClick={() => update((d) => ((d.datarepositories[i].datasources ||= []).push({ id: "source", importer: "git", source: "", datasets: [] })))}>+ Data source</button>
        </Section>
      ))}
      <button className="small" onClick={() => update((d) => ((d.datarepositories ||= []).push({ id: "repository", path: "{{ setup.path }}/../.work/datarepo", datasources: [] })))}>+ Data repository</button>
    </div>
  );
}

function sourceChoices(session: ReturnType<typeof useSession>, doc: Doc, data: any): Choice[] {
  const out: Choice[] = session.files.filter((f) => f.kind === "template").map((f) => ({ value: relPath(doc.path, f.path), description: `template file ${f.path}` }));
  for (const repo of data.datarepositories || [])
    for (const src of repo.datasources || [])
      for (const ds of src.datasets || []) if (ds.id) out.push({ value: `{{ ${ds.id}.path }}/`, description: `the folder of dataset ${ds.id} (version ${ds.version})` });
  out.push({ value: "{{ testdef.path }}/", description: "the folder of the test definition being built" });
  return out;
}

function DatapackType({ doc, name, value, update, onRenamed, onDeleted }: {
  doc: Doc; name: string; value: any; update: (fn: (d: any) => void) => void; onRenamed: (n: string) => void; onDeleted: () => void;
}) {
  const session = useSession();
  const { ctx } = session;
  const help = useSetupHelp();
  const t = value || {};
  const set = (k: string, v: any) => update((d) => {
    if (v === undefined || v === "") delete d.component_types[name][k];
    else d.component_types[name][k] = v;
  });
  const lab = t.extends ? ctx.lab.types[t.extends] : null;
  const usedBy = Object.entries<any>((doc.data?.configurations) || {}).flatMap(([c, cfg]) => Object.entries<any>(cfg?.components || {}).filter(([, comp]) => comp?.type === name).map(([id]) => `${id} (${c})`));
  const [newName, setNewName] = useState(name);
  useEffect(() => setNewName(name), [name]);         // another item picked, or created
  const configs: Record<string, any> = t.configurations || {};
  const sources = sourceChoices(session, doc, doc.data || {});
  return (
    <div>
      <div className="form-title">
        <h3>Datapack type <input className="inline-name" key={name} value={newName} onChange={(e) => setNewName(slug(e.target.value))} onBlur={() => {
          if (!newName || newName === name || newName in (doc.data.component_types || {})) return;
          update((d) => {
            d.component_types = Object.fromEntries(Object.entries(d.component_types).map(([k, v]) => [k === name ? newName : k, v]));
            for (const cfg of Object.values<any>(d.configurations || {})) for (const comp of Object.values<any>(cfg?.components || {})) if (comp?.type === name) comp.type = newName;
          });
          onRenamed(newName);
        }} /></h3>
        <span className="muted small">{usedBy.length ? `used by ${usedBy.join(", ")}` : "not used yet"}</span>
        <button className="small danger" onClick={() => {
          if (!window.confirm(`Delete type ${name}?`)) return;
          update((d) => delete d.component_types[name]);
          onDeleted();
        }}>Delete</button>
      </div>
      <Field label="Runs on (extends)" help={help(["component_types", "_", "extends"])}>
        <Combo value={t.extends} onChange={(v) => set("extends", v)} choices={typeChoices(ctx)} strict />
      </Field>
      <Field label="Description" help={help(["component_types", "_", "description"])}><Text value={t.description} onChange={(v) => set("description", v)} /></Field>
      <Section title="Inputs, by configuration" help={help(["component_types", "_", "configurations"])}
        actions={<button className="small" onClick={() => set("configurations", { ...configs, [freeName("standard", Object.keys(configs))]: { inputs: [] } })}>+ Configuration</button>}>
        {Object.entries(configs).map(([cfg, c]) => (
          <div key={cfg} className="sub-form">
            <div className="form-title">
              <strong>{cfg}</strong>
              {Object.keys(configs).length > 1 && <button className="small" onClick={() => {
                const next = { ...configs };
                delete next[cfg];
                set("configurations", next);
              }}>Remove</button>}
            </div>
            <Inputs doc={doc} type={name} value={c?.inputs || []} sources={sources} onChange={(inputs) => set("configurations", { ...configs, [cfg]: { ...c, inputs } })} />
          </div>
        ))}
      </Section>
      <CommandsEditor title="Commands added for this test" help={help(["component_types", "_", "commands"])}
        value={t.commands} onChange={(v) => set("commands", Object.keys(v).length ? v : undefined)}
        inherited={lab ? Object.fromEntries(Object.entries<any>(lab.commands).map(([k, v]) => [k, { spec: v.spec, from: `lab type ${v.from}` }])) : undefined} />
    </div>
  );
}

const METHODS: Choice[] = [
  { value: "template", description: "rendered with the datapack keys (Jinja2)" },
  { value: "copy", description: "copied as is (software, data files)" },
  { value: "generate", description: "produced by a generator from the source and the keys" },
];

function Inputs({ doc, type, value, sources, onChange }: { doc: Doc; type: string; value: any[]; sources: Choice[]; onChange: (v: any[]) => void }) {
  const { ctx } = useSession();
  const [preview, setPreview] = useState<string | null>(null);
  const set = (i: number, k: string, v: any) => onChange(value.map((inp, j) => {
    if (j !== i) return inp;
    const next = { ...inp, [k]: v };
    if (v === undefined || v === "" || v === false || (k === "method" && v === "template")) delete next[k];
    return next;
  }));
  return (
    <>
      <table className="grid compact inputs">
        <thead><tr><th>Source</th><th>Target in the input folder</th><th>Method</th><th /></tr></thead>
        <tbody>
          {value.map((inp, i) => (
            <tr key={i}>
              <td><Combo mono value={inp.source} onChange={(v) => set(i, "source", v)} choices={sources} placeholder="templates/config.yml" onPick={(v) => {
                if (!inp.target) set(i, "target", v.endsWith("/") ? "app/" : v.split("/").pop());
              }} /></td>
              <td><input className="mono" value={inp.target || ""} placeholder="config.yml or app/" onChange={(e) => set(i, "target", e.target.value)} /></td>
              <td>
                <select value={inp.method || "template"} onChange={(e) => set(i, "method", e.target.value)} title={METHODS.find((m) => m.value === (inp.method || "template"))?.description}>
                  {METHODS.map((m) => <option key={m.value} value={m.value}>{m.value}</option>)}
                </select>
                {inp.method === "generate" && <Combo value={inp.generator} onChange={(v) => set(i, "generator", v)} placeholder="generator or script.py"
                  choices={(ctx.generators || []).map((g: string) => ({ value: g, description: "registered generator" }))} />}
                <Toggle value={inp.optional} onChange={(v) => set(i, "optional", v)} label="optional" />
              </td>
              <td>
                {(inp.method || "template") === "template" && !String(inp.source || "").includes("{{") && <button className="small" onClick={() => setPreview(inp.source)}>Preview</button>}
                <button className="icon" onClick={() => onChange(value.filter((_, j) => j !== i))}>×</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="small" onClick={() => onChange([...value, { source: "", target: "" }])}>+ Input</button>
      {preview && <PreviewDialog setupPath={doc.path} type={type} source={preview} onClose={() => setPreview(null)} />}
    </>
  );
}

/** Rendered template, for a test definition using this setup (the keys come from it). */
export function PreviewDialog({ setupPath, source, type, onClose }: { setupPath: string; source: string; type?: string; onClose: () => void }) {
  const { sid, ctx } = useSession();
  const testdefs = (ctx.workspace?.testdefs || []).filter((t: any) => t.setup_file === setupPath);
  const [testdef, setTestdef] = useState(testdefs[0]?.path || "");
  const [result, setResult] = useState<any>(null);
  const setupDir = setupPath.split("/").slice(0, -1).join("/");
  const path = source.startsWith("/") ? source : [setupDir, source].filter(Boolean).join("/");
  const run = async (td: string) => {
    setTestdef(td);
    const setup = (ctx.workspace?.setups || []).find((s: any) => s.path === setupPath);
    const cfg = setup?.configurations?.[testdefs.find((t: any) => t.path === td)?.setup_config];
    const component = Object.entries<any>(cfg?.components || {}).find(([, c]) => c.type === type)?.[0];
    setResult(await attempt(() => post(`edit/sessions/${sid}/preview`, { testdef: td, source: path, component })));
  };
  useEffect(() => {
    if (testdef) run(testdef);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <Dialog title={`Preview of ${source}`} onClose={onClose} wide>
      {!testdefs.length && <p>No test definition uses this setup yet: the keys come from a test definition. Create one first.</p>}
      {testdefs.length > 1 && <Field label="With the keys of"><Choices value={testdef} onChange={(v) => v && run(v)} choices={testdefs.map((t: any) => ({ value: t.path, label: t.name || t.id }))} /></Field>}
      {result && !result.ok && <pre className="error-box">{result.error}</pre>}
      {result?.ok && <>
        <p className="muted small">component {result.component || "?"} · keys used: {Object.keys(result.keys || {}).map((k) => <code key={k}>{k}</code>)}</p>
        <pre className="preview">{result.text}</pre>
      </>}
    </Dialog>
  );
}

function NewDatapackType({ data, onClose, onCreate }: { data: any; onClose: () => void; onCreate: (name: string, body: any) => void }) {
  const { ctx } = useSession();
  const [base, setBase] = useState("linux_system");
  const [name, setName] = useState(freeName("my_app", Object.keys(data.component_types || {})));
  const taken = Object.keys(data.component_types || {});
  return (
    <Dialog title="New datapack component type" onClose={onClose} footer={<button className="primary" disabled={!name || taken.includes(name)} onClick={() => onCreate(name, { extends: base, configurations: { standard: { inputs: [] } } })}>Create</button>}>
      <Field label="Runs on" help="The lab type the component binds to (it must be that type, or derive from it). Its commands are inherited."><Combo value={base} onChange={setBase} choices={typeChoices(ctx)} strict onPick={(b) => setName(freeName(`${b}_app`, taken))} /></Field>
      <Field label="Name" help="What the component is in the test (`pilot_app`, `exploit`)." error={taken.includes(name) ? "already used" : null}><Text value={name} onChange={(v) => setName(slug(v))} /></Field>
    </Dialog>
  );
}

function Configuration({ name, data, update, onAddFromLab, onOpenType, onRenamed, onDeleted }: {
  name: string; data: any; update: (fn: (d: any) => void) => void; onAddFromLab: () => void; onOpenType: (t: string) => void;
  onRenamed: (n: string) => void; onDeleted: () => void;
}) {
  const { ctx } = useSession();
  const help = useSetupHelp();
  const cfg = data.configurations[name] || {};
  const stages: Record<string, any> = cfg.stages || {};
  const comps: Record<string, any> = cfg.components || {};
  const types: Record<string, any> = data.component_types || {};
  const stageType = (s: string) => (typeof stages[s] === "object" ? stages[s]?.type : stages[s]);
  const setComp = (id: string, k: string, v: any) => update((d) => {
    const c = d.configurations[name].components[id];
    if (v === undefined || v === "" || (Array.isArray(v) && !v.length)) delete c[k];
    else c[k] = v;
  });
  const typeOptions: Choice[] = Object.entries(types).map(([t, def]) => ({ value: t, description: `extends \`${def?.extends || "?"}\` · configurations ${Object.keys(def?.configurations || {}).join(", ")}` }));
  const [newName, setNewName] = useState(name);
  useEffect(() => setNewName(name), [name]);         // another item picked, or created
  const aliased = Object.entries<any>(data.configurations).some(([other, c]) => other !== name && JSON.stringify(c) === JSON.stringify(cfg));
  const addComponent = (stage: string | null) => {
    // the id is the user's: the name the test definition and its keys use for this component
    const typed = window.prompt("Id of the new component (as the pipeline and the keys will name it):");
    const id = slug(typed || "");
    if (!id) return;
    if (id in comps) return window.alert(`There is already a component '${id}' in this configuration.`);
    update((d) => {
      d.configurations[name].components ||= {};
      const t = Object.keys(types)[0];
      d.configurations[name].components[id] = { type: t || "", configuration: Object.keys(types[t]?.configurations || {})[0] || "standard", ...(stage ? { stage } : {}), role: "" };
    });
  };
  /** The logical components of one stage (or the ad hoc ones), with roles narrowed to that stage and to each component's type. */
  const componentsTable = (ids: string[], stage: string | null) => {
    if (!ids.length) return <p className="muted small">No component yet.</p>;
    const st = stage ? stageType(stage) : null;
    return (
      <table className="grid compact components">
        <thead><tr><th>Id</th><th>Name</th><th>Type</th><th>Configuration</th><th>Role</th><th>Needs</th><th /></tr></thead>
        <tbody>
          {ids.map((id) => {
            const c = comps[id];
            const labType = types[c?.type]?.extends;
            const roles = roleChoices(ctx, st ? { stageType: st, labType } : { adhoc: true, labType });
            const typeConfigs = Object.keys(types[c?.type]?.configurations || {});
            const roleType = st ? ctx.lab.stage_type_roles?.[st]?.[c?.role] : ctx.lab.adhoc_roles?.[c?.role];
            const mismatch = labType && roleType && !(ctx.lab.types[roleType]?.chain || []).includes(labType);
            return (
              <tr key={id}>
                <td><code>{id}</code>{!stage && c?.stage && <div className="error-text small">stage '{c.stage}' is not declared</div>}</td>
                <td><input value={c?.name || ""} onChange={(e) => setComp(id, "name", e.target.value)} /></td>
                <td>
                  <Combo value={c?.type} onChange={(v) => setComp(id, "type", v)} choices={typeOptions} strict />
                  {c?.type && types[c.type] && <button className="link small" onClick={() => onOpenType(c.type)}>open</button>}
                </td>
                <td><Combo value={c?.configuration} onChange={(v) => setComp(id, "configuration", v)} choices={typeConfigs.map((x) => ({ value: x }))} strict /></td>
                <td>
                  <Combo value={c?.role} onChange={(v) => setComp(id, "role", v)} choices={roles} />
                  {mismatch && <div className="error-text small">the lab's {c.role} is a {roleType}, not a {labType}</div>}
                </td>
                <td><Tags value={c?.needs} onChange={(v) => setComp(id, "needs", v)} choices={Object.keys(comps).filter((x) => x !== id).map((x) => ({ value: x }))} placeholder="+" /></td>
                <td><button className="icon" onClick={() => update((d) => delete d.configurations[name].components[id])}>×</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  };
  /** A stage's components: one row per role its lab stages offer, ticked when the test uses it. Ticking
   * adds a component for that role (with a datapack type extending the role's lab type), unticking
   * removes it. Components of the stage with another role follow. */
  const stageTable = (stage: string) => {
    const st = stageType(stage);
    const labRoles: [string, string][] = Object.entries<any>(ctx.lab.stage_type_roles?.[st] || {});
    const inStage = Object.keys(comps).filter((id) => comps[id]?.stage === stage);
    const byRole = (r: string) => inStage.find((id) => comps[id]?.role === r);
    const others = inStage.filter((id) => !labRoles.some(([r]) => r === comps[id]?.role));
    const typeFor = (labType: string, role: string) => Object.entries(types).find(([, t]) => t?.extends === labType)?.[0] || `${slug(role)}_app`;
    const tick = (role: string, labType: string, on: boolean) => update((d) => {
      const c = d.configurations[name];
      c.components ||= {};
      if (!on) {
        for (const [id, x] of Object.entries<any>(c.components)) if (x?.stage === stage && x?.role === role) delete c.components[id];
        return;
      }
      d.component_types ||= {};
      const t = typeFor(labType, role);
      if (!d.component_types[t]) d.component_types[t] = { extends: labType, configurations: { standard: { inputs: [] } } };
      const id = freeName(slug(role), Object.keys(c.components));
      c.components[id] = { name: role, type: t, configuration: Object.keys(d.component_types[t].configurations || { standard: 1 })[0], stage, role };
    });
    const cells = (id: string) => {
      const c = comps[id];
      const labType = types[c?.type]?.extends;
      const roleType = ctx.lab.stage_type_roles?.[st]?.[c?.role];
      const mismatch = labType && roleType && !(ctx.lab.types[roleType]?.chain || []).includes(labType);
      return (
        <>
          <td><code>{id}</code></td>
          <td><input value={c?.name || ""} onChange={(e) => setComp(id, "name", e.target.value)} /></td>
          <td>
            <Combo value={c?.type} onChange={(v) => setComp(id, "type", v)} choices={typeOptions} strict />
            {mismatch && <div className="error-text small">the lab's {c.role} is a {roleType}, not a {labType}</div>}
            {c?.type && types[c.type] && <button className="link small" onClick={() => onOpenType(c.type)}>open</button>}
          </td>
          <td><Combo value={c?.configuration} onChange={(v) => setComp(id, "configuration", v)} choices={Object.keys(types[c?.type]?.configurations || {}).map((x) => ({ value: x }))} strict /></td>
          <td><Tags value={c?.needs} onChange={(v) => setComp(id, "needs", v)} choices={Object.keys(comps).filter((x) => x !== id).map((x) => ({ value: x }))} placeholder="+" /></td>
        </>
      );
    };
    if (!labRoles.length && !others.length) return <p className="muted small">No lab stage of this type, so no component to pick.</p>;
    return (
      <table className="grid compact components">
        <thead><tr><th className="tick" /><th>Role</th><th>Id</th><th>Name</th><th>Type</th><th>Configuration</th><th>Needs</th></tr></thead>
        <tbody>
          {labRoles.map(([role, labType]) => {
            const id = byRole(role);
            return (
              <tr key={role} className={id ? "" : "unused"}>
                <td className="tick"><input type="checkbox" checked={!!id} title={id ? "the test uses it: untick to drop it" : "tick to use it"} onChange={(e) => tick(role, labType, e.target.checked)} /></td>
                <td>{role}<div className="muted small"><code>{labType}</code></div></td>
                {id ? cells(id) : <td colSpan={5} className="muted small">not used by this test</td>}
              </tr>
            );
          })}
          {others.map((id) => (
            <tr key={id}>
              <td className="tick"><button className="icon" title="remove" onClick={() => update((d) => delete d.configurations[name].components[id])}>×</button></td>
              <td>
                <Combo value={comps[id]?.role} onChange={(v) => setComp(id, "role", v)} choices={roleChoices(ctx, { stageType: st, labType: types[comps[id]?.type]?.extends })} />
                <div className="error-text small">{comps[id]?.role ? "no lab stage of this type has this role" : "no role"}</div>
              </td>
              {cells(id)}
            </tr>
          ))}
        </tbody>
      </table>
    );
  };
  return (
    <div>
      <div className="form-title">
        <h3>Configuration <input className="inline-name" key={name} value={newName} onChange={(e) => setNewName(slug(e.target.value))} onBlur={() => {
          if (!newName || newName === name || newName in data.configurations) return;
          update((d) => (d.configurations = Object.fromEntries(Object.entries(d.configurations).map(([k, v]) => [k === name ? newName : k, v]))));
          onRenamed(newName);
        }} /></h3>
        <button className="small" onClick={() => {
          const copy = freeName(`${name}_copy`, Object.keys(data.configurations));
          update((d) => (d.configurations[copy] = clone(cfg)));
          onRenamed(copy);
        }}>Duplicate</button>
        <button className="small danger" onClick={() => {
          if (!window.confirm(`Delete configuration ${name}?`)) return;
          update((d) => delete d.configurations[name]);
          onDeleted();
        }}>Delete</button>
      </div>
      {aliased && <div className="hint-box small">Same contents as another configuration (likely a YAML alias): changing it here gives it a copy of its own.</div>}
      <Markdown text={help(["configurations", "_"])} className="muted" />
      <Section title="Stages" help={help(["configurations", "_", "stages"])}
        actions={<button className="small primary" onClick={onAddFromLab}>Add from the lab…</button>}>
        {!Object.keys(stages).length && <p className="muted small">No logical stage: the test only uses ad hoc components.</p>}
        {Object.keys(stages).map((s) => {
          const st = stageType(s);   // one fold per logical stage, with its components
          const inStage = Object.keys(comps).filter((id) => comps[id]?.stage === s);
          return (
            <Section key={s} title={<><code>{s}</code> <span className="muted small">{st || "no stage type"} · {inStage.length} component{inStage.length === 1 ? "" : "s"}</span></>}
              actions={<button className="small danger" onClick={() => {
                if (inStage.length && !window.confirm(`Remove logical stage ${s} and its components ${inStage.join(", ")}?`)) return;
                update((d) => {
                  delete d.configurations[name].stages[s];
                  for (const id of inStage) delete d.configurations[name].components[id];
                });
              }}>Remove</button>}>
              <Field label="Stage type" help="Bound at run time to any free lab stage of this type.">
                <Combo value={st} onChange={(v) => update((d) => (d.configurations[name].stages[s] = { type: v }))} choices={stageTypeChoices(ctx)} />
                {!ctx.lab.stage_type_roles?.[st] && <div className="error-text small">no lab stage of this type</div>}
              </Field>
              {stageTable(s)}
            </Section>
          );
        })}
        <button className="small" onClick={() => update((d) => {
          // a new logical stage uses every role of its lab stages: untick the ones the test does not need
          const c = d.configurations[name];
          c.stages ||= {};
          c.components ||= {};
          d.component_types ||= {};
          const type = stageTypeChoices(ctx)[0]?.value || "";
          const stage = freeName(slug(type || "stage"), Object.keys(stages));
          c.stages[stage] = { type };
          for (const [role, labType] of Object.entries<any>(ctx.lab.stage_type_roles?.[type] || {})) {
            const t = Object.entries<any>(d.component_types).find(([, x]) => x?.extends === labType)?.[0] || `${slug(role)}_app`;
            d.component_types[t] ||= { extends: labType, configurations: { standard: { inputs: [] } } };
            const id = freeName(slug(role), Object.keys(c.components));
            c.components[id] = { name: role, type: t, configuration: Object.keys(d.component_types[t].configurations || { standard: 1 })[0], stage, role };
          }
        })}>+ Logical stage</button>
      </Section>
      <Section title="Ad hoc components" help={help(["configurations", "_", "components"])}
        actions={<button className="small" onClick={onAddFromLab}>Add from the lab…</button>}>
        {componentsTable(Object.keys(comps).filter((id) => !comps[id]?.stage || !(comps[id].stage in stages)), null)}
        <button className="small" onClick={() => addComponent(null)}>+ Component</button>
      </Section>
      <Section title="Command overrides per component" collapsed help={help(["configurations", "_", "components", "_", "commands"])}>
        {Object.entries(comps).map(([id, c]) => (
          <CommandsEditor key={id} title={`${id}`} value={c?.commands} onChange={(v) => setComp(id, "commands", Object.keys(v).length ? v : undefined)} />
        ))}
      </Section>
      <Section title="Other keys" collapsed>
        <KeyValues value={cfg} onChange={(v) => update((d) => (d.configurations[name] = v))} exclude={["stages", "components"]} />
      </Section>
    </div>
  );
}

/** Adds a logical stage of a lab stage type and a component per role, with datapack types extending the lab types. */
function FromLab({ data, config, onClose, onApply }: { data: any; config: string; onClose: () => void; onApply: (draft: any) => void }) {
  const { ctx } = useSession();
  const stageTypes = Object.keys(ctx.lab.stage_type_roles || {});
  const [mode, setMode] = useState<string>(stageTypes.length ? "stage" : "adhoc");
  const [stageType, setStageType] = useState(stageTypes[0] || "");
  const cfg = data.configurations?.[config] || {};
  const [logical, setLogical] = useState(freeName(slug(stageTypes[0] || "stage"), Object.keys(cfg.stages || {})));
  const roles: [string, string][] = mode === "stage" ? Object.entries<any>(ctx.lab.stage_type_roles?.[stageType] || {}) : Object.entries<any>(ctx.lab.adhoc_roles || {});
  const existing = new Set(Object.values<any>(cfg.components || {}).filter((c) => (mode === "stage" ? c?.stage === logical : !c?.stage)).map((c) => c?.role));
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const isPicked = (r: string) => picked[r] ?? (!existing.has(r) && (mode === "stage" || r !== "sequencer"));
  const types: Record<string, any> = data.component_types || {};
  const typeFor = (labType: string, role: string) => Object.entries(types).find(([, t]) => t?.extends === labType)?.[0] || `${slug(role)}_app`;
  const apply = () => {
    const draft = clone(data);
    draft.configurations ||= {};
    draft.configurations[config] ||= {};
    const c = draft.configurations[config];
    c.components ||= {};
    if (mode === "stage") {
      c.stages ||= {};
      c.stages[logical] = { type: stageType };
    }
    draft.component_types ||= {};
    for (const [role, labType] of roles) {
      if (!isPicked(role)) continue;
      const t = typeFor(labType, role);
      if (!draft.component_types[t]) draft.component_types[t] = { extends: labType, configurations: { standard: { inputs: [] } } };
      const id = freeName(slug(role), Object.keys(c.components));
      c.components[id] = { name: role, type: t, configuration: Object.keys(draft.component_types[t].configurations || { standard: 1 })[0], ...(mode === "stage" ? { stage: logical } : {}), role };
    }
    onApply(draft);
  };
  return (
    <Dialog title={`Add components from the lab to ${config}`} onClose={onClose} wide footer={<button className="primary" onClick={apply} disabled={!roles.some(([r]) => isPicked(r))}>Add them</button>}>
      <Field label="From"><Choices value={mode} onChange={(v) => v && setMode(v)} choices={[
        { value: "stage", label: "a stage type", description: "a logical stage, bound at run time to any free lab stage of that type, with a component per role" },
        { value: "adhoc", label: "ad hoc components", description: "components in no stage, bound by role: simulations, the sequencer host for the analysis" },
      ]} /></Field>
      {mode === "stage" && (
        <>
          <Field label="Stage type"><Choices value={stageType} onChange={(v) => {
            if (!v) return;
            setStageType(v);
            setLogical(freeName(slug(v), Object.keys(cfg.stages || {})));
          }} choices={stageTypeChoices(ctx).filter((c) => stageTypes.includes(c.value))} /></Field>
          <Field label="Logical stage id" help="The name the pipeline phases use (`stages: [train]`)."><Text value={logical} onChange={(v) => setLogical(slug(v))} /></Field>
        </>
      )}
      <table className="grid compact">
        <thead><tr><th /><th>Role</th><th>Lab type</th><th>Datapack type</th></tr></thead>
        <tbody>
          {roles.map(([role, labType]) => (
            <tr key={role}>
              <td><input type="checkbox" checked={isPicked(role)} onChange={(e) => setPicked({ ...picked, [role]: e.target.checked })} /></td>
              <td>{role}{existing.has(role) && <span className="muted small"> (already there)</span>}</td>
              <td><code>{labType}</code></td>
              <td><code>{typeFor(labType, role)}</code>{!types[typeFor(labType, role)] && <span className="muted small"> new, extends {labType}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">New datapack types get a <code>standard</code> configuration without inputs: add the files each component needs in its type afterwards.</p>
    </Dialog>
  );
}
