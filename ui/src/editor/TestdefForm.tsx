// Test definition form (sequencer README, section 8.3): what it is, the setup configuration it uses and its
// key files. The pipeline itself has its own view (PipelineEditor).
import { Combo, Field, helpText, Section, Tags, Text, type Choice } from "../fields";
import { joinPath, Markdown, relPath } from "../util";
import { clone, useSession, type Doc } from "./state";

export function TestdefForm({ doc, api }: { doc: Doc; api: { setData: (d: any) => void } }) {
  const { ctx, schemas, files, open } = useSession();
  const data = doc.data || {};
  const help = (f: string) => helpText(schemas.testdef, [f]);
  const set = (k: string, v: any) => {
    const next = clone(data);
    if (v === undefined || v === "" || (Array.isArray(v) && !v.length)) delete next[k];
    else next[k] = v;
    api.setData(next);
  };
  const setups: any[] = ctx.workspace?.setups || [];
  const setupPath = data.setup_file ? joinPath(doc.path, data.setup_file) : null;
  const setup = setups.find((s) => s.path === setupPath);
  const setupChoices: Choice[] = setups.map((s) => ({
    value: relPath(doc.path, s.path),
    label: relPath(doc.path, s.path),
    description: `${s.name || s.id || s.path} · configurations ${Object.keys(s.configurations).join(", ") || "none"}`,
  }));
  const configChoices: Choice[] = Object.entries<any>(setup?.configurations || {}).map(([name, c]) => ({
    value: name,
    description: `stages ${Object.entries<any>(c.stages).map(([s, t]) => `${s} (${t})`).join(", ") || "none"} · components ${Object.keys(c.components).join(", ") || "none"}`,
  }));
  const config = setup?.configurations?.[data.setup_config];
  const dictChoices: Choice[] = files.filter((f) => f.kind === "dictionary").map((f) => ({ value: relPath(doc.path, f.path), description: f.path }));
  const usedIds = (ctx.workspace?.testdefs || []).filter((t: any) => t.path !== doc.path).map((t: any) => t.id);
  return (
    <div className="form pad">
      <Section title="Test definition">
        <Field label="Id" help={help("id")} error={usedIds.includes(data.id) ? "another test definition has this id" : null}>
          <Text mono value={data.id} onChange={(v) => set("id", v)} />
        </Field>
        <Field label="Name" help={help("name")}><Text value={data.name} onChange={(v) => set("name", v)} /></Field>
        <Field label="Description" help={help("description")} wide><Text multiline value={data.description} onChange={(v) => set("description", v)} /></Field>
      </Section>
      <Section title="Setup" help="The setup says which stages and components the test needs; the configuration picks one of its sets.">
        <Field label="Setup file" help={help("setup_file")} error={data.setup_file && !setup ? "no setup at this path in the session" : null}>
          <Combo mono value={data.setup_file} onChange={(v) => set("setup_file", v)} choices={setupChoices} strict
            onPick={(v) => {
              const s = setups.find((x) => x.path === joinPath(doc.path, v));
              const configs = Object.keys(s?.configurations || {});
              const next = clone(data);
              next.setup_file = v;
              if (!configs.includes(next.setup_config) && configs.length) next.setup_config = configs[0];
              api.setData(next);
            }} />
          {setup && <button className="link small" onClick={() => open(setup.path)}>open {setup.path}</button>}
        </Field>
        <Field label="Configuration" help={help("setup_config")}
          error={setup && data.setup_config && !config ? `not a configuration of ${setup.id || setup.path}` : null}>
          <Combo value={data.setup_config} onChange={(v) => set("setup_config", v)} choices={configChoices} strict={!!setup} />
        </Field>
        {config && (
          <table className="grid compact">
            <thead><tr><th>Component</th><th>Datapack type</th><th>Configuration</th><th>Stage</th><th>Role</th><th>Needs</th></tr></thead>
            <tbody>
              {Object.entries<any>(config.components).map(([id, c]) => (
                <tr key={id}>
                  <td><code>{id}</code> <span className="muted small">{c.name}</span></td>
                  <td>{c.type}</td><td>{c.configuration}</td>
                  <td>{c.stage ? `${c.stage} (${config.stages[c.stage] || "?"})` : <span className="muted">ad hoc</span>}</td>
                  <td>{c.role}</td><td className="small">{(c.needs || []).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      <Section title="Key files" collapsed={!data.dictionaries?.length}
        help="YAML files whose keys the templates and commands use, on top of those of the setup: `{{ section.key }}`. A later file overrides an earlier one.">
        <Field label="Dictionaries" help={help("dictionaries")}><Tags value={data.dictionaries} onChange={(v) => set("dictionaries", v)} choices={dictChoices} /></Field>
      </Section>
      <Section title="Pipeline">
        <Markdown text={`${(data.pipeline?.phases || []).length} phase(s): ${(data.pipeline?.phases || []).map((p: any) => `\`${p.id}\``).join(", ") || "none yet"}. Open the **Pipeline** view above to edit it as a graph.`} />
      </Section>
    </div>
  );
}
