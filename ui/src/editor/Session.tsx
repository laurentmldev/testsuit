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

// An editing session: its files by kind, the form or YAML view of the open file, the help panel,
// and the session actions (check everything, generate a datapack, review and push).
import { useEffect, useMemo, useRef, useState } from "react";
import { del, post, put } from "../api";
import { HelpPanel, HelpProvider, KeysProvider } from "../fields";
import { CodeEditor, yamlSections } from "../monaco";
import { attempt, classes, Markdown, navigate, notify, setQuery, Spinner, type Route } from "../util";
import { runtimeKeyItems, SessionContext, useDoc, useSession, useSessionData, type Doc, type FileInfo, type Problem, type Session } from "./state";
import { InventoryForm } from "./InventoryForm";
import { LibraryForm } from "./LibraryForm";
import { SetupForm } from "./SetupForm";
import { TestdefForm } from "./TestdefForm";
import { PipelineEditor } from "./PipelineEditor";
import { TemplateEditor } from "./TemplateEditor";
import { GenerateDialog } from "./Generate";
import { PublishDialog } from "./Publish";
import { NewFileDialog } from "./wizards";

const KIND_LABELS: [string, string, string][] = [
  ["inventory", "Lab inventory", "Stages, ad hoc components and their variables (Ansible YAML inventory)."],
  ["library", "Lab roles and components", "Roles: the commands a component offers. Components: how they implement them, status probe and self-test."],
  ["testdef", "Test definitions", "A test: its setup configuration, key files and pipeline."],
  ["setup", "Setups", "Slots and their inputs, slot types, and the configurations tests choose from."],
  ["subpipelines", "Shared sub-pipelines", "Groups of steps used by several test definitions (pipeline.include)."],
  ["dictionary", "Key files", "Keys used by the templates (dictionaries)."],
  ["template", "Templates", "Input files rendered with the keys (Jinja2)."],
  ["yaml", "Other YAML", ""],
  ["other", "Other files", ""],
];

const SCHEMA_OF: Record<string, string> = { inventory: "inventory", library: "library", setup: "setup", testdef: "testdef", subpipelines: "subpipelines" };

// what one file of a kind is called, shown on the header of the open file
const KIND_NAME: Record<string, string> = {
  inventory: "Lab inventory", library: "Roles and components", testdef: "Test definition", setup: "Setup",
  subpipelines: "Sub-pipelines", dictionary: "Key file", template: "Template", yaml: "YAML", other: "File",
};

/** The folder (with its trailing slash) and the name of a path. */
function splitPath(path: string): [string, string] {
  const i = path.lastIndexOf("/");
  return [path.slice(0, i + 1), path.slice(i + 1)];
}

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
      <div className="file-tree-new"><button className="primary small wide" onClick={onNew}>+ New…</button></div>
      {KIND_LABELS.map(([kind, label, help]) => {
        const list = files.filter((f) => f.kind === kind);
        const hasCurrent = list.some((f) => f.path === current);
        if (!list.length || (kind === "other" && !showOther && !hasCurrent)) {
          return kind === "other" && list.length ? (
            <div key={kind} className={`tree-group kind-${kind}`}><button className="link small tree-more" onClick={() => setShowOther(true)}>{list.length} other files…</button></div>
          ) : null;
        }
        return (
          <div key={kind} className={classes(`tree-group kind-${kind}`, hasCurrent && "has-current")}>
            <div className="tree-group-title" title={help}>
              <span className="kind-dot" aria-hidden="true" />{label}<span className="tree-group-count">{list.length}</span>
            </div>
            {list.map((f) => {
              const [dir, base] = splitPath(f.path);
              const selected = current === f.path;
              return (
                <div key={f.path} className={classes("tree-row", selected && "selected")} onClick={() => open(f.path)} title={f.path} aria-current={selected ? "page" : undefined}>
                  <span className="tree-name">{dir && <span className="tree-dir">{dir}</span>}<span className="tree-base">{base}</span></span>
                  {counts[f.path]?.e ? <span className="count bad">{counts[f.path].e}</span> : counts[f.path]?.w ? <span className="count warn">{counts[f.path].w}</span> : null}
                </div>
              );
            })}
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
  const hasForm = ["inventory", "library", "setup", "testdef"].includes(doc.kind);
  const hasPipeline = ["testdef", "subpipelines"].includes(doc.kind);
  const views: [string, string][] = [
    ...(hasForm ? [["form", "Form"] as [string, string]] : []),
    ...(hasPipeline ? [["pipeline", "Pipeline"] as [string, string]] : []),
    ...(doc.kind === "template" ? [["template", "Template and preview"] as [string, string]] : []),
    ["yaml", doc.kind === "template" || doc.kind === "other" ? "Text" : "YAML"],
  ];
  const current = views.some(([v]) => v === view) ? view : views[0][0];
  const parsed = doc.data !== undefined && doc.data !== null;
  // the YAML view of a YAML file lists its top-level sections, to jump to one
  const [jump, setJump] = useState<{ line: number } | null>(null);
  useEffect(() => setJump(null), [doc.path, current]);
  const isYaml = /\.ya?ml$/.test(doc.path) && doc.kind !== "template";
  const sections = useMemo(() => (isYaml ? yamlSections(doc.content) : []), [isYaml, doc.content]);
  const showYaml = current === "yaml" || (!parsed && current !== "template");
  const [dir, base] = splitPath(doc.path);
  let body;
  if (showYaml) {
    body = (
      <CodeEditor uri={`file:///session/${session.sid}/${doc.path}`} value={doc.content} onChange={api.setText} schema={schema}
        markers={doc.problems.filter((p) => p.file === doc.path)} onSave={api.flush} revealLine={revealLine} reveal={jump} sections={isYaml} />
    );
  } else if (current === "template") body = <TemplateEditor doc={doc} api={api} />;
  else if (current === "pipeline") body = <PipelineEditor doc={doc} api={api} />;
  else if (doc.kind === "inventory") body = <InventoryForm doc={doc} api={api} />;
  else if (doc.kind === "library") body = <LibraryForm doc={doc} api={api} />;
  else if (doc.kind === "setup") body = <SetupForm doc={doc} api={api} />;
  else if (doc.kind === "testdef") body = <TestdefForm doc={doc} api={api} />;
  return (
    <div className={`doc-view kind-${doc.kind}`}>
      <div className="doc-head">
        <span className="kind-chip" title={KIND_LABELS.find(([k]) => k === doc.kind)?.[2] || undefined}>{KIND_NAME[doc.kind] || doc.kind}</span>
        <span className="doc-path" title={doc.path}>{dir && <span className="doc-dir">{dir}</span>}<span className="doc-base">{base}</span></span>
        <div className="segmented">
          {views.map(([v, label]) => <button key={v} className={current === v ? "selected" : ""} onClick={() => setView(v)}>{label}</button>)}
        </div>
        {current !== "yaml" && current !== "template" && (
          <span className="undo">
            <button className="small" onClick={api.undo} disabled={!api.canUndo} title="Undo (Ctrl+Z)">↶ Undo</button>
            <button className="small" onClick={api.redo} disabled={!api.canRedo} title="Redo (Ctrl+Shift+Z or Ctrl+Y)">↷ Redo</button>
          </span>
        )}
        <span className={classes("save-state", api.state)}>{{ saved: "saved in the session", pending: "editing…", saving: "saving…", error: "not saved" }[api.state]}</span>
        {doc.aliases && current !== "yaml" && <span className="badge info" title="This file uses YAML aliases (*name). A form change to an aliased part gives that place a copy of its own.">aliases</span>}
      </div>
      {showYaml && sections.length > 1 && (
        <nav className="section-bar" aria-label="Sections of the file">
          <span className="section-bar-title">Sections</span>
          {sections.map((sec) => <button key={sec.line} className="section-chip" onClick={() => setJump({ line: sec.line })} title={`Go to line ${sec.line}`}>{sec.key}</button>)}
        </nav>
      )}
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
  const [checking, setChecking] = useState(false);
  const api = useDoc(id, path, (saved) => {
    setAllProblems((all) => [...all.filter((p) => p.file !== saved.path), ...(saved.problems || [])]);
    if (["inventory", "library", "setup", "testdef", "subpipelines"].includes(saved.kind)) session?.refreshContext();
    session?.refreshFiles();
  });
  // Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y) undo and redo the form and pipeline edits; the YAML and text
  // editors keep their own undo.
  const keys = useRef(api);
  keys.current = api;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k !== "z" && k !== "y") return;
      if ((e.target as HTMLElement | null)?.closest?.(".monaco-editor")) return;
      if (document.querySelector(".dialog")) return;
      e.preventDefault();
      if (k === "y" || e.shiftKey) keys.current.redo();
      else keys.current.undo();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  // the browser tab says what it edits (the Lab page and the Datapacks page open the Editor in a tab of its own)
  const repoOf = session?.info.repo;
  useEffect(() => {
    if (!repoOf) return;
    const before = document.title;
    document.title = repoOf === "lab" ? "Edit Lab" : "Edit Datapacks";
    return () => { document.title = before; };
  }, [repoOf]);
  if (!session) return <div className="page"><Spinner className="block" text="Opening the session: reading its files, the lab and the schemas…" /></div>;
  const doc = api.doc;
  const validateAll = async () => {
    setChecking(true);
    const result = await attempt(() => post(`edit/sessions/${id}/validate`));
    setChecking(false);
    if (result) {
      setAllProblems(result.problems);
      notify(result.ok ? `no error${result.problems.length ? `, ${result.problems.length} warning(s)` : ""}` : `${result.problems.filter((p: Problem) => p.severity === "error").length} error(s)`, result.ok ? "ok" : "error");
    }
  };
  const problems = doc ? [...doc.problems, ...allProblems.filter((p) => p.file !== doc.path)] : allProblems;
  const info = session.info;
  // where a session belongs: the Lab page for the lab, the Datapacks page (on its repository) for tests
  const back = info.repo === "lab" ? { path: "lab", label: "Lab" } : { path: "datapacks", label: "Datapacks", query: { repo: info.repo } };
  // leaving a lab session offers to reload the lab, so that tsqd runs what was edited: the folder as it is
  // (in place), or the latest pushed commit (a clone on a branch, once pushed)
  const leave = async () => {
    if (info.repo === "lab" && window.confirm(info.local
      ? "Reload the lab now, so that the sequencer uses the files as they are?"
      : "Reload the lab now from its latest pushed commit (tsq lab reload --pull)?")) {
      await attempt(() => post("lab/reload", { pull: !info.local, revision: null }), "lab reloaded");
    }
    navigate(back.path, back.query);
  };
  return (
    <SessionContext.Provider value={session}>
      <HelpProvider>
        {(topic) => (
          <div className="session">
            <div className="session-bar">
              <button className="link muted" onClick={leave} title={`Back to the ${back.label} page`}>{back.label}</button>
              <strong>{info.repo === "lab" ? "Lab" : `Tests (${info.folder})`}</strong>
              {info.local && <span className="badge info" title={`Files are saved directly in ${info.source}, where the sequencer reads them. Closing the session keeps them.`}>in place</span>}
              {info.git === false ? <span className="muted">not a git work tree: files only</span> : (
                <span className="muted">branch <code>{info.branch || "detached"}</code> · {info.changed ? `${info.changed} changed file(s)` : "no change yet"}</span>
              )}
              {session.ctx.lab_error && <span className="badge bad" title={session.ctx.lab_error}>the lab files do not load as they are (see the problems)</span>}
              <span className="spacer" />
              <button onClick={validateAll} disabled={checking} title="Check every file, as tsq lab reload and tsq validate would">{checking ? <><span className="spinner" aria-hidden="true" />Checking…</> : "Check everything"}</button>
              {info.kind === "workspace" && <button onClick={() => setDialog("generate")} title="Run the datapack tool on a test definition, as datapack <testdef> would">Generate a datapack</button>}
              {info.git !== false && <button className="primary" onClick={() => setDialog("publish")}>Review and push…</button>}
              <button onClick={async () => {
                if (!info.local && info.changed && !window.confirm("Close the session and drop the changes that were not pushed?")) return;
                await attempt(() => del(`edit/sessions/${id}`));
                await leave();
              }} title={`Close the session and go back to the ${back.label} page`}>Close</button>
            </div>
            <div className="session-main">
              <FileTree files={session.files} current={path} problems={problems} onNew={() => setDialog("new")} />
              <div className="session-center">
                {!path && <SessionWelcome session={session} />}
                {path && !doc && !api.error && <div className="pad"><Spinner text={`Loading and checking ${path}…`} /></div>}
                {path && api.error && !doc && <div className="error-box">{api.error}</div>}
                {doc && (
                  <KeysProvider keys={runtimeKeyItems(session.ctx)}>
                    <DocView session={session} doc={doc} view={view} setView={(v) => setQuery("view", v)} api={api} revealLine={revealLine} />
                  </KeysProvider>
                )}
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
  inventory: "**Lab inventory.** Stages are groups with `tsq_stage_type`: datapacks ask for a stage by type and get any free one. Each component extends a lab component (`tsq_extends`), which implements the commands of a lab role; it has the role of that component, or picks one of its roles with `tsq_role`. Datapacks bind by role. Hosts directly under `all` are **ad hoc** components (simulations, the sequencer host). Pick an item on the left of the form to edit it.",
  library: "**Lab roles and components.** A **role** is abstract: the commands a component offers (`do: <command>` in pipelines), each with what it does; datapacks bind to it. A **component** is concrete: the roles it supports, how it implements their commands, its status probe and its self-test; hosts extend one (`tsq_extends`) and have its role, or pick one of its roles (`tsq_role`). Both `extend` another and redefine only what differs: start from `linux_system` for anything reachable over SSH.",
  setup: "**Setup.** A configuration lists the slots a test needs, each bound at run time to a lab component of its role (a lab role, which gives its commands), and the stage slots that group slots in one lab stage (by stage type). A slot's type says which input files it gets. Use **Add from the lab** to create slots for every role of a lab stage type.",
  testdef: "**Test definition.** It picks a setup configuration and gives the pipeline: phases that lock stage slots and slots, and steps linked by `needs`. Open the **Pipeline** view to edit it as a graph.",
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
          <p>This session edits {session.info.local ? <>the lab folder in place (<code>{session.info.source}</code>)</> : "a clone of the lab repository"}. Start with:</p>
          <ul>
            {inventory && <li><button className="link" onClick={() => open(inventory)}>the inventory ({inventory})</button>: stages, components, roles</li>}
            {files.filter((f) => f.kind === "library").map((f) => <li key={f.path}><button className="link" onClick={() => open(f.path)}>{f.path}</button>: lab roles and components</li>)}
          </ul>
        </>
      ) : (
        <>
          <p>This session edits {session.info.local ? <>the tests folder in place (<code>{session.info.source}</code>)</> : "a clone of a tests repository"}. Test definitions:</p>
          <ul>
            {(ctx.workspace?.testdefs || []).map((t: any) => <li key={t.path}><button className="link" onClick={() => open(t.path)}>{t.name || t.id}</button> <span className="muted">{t.path}</span></li>)}
          </ul>
          <p>Use <strong>+ New…</strong> to create a test definition from a setup configuration, with a starting pipeline built from the slots it uses.</p>
        </>
      )}
    </div>
  );
}

// exported for the dialogs that create files
export async function createFile(sid: string, path: string, data: any, content?: string) {
  return put(`edit/sessions/${sid}/files/${path}`, content !== undefined ? { content } : { data });
}
