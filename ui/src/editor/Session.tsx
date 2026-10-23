// An editing session: its files by kind, the form or YAML view of the open file, the help panel,
// and the session actions (check everything, generate a datapack, review and push).
import { useMemo, useState } from "react";
import { del, post, put } from "../api";
import { HelpPanel, HelpProvider } from "../fields";
import { CodeEditor } from "../monaco";
import { attempt, classes, Markdown, navigate, notify, setQuery, type Route } from "../util";
import { SessionContext, useDoc, useSession, useSessionData, type Doc, type FileInfo, type Problem, type Session } from "./state";
import { InventoryForm } from "./InventoryForm";
import { TypesForm } from "./TypesForm";
import { SetupForm } from "./SetupForm";
import { TestdefForm } from "./TestdefForm";
import { PipelineEditor } from "./PipelineEditor";
import { TemplateEditor } from "./TemplateEditor";
import { GenerateDialog } from "./Generate";
import { PublishDialog } from "./Publish";
import { NewFileDialog } from "./wizards";

const KIND_LABELS: [string, string, string][] = [
  ["inventory", "Lab inventory", "Stages, ad hoc components and their variables (Ansible YAML inventory)."],
  ["types", "Component types", "The lab's type library: commands, status probe and self-test of each kind of component."],
  ["testdef", "Test definitions", "A test: its setup configuration, key files and pipeline."],
  ["setup", "Setups", "Inputs of each component, datapack component types and the configurations tests choose from."],
  ["subpipelines", "Shared sub-pipelines", "Groups of steps used by several test definitions (pipeline.include)."],
  ["dictionary", "Key files", "Keys used by the templates (dictionaries)."],
  ["template", "Templates", "Input files rendered with the keys (Jinja2)."],
  ["yaml", "Other YAML", ""],
  ["other", "Other files", ""],
];

const SCHEMA_OF: Record<string, string> = { inventory: "inventory", types: "types", setup: "setup", testdef: "testdef", subpipelines: "subpipelines" };

function FileTree({ files, current, problems, onNew }: { files: FileInfo[]; current: string | null; problems: Problem[]; onNew: () => void }) {
  const { open } = useSession();
  const [showOther, setShowOther] = useState(false);
  const counts = useMemo(() => {
    const c: Record<string, { e: number; w: number }> = {};
    for (const p of problems) {
      c[p.file] ||= { e: 0, w: 0 };
      if (p.severity === "error") c[p.file].e++;
      else c[p.file].w++;
    }
    return c;
  }, [problems]);
  return (
    <div className="file-tree">
      <button className="primary small wide" onClick={onNew}>+ New…</button>
      {KIND_LABELS.map(([kind, label, help]) => {
        const list = files.filter((f) => f.kind === kind);
        if (!list.length || (kind === "other" && !showOther)) {
          return kind === "other" && list.length ? (
            <div key={kind} className="tree-group"><button className="link small" onClick={() => setShowOther(true)}>{list.length} other files…</button></div>
          ) : null;
        }
        return (
          <div key={kind} className="tree-group">
            <div className="tree-group-title" title={help}>{label}</div>
            {list.map((f) => (
              <div key={f.path} className={classes("tree-row", current === f.path && "selected")} onClick={() => open(f.path)} title={f.path}>
                <span className="tree-name">{f.path}</span>
                {counts[f.path]?.e ? <span className="count bad">{counts[f.path].e}</span> : counts[f.path]?.w ? <span className="count warn">{counts[f.path].w}</span> : null}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function Problems({ problems, onPick }: { problems: Problem[]; onPick: (p: Problem) => void }) {
  if (!problems.length) return <div className="problems ok">No problem found.</div>;
  return (
    <div className="problems">
      {problems.map((p, i) => (
        <div key={i} className={classes("problem", p.severity)} onClick={() => onPick(p)}>
          <span className="where">{p.file}{p.line ? `:${p.line}` : ""}</span> {p.message}
        </div>
      ))}
    </div>
  );
}

function DocView({ session, doc, view, setView, api, revealLine }: {
  session: Session; doc: Doc; view: string; setView: (v: string) => void; api: ReturnType<typeof useDoc>; revealLine: number | null;
}) {
  const schema = SCHEMA_OF[doc.kind] ? session.schemas[SCHEMA_OF[doc.kind]] : undefined;
  const hasForm = ["inventory", "types", "setup", "testdef"].includes(doc.kind);
  const hasPipeline = ["testdef", "subpipelines"].includes(doc.kind);
  const views: [string, string][] = [
    ...(hasForm ? [["form", "Form"] as [string, string]] : []),
    ...(hasPipeline ? [["pipeline", "Pipeline"] as [string, string]] : []),
    ...(doc.kind === "template" ? [["template", "Template and preview"] as [string, string]] : []),
    ["yaml", doc.kind === "template" || doc.kind === "other" ? "Text" : "YAML"],
  ];
  const current = views.some(([v]) => v === view) ? view : views[0][0];
  const parsed = doc.data !== undefined && doc.data !== null;
  let body;
  if (current === "yaml" || (!parsed && current !== "template")) {
    body = (
      <CodeEditor uri={`file:///session/${session.sid}/${doc.path}`} value={doc.content} onChange={api.setText} schema={schema}
        markers={doc.problems.filter((p) => p.file === doc.path)} onSave={api.flush} revealLine={revealLine} />
    );
  } else if (current === "template") body = <TemplateEditor doc={doc} api={api} />;
  else if (current === "pipeline") body = <PipelineEditor doc={doc} api={api} />;
  else if (doc.kind === "inventory") body = <InventoryForm doc={doc} api={api} />;
  else if (doc.kind === "types") body = <TypesForm doc={doc} api={api} />;
  else if (doc.kind === "setup") body = <SetupForm doc={doc} api={api} />;
  else if (doc.kind === "testdef") body = <TestdefForm doc={doc} api={api} />;
  return (
    <div className="doc-view">
      <div className="doc-head">
        <code className="doc-path">{doc.path}</code>
        <div className="segmented">
          {views.map(([v, label]) => <button key={v} className={current === v ? "selected" : ""} onClick={() => setView(v)}>{label}</button>)}
        </div>
        <span className={classes("save-state", api.state)}>{{ saved: "saved in the session", pending: "editing…", saving: "saving…", error: "not saved" }[api.state]}</span>
        {doc.aliases && current !== "yaml" && <span className="badge info" title="This file uses YAML aliases (*name). A form change to an aliased part gives that place a copy of its own.">aliases</span>}
      </div>
      {api.error && <div className="error-box">{api.error}</div>}
      {!parsed && current !== "yaml" && current !== "template" && <div className="banner warn">The file does not parse as YAML: fix it in the YAML view first.</div>}
      <div className="doc-body">{body}</div>
    </div>
  );
}

export function SessionPage({ id, route }: { id: string; route: Route }) {
  const path = route.query.get("file");
  const view = route.query.get("view") || "form";
  const [revealLine, setRevealLine] = useState<number | null>(null);
  const openFile = (p: string, line?: number) => {
    setQuery("file", p);
    setRevealLine(line || null);
  };
  const session = useSessionData(id, openFile);
  const [allProblems, setAllProblems] = useState<Problem[]>([]);
  const [dialog, setDialog] = useState<string | null>(null);
  const api = useDoc(id, path, (saved) => {
    setAllProblems((all) => [...all.filter((p) => p.file !== saved.path), ...(saved.problems || [])]);
    if (["inventory", "types", "setup", "testdef", "subpipelines"].includes(saved.kind)) session?.refreshContext();
    session?.refreshFiles();
  });
  if (!session) return <div className="page muted">Opening the session…</div>;
  const doc = api.doc;
  const validateAll = async () => {
    const result = await attempt(() => post(`edit/sessions/${id}/validate`));
    if (result) {
      setAllProblems(result.problems);
      notify(result.ok ? `no error${result.problems.length ? `, ${result.problems.length} warning(s)` : ""}` : `${result.problems.filter((p: Problem) => p.severity === "error").length} error(s)`, result.ok ? "ok" : "error");
    }
  };
  const problems = doc ? [...doc.problems, ...allProblems.filter((p) => p.file !== doc.path)] : allProblems;
  const info = session.info;
  return (
    <SessionContext.Provider value={session}>
      <HelpProvider>
        {(topic) => (
          <div className="session">
            <div className="session-bar">
              <a href="#/edit" className="muted">Editor</a>
              <strong>{info.repo === "lab" ? "Lab" : `Tests (${info.folder})`}</strong>
              <span className="muted">branch <code>{info.branch}</code> · {info.changed ? `${info.changed} changed file(s)` : "no change yet"}</span>
              {session.ctx.lab_error && <span className="badge bad" title={session.ctx.lab_error}>the lab files do not load: helpers use the running lab</span>}
              <span className="spacer" />
              <button onClick={validateAll} title="Check every file, as tsq lab reload and tsq validate would">Check everything</button>
              {info.kind === "workspace" && <button onClick={() => setDialog("generate")} title="Run the datapack tool on a test definition, as datapack <testdef> would">Generate a datapack</button>}
              <button className="primary" onClick={() => setDialog("publish")}>Review and push…</button>
              <button onClick={async () => {
                if (info.changed && !window.confirm("Close the session and drop the changes that were not pushed?")) return;
                await attempt(() => del(`edit/sessions/${id}`));
                navigate("edit");
              }}>Close</button>
            </div>
            <div className="session-main">
              <FileTree files={session.files} current={path} problems={problems} onNew={() => setDialog("new")} />
              <div className="session-center">
                {!path && <SessionWelcome session={session} />}
                {path && !doc && !api.error && <p className="muted pad">Loading {path}…</p>}
                {path && api.error && !doc && <div className="error-box">{api.error}</div>}
                {doc && <DocView session={session} doc={doc} view={view} setView={(v) => setQuery("view", v)} api={api} revealLine={revealLine} />}
              </div>
              <div className="session-side">
                <HelpPanel topic={topic} fallback={<KindHelp kind={doc?.kind} />} />
                <h4>Problems</h4>
                <Problems problems={problems} onPick={(p) => {
                  openFile(p.file, p.line);
                  setQuery("view", "yaml");
                }} />
              </div>
            </div>
            {dialog === "generate" && <GenerateDialog onClose={() => setDialog(null)} />}
            {dialog === "publish" && <PublishDialog onClose={() => {
              setDialog(null);
              session.refreshFiles();
            }} />}
            {dialog === "new" && <NewFileDialog onClose={() => setDialog(null)} onCreated={(p) => {
              setDialog(null);
              session.refreshFiles().then(() => openFile(p));
            }} />}
          </div>
        )}
      </HelpProvider>
    </SessionContext.Provider>
  );
}

const KIND_HELP: Record<string, string> = {
  inventory: "**Lab inventory.** Stages are groups with `tsq_stage_type`: datapacks ask for a stage by type and get any free one. Each component of a stage has a `tsq_role`, which datapacks bind to. Hosts directly under `all` are **ad hoc** components (simulations, the sequencer host). Pick an item on the left of the form to edit it.",
  types: "**Component types.** A type gives a kind of component its commands (`do: <command>` in pipelines), its status probe and its self-test. A type `extends` another and redefines only what differs: start from `linux_system` for anything reachable over SSH.",
  setup: "**Setup.** A configuration lists the logical stages (by stage type) and components (by role) a test needs; each component has a datapack type, which `extends` a lab type and says which input files it gets. Use **Add from the lab** to create components for every role of a stage type.",
  testdef: "**Test definition.** It picks a setup configuration and gives the pipeline: phases that lock stages and components, and steps linked by `needs`. Open the **Pipeline** view to edit it as a graph.",
  subpipelines: "**Shared sub-pipelines.** Steps grouped under a name, used with `uses: <name>` by several test definitions that list this file in `pipeline.include`.",
  template: "**Template.** Rendered with the datapack keys when the datapack is generated. Type `{{` to get the keys, and look at the preview.",
  dictionary: "**Key file.** Its keys are available to every template and command: `{{ section.key }}`.",
};

function KindHelp({ kind }: { kind?: string }) {
  return <Markdown text={(kind && KIND_HELP[kind]) || "Pick a file on the left, or create one with **+ New…**. Every field shows its help here when you point at it."} />;
}

function SessionWelcome({ session }: { session: Session }) {
  const { ctx, files, open } = session;
  const lab = session.info.kind === "lab";
  const inventory = ctx.inventory;
  return (
    <div className="welcome pad">
      <h3>{lab ? "Editing the lab" : "Editing the tests"}</h3>
      {lab ? (
        <>
          <p>This session edits a clone of the lab repository. Start with:</p>
          <ul>
            {inventory && <li><button className="link" onClick={() => open(inventory)}>the inventory ({inventory})</button>: stages, components, roles</li>}
            {files.filter((f) => f.kind === "types").map((f) => <li key={f.path}><button className="link" onClick={() => open(f.path)}>{f.path}</button>: component types</li>)}
          </ul>
        </>
      ) : (
        <>
          <p>This session edits a clone of a tests repository. Test definitions:</p>
          <ul>
            {(ctx.workspace?.testdefs || []).map((t: any) => <li key={t.path}><button className="link" onClick={() => open(t.path)}>{t.name || t.id}</button> <span className="muted">{t.path}</span></li>)}
          </ul>
          <p>Use <strong>+ New…</strong> to create a test definition from a setup configuration, with a starting pipeline built from the components it uses.</p>
        </>
      )}
    </div>
  );
}

// exported for the dialogs that create files
export async function createFile(sid: string, path: string, data: any, content?: string) {
  return put(`edit/sessions/${sid}/files/${path}`, content !== undefined ? { content } : { data });
}
