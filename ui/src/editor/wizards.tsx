// "+ New…" (sequencer README, section 13.2): new files that start from what the lab and the workspace already
// have, so that a test definition comes with a pipeline that drives the components of its setup.
import { useMemo, useState } from "react";
import { Choices, Combo, Dialog, Field, Text, type Choice } from "../fields";
import { attempt, Markdown, relPath, slug } from "../util";
import { createFile } from "./Session";
import { useSession } from "./state";

type Kind = "testdef" | "setup" | "types" | "subpipelines" | "dictionary" | "template";

const KINDS: Record<Kind, { label: string; description: string; lab?: boolean; workspace?: boolean }> = {
  testdef: { label: "Test definition", description: "a test, from a setup configuration, with a starting pipeline", workspace: true },
  setup: { label: "Setup", description: "the components a family of tests needs and their inputs", workspace: true },
  subpipelines: { label: "Shared sub-pipelines", description: "steps used by several test definitions", workspace: true },
  dictionary: { label: "Key file", description: "keys for the templates and commands", workspace: true },
  template: { label: "Template", description: "an input file rendered with the keys", workspace: true },
  types: { label: "Component types", description: "a file of the lab's type library", lab: true },
};

/** The lab type a datapack component type ends up extending, through the setup's own types. */
function labTypeOf(setup: any, type: string, labTypes: Record<string, any>): string | null {
  let t = type;
  for (let i = 0; i < 10 && t; i++) {
    if (labTypes[t]) return t;
    t = setup.component_types?.[t]?.extends;
  }
  return null;
}

/** A starting pipeline for a setup configuration: deploy and start the components in the order of their
 * needs, wait until they are ready, run, then stop them and collect their outputs in finally. */
export function starterPipeline(setup: any, configName: string, ctx: any): any {
  const cfg = setup.configurations[configName] || { stages: {}, components: {} };
  const labTypes = ctx.lab.types || {};
  const comps = Object.entries<any>(cfg.components);
  const has = (id: string, cmd: string) => {
    const comp = cfg.components[id];
    const own: string[] = setup.component_types?.[comp.type]?.commands || [];
    const lab = labTypeOf(setup, comp.type, labTypes);
    return own.includes(cmd) || Object.keys(labTypes[lab || ""]?.commands || {}).includes(cmd);
  };
  const steps: any[] = [];
  const started: Record<string, string> = {};
  const order: string[] = [];
  const visit = (id: string, seen: Set<string>) => {
    if (order.includes(id) || seen.has(id) || !cfg.components[id]) return;
    seen.add(id);
    for (const n of cfg.components[id].needs || []) visit(n, seen);
    order.push(id);
  };
  for (const [id] of comps) visit(id, new Set());
  for (const id of order) {
    const needs = (cfg.components[id].needs || []).map((n: string) => started[n]).filter(Boolean);
    let last: string | null = null;
    if (has(id, "deploy")) {
      steps.push({ id: `deploy_${id}`, component: id, do: "deploy" });
      last = `deploy_${id}`;
    }
    if (has(id, "start")) {
      steps.push({ id: `start_${id}`, component: id, do: "start", ...((last || needs.length) ? { needs: [...(last ? [last] : []), ...needs] } : {}) });
      last = `start_${id}`;
      if (has(id, "ready")) {
        steps.push({ id: `ready_${id}`, component: id, do: "ready", needs: [last] });
        last = `ready_${id}`;
      }
    }
    if (last) started[id] = last;
  }
  const ends = Object.values(started).filter((s) => !steps.some((x) => (x.needs || []).includes(s)));
  steps.push({ id: "scenario", delay: "60s", ...(ends.length ? { needs: ends } : {}) });
  const fin: any[] = [];
  for (const id of [...order].reverse()) if (has(id, "stop")) fin.push({ id: `stop_${id}`, component: id, do: "stop" });
  const stops = fin.map((s) => s.id);
  for (const id of order) if (has(id, "collect")) fin.push({ id: `collect_${id}`, component: id, do: "collect", ...(stops.length ? { needs: stops } : {}) });
  return {
    phases: [{
      id: "main",
      ...(Object.keys(cfg.stages).length ? { stages: Object.keys(cfg.stages) } : {}),
      ...(comps.some(([, c]) => !c.stage) ? { components: comps.filter(([, c]) => !c.stage).map(([id]) => id) } : {}),
      steps, ...(fin.length ? { finally: fin } : {}),
    }],
  };
}

export function NewFileDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (path: string) => void }) {
  const { sid, ctx, info, files } = useSession();
  const lab = info.kind === "lab";
  const kinds = (Object.keys(KINDS) as Kind[]).filter((k) => (lab ? KINDS[k].lab : KINDS[k].workspace));
  const [kind, setKind] = useState<Kind>(kinds[0]);
  const setups: any[] = ctx.workspace?.setups || [];
  const [setupPath, setSetupPath] = useState<string>(setups[0]?.path || "");
  const setup = setups.find((s) => s.path === setupPath);
  const [config, setConfig] = useState<string>(Object.keys(setups[0]?.configurations || {})[0] || "");
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const typeFolders: string[] = ctx.lab.type_folders || [];
  const folders = useMemo(() => [...new Set(files.map((f) => f.path.split("/").slice(0, -1).join("/")).filter(Boolean))].sort(), [files]);
  const defaultFolder: Record<Kind, string> = {
    testdef: (ctx.workspace?.testdefs?.[0]?.path || "testdef/x/y").split("/").slice(0, -2).join("/") || "testdef",
    setup: (setups[0]?.path || "setup/x").split("/").slice(0, -1).join("/") || "setup",
    subpipelines: "pipelines",
    dictionary: (setups[0]?.path || "setup/x").split("/").slice(0, -1).join("/") || ".",
    template: ((setups[0]?.path || "setup/x").split("/").slice(0, -1).join("/") || ".") + "/templates",
    types: typeFolders[0] || "types",
  };
  const [folder, setFolder] = useState<string | null>(null);
  const dir = folder ?? defaultFolder[kind];
  const fileName = (() => {
    const base = slug(id || name || kind);
    if (kind === "testdef") return `${base}/${base}.yml`;
    if (kind === "template") return name || "input.yml.j2";
    return `${base}.yml`;
  })();
  const path = [dir, fileName].filter(Boolean).join("/").replace(/^\.\//, "");
  const exists = files.some((f) => f.path === path);

  const create = async () => {
    let data: any = null;
    let content: string | undefined;
    if (kind === "testdef") {
      data = {
        id: slug(id || name), name: name || id, setup_file: relPath(path, setup.path), setup_config: config,
        pipeline: starterPipeline(setup, config, ctx),
      };
    } else if (kind === "setup") {
      data = { id: slug(id || name), name: name || id, dictionaries: [], configurations: { default: { stages: {}, components: {} } }, component_types: {} };
    } else if (kind === "types") data = { component_types: {} };
    else if (kind === "subpipelines") data = { subpipelines: {} };
    else if (kind === "dictionary") content = `# Keys for the templates and commands: {{ ${slug(id || name || "section")}.example }}\n${slug(id || name || "section")}:\n  example: value\n`;
    else content = "# Rendered with the datapack keys: type {{ to get them.\n";
    const ok = await attempt(() => createFile(sid, path, data, content), `${path} created`);
    if (ok) onCreated(path);
  };

  const configChoices: Choice[] = Object.entries<any>(setup?.configurations || {}).map(([c, v]) => ({
    value: c, description: `components ${Object.keys(v.components).join(", ") || "none"}`,
  }));
  const canCreate = !exists && (kind !== "testdef" || (setup && config && (id || name)));
  return (
    <Dialog title="New file" onClose={onClose} footer={<button className="primary" disabled={!canCreate} onClick={create}>Create {path}</button>}>
      <Field label="What"><Choices value={kind} onChange={(v) => v && setKind(v as Kind)} choices={kinds.map((k) => ({ value: k, label: KINDS[k].label, description: KINDS[k].description }))} /></Field>
      {kind === "testdef" && (
        <>
          {!setups.length && <div className="banner warn">No setup in this session yet: create one first.</div>}
          <Field label="Setup" help="The test uses one configuration of a setup: its stages and components.">
            <Choices value={setupPath} onChange={(v) => {
              setSetupPath(v || "");
              setConfig(Object.keys(setups.find((s) => s.path === v)?.configurations || {})[0] || "");
            }} choices={setups.map((s) => ({ value: s.path, label: s.name || s.id, description: s.path }))} />
          </Field>
          <Field label="Configuration"><Choices value={config} onChange={(v) => setConfig(v || "")} choices={configChoices} /></Field>
          <Markdown className="small muted" text="The pipeline starts with one phase that locks the stages and components of the configuration: each component is deployed, started and waited for in the order of its `needs`, a `delay` stands for the test itself, and **finally** stops the components and collects their outputs. Only the commands their types have are used." />
        </>
      )}
      {["testdef", "setup", "dictionary"].includes(kind) && (
        <div className="inline-fields">
          {kind !== "dictionary" && <Field label="Name"><Text value={name} onChange={setName} placeholder={kind === "testdef" ? "Front contact at full speed" : "Toy train"} /></Field>}
          <Field label={kind === "dictionary" ? "Section" : "Id"} help={kind === "dictionary" ? "The top-level key of the file." : "Part of the datapack name: letters, digits, _."}>
            <Text mono value={id} onChange={(v) => setId(slug(v))} placeholder={slug(name) || "id"} />
          </Field>
        </div>
      )}
      {kind === "template" && <Field label="File name" help="The extension picks the highlighting: `.yml.j2`, `.ini`, `.sh`…"><Text mono value={name} onChange={setName} placeholder="input.yml.j2" /></Field>}
      {["subpipelines", "types"].includes(kind) && <Field label="File name"><Text mono value={id} onChange={(v) => setId(slug(v))} placeholder={kind} /></Field>}
      <Field label="Folder" error={exists ? `${path} exists` : null} hint={kind === "types" && typeFolders.length ? `type folders of the inventory: ${typeFolders.join(", ")}` : null}>
        <Combo mono value={dir} onChange={setFolder} choices={folders.map((f) => ({ value: f }))} />
      </Field>
    </Dialog>
  );
}
