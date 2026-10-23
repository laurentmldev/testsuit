// Pipeline editor (sequencer README, sections 8.3, 8.4 and 13.2): a graph of the steps of a phase or of a
// sub-pipeline, where `needs` are the edges; a palette of what a step can do on each logical component
// (the commands its types give), a form per step, the phase resources, and the flattened pipeline.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyEdgeChanges, Background, Controls, Handle, MarkerType, Position, ReactFlow, type Connection, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import { post } from "../api";
import { Choices, Combo, Dialog, Field, helpText, KeyValues, Section, Tags, Text, Toggle, useHelp, type Choice } from "../fields";
import { layout } from "../graph";
import { attempt, classes, freeName, Markdown, notify, relPath, slug } from "../util";
import { ExpectEditor, summary, WaitForEditor } from "./commands";
import { clone, useSession, type Doc } from "./state";

type Scope = { kind: "phase"; index: number } | { kind: "sub"; name: string } | { kind: "settings" } | { kind: "flat" };
const ACTIONS = ["do", "run", "push", "pull", "delay", "wait_for", "uses"];

export function stepAction(step: any, plugins: string[] = []): string {
  const found = ACTIONS.find((a) => step?.[a] !== undefined && step?.[a] !== null);
  if (found) return found;
  return Object.keys(step || {}).find((k) => plugins.includes(k)) || "";
}

function hasCycle(steps: any[], from: string, to: string): boolean {
  // adding "to needs from": a cycle if from (transitively) needs to
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
  const seen = new Set<string>();
  const visit = (id: string): boolean => {
    if (id === to) return true;
    if (seen.has(id)) return false;
    seen.add(id);
    return (byId[id]?.needs || []).some(visit);
  };
  return visit(from);
}

// ── graph nodes ─────────────────────────────────────────────────────────

function EditStepNode({ data, selected }: NodeProps) {
  const d = data as any;
  return (
    <div className={classes("step-node edit", selected && "selected", d.problem && "has-problem", d.part === "finally" && "finally", d.action === "uses" && "uses")} title={d.problem || ""}>
      <Handle type="target" position={Position.Left} />
      <div className="step-id">{d.step.id}</div>
      <div className="step-action">{d.label}</div>
      {d.inner && <div className="step-inner">{d.inner.map((s: string) => <span key={s}>{s}</span>)}</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function LaneNode({ data }: NodeProps) {
  return <div className={classes("frame-node", (data as any).kind)}><div className="frame-title">{(data as any).label}</div></div>;
}

const nodeTypes = { step: EditStepNode, lane: LaneNode };

function label(step: any, plugins: string[]): string {
  const a = stepAction(step, plugins);
  const target = step.component || (Array.isArray(step.for_each) ? step.for_each.join(", ") : step.for_each) || "";
  const what = a === "do" ? step.do : a === "run" ? `run ${String(step.run).slice(0, 30)}` : a === "delay" ? `delay ${step.delay}`
    : a === "wait_for" ? summary({ wait_for: step.wait_for }) : a === "uses" ? `uses ${step.uses}` : a || "no action";
  return `${what}${target ? ` · ${target}` : ""}${step.if ? " · if…" : ""}`;
}

function StepGraph({ lanes, selected, onSelect, onConnect, onDisconnect, onDelete, problems, plugins, subs }: {
  lanes: { part: string; title: string; steps: any[] }[]; selected: string[]; onSelect: (ids: string[]) => void;
  onConnect: (part: string, from: string, to: string) => void; onDisconnect: (part: string, from: string, to: string) => void;
  onDelete: (part: string, ids: string[]) => void; problems: Record<string, string>; plugins: string[]; subs: Record<string, any>;
}) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const shape = JSON.stringify(lanes.map((l) => l.steps.map((s) => [s.id, s.needs, s.uses])));
  const content = JSON.stringify(lanes);
  useEffect(() => {
    let cancelled = false;
    const lay: any[] = [];
    const edgeList: Edge[] = [];
    for (const lane of lanes) {
      const laneId = `lane:${lane.part}`;
      lay.push({ id: laneId, width: 220, height: 70, lane: lane.part });
      for (const s of lane.steps) {
        const inner = s.uses ? subs[s.uses]?.steps : null;
        lay.push({ id: `${lane.part}:${s.id}`, width: 170, height: inner?.length ? 64 + Math.min(inner.length, 8) * 15 : 56, parent: laneId, lane: lane.part });
        for (const n of s.needs || []) edgeList.push({ id: `${lane.part}:${n}->${s.id}`, source: `${lane.part}:${n}`, target: `${lane.part}:${s.id}`, data: { part: lane.part } });
      }
    }
    // each lane laid out on its own, the finally lane under the steps
    Promise.all(lanes.map((lane) => layout(lay.filter((n) => n.lane === lane.part), edgeList.filter((e) => e.data?.part === lane.part).map((e) => ({ id: e.id, source: e.source, target: e.target })))))
      .then((results) => {
        if (cancelled) return;
        const placed: Record<string, any> = {};
        let y = 0;
        results.forEach((r, i) => {
          const laneId = `lane:${lanes[i].part}`;
          // room for the lane title above its steps
          for (const [id, p] of Object.entries(r)) placed[id] = id === laneId ? { ...p, x: 0, y, height: p.height + 24 } : { ...p, y: p.y + 24 };
          y += (r[laneId]?.height || 70) + 54;
        });
        const list: Node[] = [];
        for (const n of lay) {
          const p = placed[n.id];
          if (!p) continue;
          if (n.id.startsWith("lane:")) {
            const lane = lanes.find((l) => `lane:${l.part}` === n.id)!;
            list.push({ id: n.id, type: "lane", position: { x: p.x, y: p.y }, data: { label: lane.title, kind: lane.part === "finally" ? "sub" : "phase" }, style: { width: p.width, height: p.height }, selectable: false, draggable: false });
          } else {
            const [part, ...rest] = n.id.split(":");
            const id = rest.join(":");
            list.push({ id: n.id, type: "step", parentId: n.parent, extent: "parent", position: { x: p.x, y: p.y }, data: { part, id }, style: { width: p.width }, draggable: false });
          }
        }
        setNodes(list);
        setEdges(edgeList.map((e) => ({ ...e, markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } })));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape]);
  const live = useMemo(() => nodes.flatMap((n) => {
    if (n.type !== "step") return [n];
    const { part, id } = n.data as any;
    const step = lanes.find((l) => l.part === part)?.steps.find((s) => s.id === id);
    if (!step) return [];   // laid out for another scope, or just deleted
    return [{ ...n, selected: selected.includes(`${part}:${id}`), data: { ...n.data, step, label: label(step, plugins), action: stepAction(step, plugins), problem: problems[id], inner: step.uses ? subs[step.uses]?.steps : null } }];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [nodes, content, selected, problems, subs]);
  return (
    <>
    <div className="graph-box editable">
      <ReactFlow nodes={live} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ maxZoom: 1.1 }} minZoom={0.2} proOptions={{ hideAttribution: true }}
        onNodeClick={(e, node) => {
          if (node.type !== "step") return;
          const many = e.shiftKey || e.metaKey || e.ctrlKey;
          onSelect(many ? (selected.includes(node.id) ? selected.filter((x) => x !== node.id) : [...selected, node.id]) : [node.id]);
        }}
        onPaneClick={() => selected.length && onSelect([])}
        onEdgesChange={(changes) => setEdges((es) => applyEdgeChanges(changes.filter((c) => c.type === "select"), es))}
        onConnect={(c: Connection) => {
          const [ps, ...s] = (c.source || "").split(":");
          const [pt, ...t] = (c.target || "").split(":");
          if (ps !== pt) return notify("needs link steps of the same part (steps, or finally)", "error");
          onConnect(ps, s.join(":"), t.join(":"));
        }}
        onEdgesDelete={(list) => list.forEach((e) => {
          if (selected.includes(e.source) || selected.includes(e.target)) return;   // a deleted step: onDelete rewires
          const [part, ...s] = e.source.split(":");
          onDisconnect(part, s.join(":"), e.target.split(":").slice(1).join(":"));
        })}
        onNodesDelete={(list) => {
          const byPart: Record<string, string[]> = {};
          for (const n of list) if (n.type === "step") (byPart[(n.data as any).part] ||= []).push((n.data as any).id);
          for (const [part, ids] of Object.entries(byPart)) onDelete(part, ids);
        }}
        deleteKeyCode={["Delete", "Backspace"]} multiSelectionKeyCode={["Shift", "Meta", "Control"]}>
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
      </div>
      <div className="graph-hint">Drag from the right dot of a step to another step: it then <em>needs</em> it. Select an arrow or a step and press Delete to remove it. Shift-click to select several steps.</div>
    </>
  );
}

// ── the editor ──────────────────────────────────────────────────────────

export function PipelineEditor({ doc, api }: { doc: Doc; api: { setData: (d: any) => void } }) {
  const { sid, ctx, schemas, files } = useSession();
  const isTestdef = doc.kind === "testdef";
  const data = doc.data || {};
  const pipeline = isTestdef ? data.pipeline || {} : null;
  const subsOwn: Record<string, any> = (isTestdef ? pipeline.subpipelines : data.subpipelines) || {};
  // the test definition giving the components: this one, or one including this file
  const testdefFor = isTestdef ? doc.path : (ctx.workspace?.testdefs || []).find((t: any) => (t.include || []).some((i: string) => doc.path.endsWith(i.replace(/^\.\.\//g, ""))))?.path || (ctx.workspace?.testdefs || [])[0]?.path;
  const [config, setConfig] = useState<string | null>(null);
  const [palette, setPalette] = useState<any>(null);
  const [paletteError, setPaletteError] = useState<string | null>(null);
  const phases: any[] = pipeline?.phases || [];
  const [scope, setScope] = useState<Scope>(isTestdef ? (phases.length ? { kind: "phase", index: 0 } : { kind: "settings" }) : { kind: "sub", name: Object.keys(subsOwn)[0] || "" });
  const [selected, setSelected] = useState<string[]>([]);
  const [part, setPart] = useState<"steps" | "finally">("steps");
  const [grouping, setGrouping] = useState(false);
  const plugins: string[] = ctx.step_types || [];

  const loadPalette = useCallback(() => {
    if (!testdefFor) return;
    post(`edit/sessions/${sid}/palette`, { testdef: testdefFor, setup_config: config }).then((p) => {
      setPalette(p);
      setPaletteError(null);
    }).catch((e) => setPaletteError(e.message));
  }, [sid, testdefFor, config]);
  useEffect(loadPalette, [loadPalette, doc.data?.setup_file, doc.data?.setup_config]);

  const subs: Record<string, any> = useMemo(() => {
    const all: Record<string, any> = { ...(palette?.shared_subpipelines || {}), ...(palette?.subpipelines || {}) };
    for (const [n, s] of Object.entries<any>(subsOwn)) all[n] = { params: s?.params || {}, steps: (s?.steps || []).map((x: any) => x.id) };
    return all;
  }, [palette, subsOwn]);

  // read and write the step lists of the current scope
  // several edits can come in one event (a deleted step and its arrows): each builds on the previous one
  const latest = useRef(data);
  latest.current = data;
  const write = (fn: (draft: any) => void) => {
    const draft = clone(latest.current);
    if (isTestdef) draft.pipeline ||= {};
    fn(draft);
    latest.current = draft;
    api.setData(draft);
  };
  const listOf = (draft: any, p: string): any[] => {
    const pl = isTestdef ? draft.pipeline : draft;
    if (scope.kind === "phase") {
      const ph = pl.phases[scope.index];
      const key = p === "finally" ? "finally" : "steps";
      ph[key] ||= [];
      return ph[key];
    }
    if (scope.kind === "sub") {
      const holder = isTestdef ? (pl.subpipelines ||= {}) : (pl.subpipelines ||= {});
      holder[scope.name] ||= { steps: [] };
      holder[scope.name].steps ||= [];
      return holder[scope.name].steps;
    }
    return [];
  };
  const current = (p: string): any[] => {
    if (scope.kind === "phase") return (phases[scope.index]?.[p === "finally" ? "finally" : "steps"] || []);
    if (scope.kind === "sub") return subsOwn[scope.name]?.steps || [];
    return [];
  };
  const editSteps = (p: string, fn: (steps: any[]) => void) => write((d) => fn(listOf(d, p)));

  const phase = scope.kind === "phase" ? phases[scope.index] : null;
  const held: string[] = useMemo(() => {
    if (!palette) return [];
    if (!phase) return Object.keys(palette.components);
    return Object.entries<any>(palette.components).filter(([id, c]) => (c.stage && (phase.stages || []).includes(c.stage)) || (phase.components || []).includes(id)).map(([id]) => id);
  }, [palette, phase]);

  const problems: Record<string, string> = {};
  for (const p of doc.problems) {
    const m = p.message.match(/step '([^']+)'/);
    if (m) problems[m[1].split(".").pop()!] = p.message;
  }

  const addStep = (step: any, p: "steps" | "finally" = part) => {
    const steps = current(p);
    const ids = steps.map((s: any) => s.id);
    const id = freeName(slug(step.id), ids);
    const needs = selected.filter((s) => s.startsWith(p + ":")).map((s) => s.slice(p.length + 1)).filter((s) => ids.includes(s));
    editSteps(p, (list) => list.push({ ...step, id, ...(needs.length ? { needs } : {}) }));
    setSelected([`${p}:${id}`]);
  };

  const selectedStep = selected.length === 1 ? (() => {
    const [p, ...rest] = selected[0].split(":");
    const id = rest.join(":");
    const step = current(p).find((s: any) => s.id === id);
    return step ? { part: p, step } : null;
  })() : null;

  const scopes = (
    <div className="scope-bar">
      {isTestdef && <button className={classes("scope", scope.kind === "settings" && "selected")} onClick={() => setScope({ kind: "settings" })}>Pipeline</button>}
      {isTestdef && phases.map((ph, i) => (
        <button key={i} className={classes("scope", scope.kind === "phase" && scope.index === i && "selected")} onClick={() => {
          setScope({ kind: "phase", index: i });
          setSelected([]);
        }}>phase <strong>{ph.id}</strong>{ph.needs?.length ? <span className="muted"> after {ph.needs.join(", ")}</span> : null}</button>
      ))}
      {isTestdef && <button className="scope add" onClick={() => write((d) => {
        d.pipeline.phases ||= [];
        const id = freeName("phase", d.pipeline.phases.map((p: any) => p.id));
        const last = d.pipeline.phases[d.pipeline.phases.length - 1];
        d.pipeline.phases.push({ id, ...(last ? { needs: [last.id] } : {}), steps: [] });
        setScope({ kind: "phase", index: d.pipeline.phases.length - 1 });
      })}>+ phase</button>}
      <span className="scope-sep">sub-pipelines</span>
      {Object.keys(subsOwn).map((n) => (
        <button key={n} className={classes("scope", scope.kind === "sub" && scope.name === n && "selected")} onClick={() => {
          setScope({ kind: "sub", name: n });
          setSelected([]);
        }}>{n}</button>
      ))}
      <button className="scope add" onClick={() => {
        const name = window.prompt("Name of the new sub-pipeline:", freeName("group", Object.keys(subs)));
        if (!name) return;
        write((d) => {
          const holder = isTestdef ? (d.pipeline.subpipelines ||= {}) : (d.subpipelines ||= {});
          holder[slug(name)] = { steps: [] };
        });
        setScope({ kind: "sub", name: slug(name) });
      }}>+ sub-pipeline</button>
      <span className="spacer" />
      {palette?.configurations?.length > 1 && (
        <label className="small">components of <select value={config || palette.configuration} onChange={(e) => setConfig(e.target.value)}>
          {palette.configurations.map((c: string) => <option key={c}>{c}</option>)}
        </select></label>
      )}
      {isTestdef && <button className={classes("scope", scope.kind === "flat" && "selected")} onClick={() => setScope({ kind: "flat" })} title="Sub-pipelines expanded, every command resolved for the configuration, as tsq validate shows it">Flattened view</button>}
    </div>
  );

  if (scope.kind === "settings" && isTestdef) return <div className="pipeline-editor">{scopes}<PipelineSettings doc={doc} pipeline={pipeline} write={write} files={files} /></div>;
  if (scope.kind === "flat") return <div className="pipeline-editor">{scopes}<Flattened testdef={doc.path} config={config} /></div>;

  const lanes = scope.kind === "phase"
    ? [{ part: "steps", title: `steps of ${phase?.id}`, steps: phase?.steps || [] }, { part: "finally", title: "finally: always, even on failure or cancel", steps: phase?.finally || [] }]
    : [{ part: "steps", title: `sub-pipeline ${scope.kind === "sub" ? scope.name : ""}`, steps: current("steps") }];

  return (
    <div className="pipeline-editor">
      {scopes}
      {paletteError && <div className="banner warn">{paletteError}: the palette has the generic steps only.</div>}
      {phase && <PhaseForm phase={phase} index={scope.kind === "phase" ? scope.index : 0} phases={phases} palette={palette} write={write}
        onDeleted={() => setScope(phases.length > 1 ? { kind: "phase", index: 0 } : { kind: "settings" })} />}
      {scope.kind === "sub" && scope.name && <SubForm name={scope.name} sub={subsOwn[scope.name] || {}} isTestdef={isTestdef} write={write} onRenamed={(n) => setScope({ kind: "sub", name: n })} />}
      <div className="pipeline-body">
        <Palette palette={palette} held={held} subs={subs} part={part} setPart={scope.kind === "phase" ? setPart : undefined} onAdd={addStep} selected={selected.length} />
        <div className="pipeline-graph">
          <StepGraph lanes={lanes} selected={selected} onSelect={setSelected} problems={problems} plugins={plugins} subs={subs}
            onConnect={(p, from, to) => {
              if (from === to) return;
              if (hasCycle(current(p), from, to)) return notify(`${from} already waits for ${to}: that would be a cycle`, "error");
              editSteps(p, (list) => {
                const s = list.find((x: any) => x.id === to);
                s.needs = [...new Set([...(s.needs || []), from])];
              });
            }}
            onDisconnect={(p, from, to) => editSteps(p, (list) => {
              const s = list.find((x: any) => x.id === to);
              if (s) s.needs = (s.needs || []).filter((n: string) => n !== from);
              if (s && !s.needs.length) delete s.needs;
            })}
            onDelete={(p, ids) => {
              editSteps(p, (list) => {
                const gone = list.filter((s: any) => ids.includes(s.id));
                for (const s of list) {
                  if (!s.needs) continue;
                  // a step that needed a deleted step now needs what that step needed
                  s.needs = [...new Set(s.needs.flatMap((n: string) => (ids.includes(n) ? gone.find((g: any) => g.id === n)?.needs || [] : [n])))];
                  if (!s.needs.length) delete s.needs;
                }
                list.splice(0, list.length, ...list.filter((s: any) => !ids.includes(s.id)));
              });
              setSelected([]);
            }} />
          {selected.length > 1 && (
            <div className="selection-bar">
              {selected.length} steps selected
              <button className="small" onClick={() => setGrouping(true)}>Group into a sub-pipeline</button>
            </div>
          )}
        </div>
        <div className="step-panel">
          {selectedStep ? (
            <StepForm key={selected[0]} step={selectedStep.step} part={selectedStep.part} siblings={current(selectedStep.part)} palette={palette} held={held} subs={subs}
              isSub={scope.kind === "sub"} schemas={schemas}
              onChange={(next) => editSteps(selectedStep.part, (list) => {
                const i = list.findIndex((s: any) => s.id === selectedStep.step.id);
                if (next.id !== selectedStep.step.id) for (const s of list) if (s.needs) s.needs = s.needs.map((n: string) => (n === selectedStep.step.id ? next.id : n));
                list[i] = next;
                if (next.id !== selectedStep.step.id) setSelected([`${selectedStep.part}:${next.id}`]);
              })}
              onOpenSub={(n) => {
                if (subsOwn[n]) setScope({ kind: "sub", name: n });
                else notify(`${n} is in ${subs[n]?.file}: open that file to edit it`, "info");
              }} />
          ) : (
            <div className="muted pad">
              <Markdown text={"Pick a step to edit it, or add one from the palette: it is linked after the selected steps.\n\nA phase locks its **stages** and ad hoc **components**, then runs its steps as their `needs` allow, in parallel otherwise. The **finally** steps run in the end whatever happened: stop leftovers, collect the outputs."} />
            </div>
          )}
        </div>
      </div>
      {grouping && <GroupDialog subs={subs} onClose={() => setGrouping(false)} onGroup={(name) => {
        const p = selected[0].split(":")[0];
        const ids = selected.filter((s) => s.startsWith(p + ":")).map((s) => s.slice(p.length + 1));
        write((d) => {
          const list = listOf(d, p);
          const chosen = list.filter((s: any) => ids.includes(s.id));
          const outer = [...new Set(chosen.flatMap((s: any) => (s.needs || []).filter((n: string) => !ids.includes(n))))];
          const inner = chosen.map((s: any) => {
            const c = clone(s);
            c.needs = (c.needs || []).filter((n: string) => ids.includes(n));
            if (!c.needs.length) delete c.needs;
            return c;
          });
          const holder = isTestdef ? (d.pipeline.subpipelines ||= {}) : (d.subpipelines ||= {});
          holder[name] = { steps: inner };
          const first = list.findIndex((s: any) => ids.includes(s.id));
          const usesId = freeName(name, list.map((s: any) => s.id).filter((x: string) => !ids.includes(x)));
          const uses: any = { id: usesId, uses: name, ...(outer.length ? { needs: outer } : {}) };
          const rest = list.filter((s: any) => !ids.includes(s.id));
          for (const s of rest) if (s.needs) s.needs = [...new Set(s.needs.map((n: string) => (ids.includes(n) ? usesId : n)))];
          rest.splice(Math.min(first, rest.length), 0, uses);
          list.splice(0, list.length, ...rest);
          setSelected([`${p}:${usesId}`]);
        });
        setGrouping(false);
      }} />}
    </div>
  );
}

function GroupDialog({ subs, onClose, onGroup }: { subs: Record<string, any>; onClose: () => void; onGroup: (name: string) => void }) {
  const [name, setName] = useState(freeName("group", Object.keys(subs)));
  return (
    <Dialog title="Group into a sub-pipeline" onClose={onClose} footer={<button className="primary" disabled={!name || name in subs} onClick={() => onGroup(name)}>Group</button>}>
      <Markdown text="The selected steps become a sub-pipeline, and a `uses` step takes their place: it needs what they needed from outside, and the steps that needed them now need it. Their own `needs` between them stay." />
      <Field label="Name" error={name in subs ? "already a sub-pipeline" : null}><Text value={name} onChange={(v) => setName(slug(v))} /></Field>
    </Dialog>
  );
}

// ── palette ─────────────────────────────────────────────────────────────

function Palette({ palette, held, subs, part, setPart, onAdd, selected }: {
  palette: any; held: string[]; subs: Record<string, any>; part: string; setPart?: (p: "steps" | "finally") => void;
  onAdd: (step: any, part?: "steps" | "finally") => void; selected: number;
}) {
  const setHelp = useHelp();
  const { ctx } = useSession();
  const comps: [string, any][] = palette ? Object.entries<any>(palette.components).filter(([id]) => held.includes(id)) : [];
  const common = (cmd: string) => comps.length > 1 && comps.every(([, c]) => cmd in c.commands);
  const allCommands = [...new Set(comps.flatMap(([, c]) => Object.keys(c.commands)))];
  return (
    <div className="palette">
      {setPart && (
        <div className="segmented small">
          <button className={part === "steps" ? "selected" : ""} onClick={() => setPart("steps")}>add to steps</button>
          <button className={part === "finally" ? "selected" : ""} onClick={() => setPart("finally")}>add to finally</button>
        </div>
      )}
      <div className="muted small">{selected ? `new steps come after the ${selected} selected` : "new steps start with the phase"}</div>
      {comps.length > 1 && (
        <div className="palette-group">
          <div className="palette-title" onMouseEnter={() => setHelp({ title: "for_each: components", text: "One step that runs on every component of the phase, in parallel." })}>every component</div>
          {allCommands.filter(common).map((cmd) => (
            <button key={cmd} className="palette-item" onClick={() => onAdd({ id: cmd, for_each: "components", do: cmd })}>{cmd}</button>
          ))}
        </div>
      )}
      {comps.map(([id, c]) => (
        <details key={id} className="palette-group" open={comps.length <= 2}>
          <summary className="palette-title" title={`${c.type} on a ${c.lab_type}, role ${c.role}${c.stage ? `, stage ${c.stage}` : ", ad hoc"}`}>{id} <span className="muted small">{c.lab_type}</span></summary>
          {Object.entries<any>(c.commands).map(([cmd, info]) => (
            <button key={cmd} className="palette-item" onClick={() => onAdd({ id: `${cmd}_${id}`, component: id, do: cmd })}
              onMouseEnter={() => setHelp({ title: `${cmd} on ${id}`, text: `${info.help ? info.help + "\n\n" : ""}\`${summary(info.spec)}\`\n\nfrom ${info.from}` })}>
              {cmd}
            </button>
          ))}
        </details>
      ))}
      {!palette && <p className="muted small">The components come from the setup configuration of the test definition.</p>}
      <div className="palette-group">
        <div className="palette-title">anywhere</div>
        <button className="palette-item" onClick={() => onAdd({ id: "run", delay: "30s" })} onMouseEnter={() => setHelp({ title: "delay", text: "Wait for a duration: a test with no objective end, such as a scenario that runs by itself." })}>delay</button>
        <button className="palette-item" onClick={() => onAdd({ id: "wait", wait_for: { state: "running", component: held[0] || "", timeout: "30s" } })}
          onMouseEnter={() => setHelp({ title: "wait_for", text: "Wait until a check passes: state, log line, port, URL, file, command." })}>wait for</button>
        <button className="palette-item" onClick={() => onAdd({ id: "check", run: "echo ok" })} onMouseEnter={() => setHelp({ title: "run", text: "An inline shell command, on a component or in the run folder on the sequencer host." })}>shell command</button>
        {Object.keys(subs).map((n) => (
          <button key={n} className="palette-item" onClick={() => onAdd({ id: n, uses: n, ...(Object.keys(subs[n].params || {}).length ? { with: clone(subs[n].params) } : {}) })}
            onMouseEnter={() => setHelp({ title: `uses ${n}`, text: `Runs the sub-pipeline as one step: ${(subs[n].steps || []).join(", ")}` })}>uses {n}</button>
        ))}
        {(ctx.step_types || []).map((t: string) => <button key={t} className="palette-item" onClick={() => onAdd({ id: t, [t]: {} })}>{t}</button>)}
      </div>
    </div>
  );
}

// ── forms ───────────────────────────────────────────────────────────────

function useStepHelp(schemas: any) {
  return (field: string) => helpText(schemas.testdef, ["pipeline", "phases", 0, "steps", 0, field]);
}

function StepForm({ step, part, siblings, palette, held, subs, isSub, schemas, onChange, onOpenSub }: {
  step: any; part: string; siblings: any[]; palette: any; held: string[]; subs: Record<string, any>; isSub: boolean; schemas: any;
  onChange: (s: any) => void; onOpenSub: (n: string) => void;
}) {
  const { ctx } = useSession();
  const help = useStepHelp(schemas);
  const plugins: string[] = ctx.step_types || [];
  const action = stepAction(step, plugins);
  const set = (k: string, v: any) => {
    const next = { ...step, [k]: v };
    if (v === undefined || v === "" || v === false || (Array.isArray(v) && !v.length) || (k === "retries" && !v)) delete next[k];
    onChange(next);
  };
  const comps: Choice[] = (palette ? Object.entries<any>(palette.components) : []).filter(([id]) => isSub || held.includes(id))
    .map(([id, c]) => ({ value: id, description: `${c.name || ""} · ${c.type} on a ${c.lab_type} · role ${c.role}${c.stage ? ` · stage ${c.stage}` : " · ad hoc"}` }));
  const roles = [...new Set(comps.map((c) => palette.components[c.value].role))];
  const targetMode = step.component ? "component" : step.for_each === "components" ? "all" : Array.isArray(step.for_each) ? "list"
    : typeof step.for_each === "string" && roles.includes(step.for_each) ? "role" : typeof step.for_each === "string" ? "expr" : "none";
  const targets: string[] = step.component ? [step.component] : targetMode === "all" ? held : targetMode === "list" ? step.for_each
    : targetMode === "role" ? comps.filter((c) => palette.components[c.value].role === step.for_each).map((c) => c.value) : [];
  const commandChoices: Choice[] = useMemo(() => {
    if (!palette || !targets.length) return [];
    const sets = targets.map((t) => palette.components[t]?.commands || {});
    const names = Object.keys(sets[0]).filter((n) => sets.every((s) => n in s));
    return names.map((n) => ({ value: n, description: `\`${summary(sets[0][n].spec)}\` (${sets[0][n].from})${sets[0][n].help ? " · " + sets[0][n].help : ""}` }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [palette, JSON.stringify(targets)]);
  const setTarget = (mode: string | null) => {
    const next = { ...step };
    delete next.component;
    delete next.for_each;
    if (mode === "component") next.component = held[0] || "";
    if (mode === "all") next.for_each = "components";
    if (mode === "list") next.for_each = held.slice(0, 2);
    if (mode === "role") next.for_each = roles[0] || "";
    onChange(next);
  };
  const setAction = (a: string | null) => {
    if (!a || a === action) return;
    const next = { ...step };
    for (const k of [...ACTIONS, ...plugins, "with"]) delete next[k];
    if (a === "do") next.do = commandChoices[0]?.value || "start";
    if (a === "run") next.run = "";
    if (a === "delay") next.delay = "30s";
    if (a === "wait_for") next.wait_for = { state: "running", timeout: "30s" };
    if (a === "uses") next.uses = Object.keys(subs)[0] || "";
    if (a === "push" || a === "pull") next[a] = { from: "", to: "" };
    if (plugins.includes(a)) next[a] = {};
    onChange(next);
  };
  const others = siblings.filter((s) => s.id !== step.id).map((s) => ({ value: s.id, description: label(s, plugins) }));
  const ifChoices: Choice[] = others.flatMap((o) => [
    { value: `not succeeded('${o.value}')`, description: `when ${o.value} did not succeed: cleanup after a failure` },
    { value: `succeeded('${o.value}')`, description: `only when ${o.value} succeeded` },
  ]);
  return (
    <div className="step-form">
      <Field label="Id" help={help("id")}><Text value={step.id} onChange={(v) => v && set("id", slug(v))} /></Field>
      <Field label="On" help={help("for_each")}>
        <Choices value={targetMode === "expr" ? null : targetMode} onChange={setTarget} allowNone="no component" choices={[
          { value: "component", label: "a component" }, { value: "all", label: "every component", description: "of the phase, in parallel" },
          { value: "list", label: "some components", description: "in parallel" }, { value: "role", label: "a role" },
        ]} />
      </Field>
      {targetMode === "component" && <Field label="Component" help={help("component")}><Combo value={step.component} onChange={(v) => set("component", v)} choices={comps} strict={!isSub} /></Field>}
      {targetMode === "list" && <Field label="Components" help={help("for_each")}><Tags value={step.for_each} onChange={(v) => set("for_each", v)} choices={comps} /></Field>}
      {targetMode === "role" && <Field label="Role"><Combo value={step.for_each} onChange={(v) => set("for_each", v)} choices={roles.map((r) => ({ value: r }))} /></Field>}
      {targetMode === "expr" && <Field label="for_each" help={help("for_each")}><Text mono value={step.for_each} onChange={(v) => set("for_each", v)} /></Field>}
      <Field label="Does" help={help(action || "do")}>
        <Choices value={action} onChange={setAction} choices={[
          { value: "do", label: "a command" }, { value: "run", label: "shell" }, { value: "wait_for", label: "wait for" }, { value: "delay", label: "delay" },
          { value: "uses", label: "sub-pipeline" }, { value: "push", label: "push files" }, { value: "pull", label: "pull files" },
          ...plugins.map((p) => ({ value: p, label: p, description: "plugin step type" })),
        ]} />
      </Field>
      {action === "do" && (
        <Field label="Command" help={help("do")} hint={!targets.length ? "pick the component(s) first: their types give the commands" : null}>
          <Combo value={step.do} onChange={(v) => set("do", v)} choices={commandChoices} strict={commandChoices.length > 0} />
        </Field>
      )}
      {action === "run" && <Field label="Command" help={help("run")} wide><Text mono multiline value={step.run} onChange={(v) => set("run", v)} /></Field>}
      {action === "delay" && <Field label="Duration" help={help("delay")}><Text value={step.delay} onChange={(v) => set("delay", v)} placeholder="30s, or {{ scenario.duration_s }}s" /></Field>}
      {action === "wait_for" && <WaitForEditor value={step.wait_for} onChange={(v) => set("wait_for", v)} withComponent={!step.component} components={comps} />}
      {(action === "push" || action === "pull") && (
        <div className="inline-fields">
          <Field label="From" help={help(action)}><Text mono value={step[action]?.from} onChange={(v) => set(action, { ...step[action], from: v })} /></Field>
          <Field label="To"><Text mono value={step[action]?.to} onChange={(v) => set(action, { ...step[action], to: v })} /></Field>
        </div>
      )}
      {action === "uses" && (
        <>
          <Field label="Sub-pipeline" help={help("uses")}>
            <Combo value={step.uses} onChange={(v) => set("uses", v)} choices={Object.entries(subs).map(([n, s]) => ({ value: n, description: (s.steps || []).join(", ") }))} strict
              onPick={(n) => onChange({ ...step, uses: n, ...(Object.keys(subs[n]?.params || {}).length ? { with: { ...clone(subs[n].params), ...(step.with || {}) } } : {}) })} />
            {step.uses && <button className="link small" onClick={() => onOpenSub(step.uses)}>open {step.uses}</button>}
          </Field>
          {step.uses && subs[step.uses] && <div className="small muted">its steps: {(subs[step.uses].steps || []).join(" → ")}</div>}
          {Object.keys(subs[step.uses]?.params || {}).length > 0 && (
            <Field label="Parameters" help={help("with")}>
              <KeyValues value={step.with} onChange={(v) => set("with", v)} known={Object.entries<any>(subs[step.uses].params).map(([k, v]) => ({ value: k, description: `default: \`${JSON.stringify(v)}\`` }))} />
            </Field>
          )}
        </>
      )}
      {plugins.includes(action) && <Field label="Options" help="Given to the plugin step type."><KeyValues value={step[action]} onChange={(v) => set(action, v)} /></Field>}
      <Field label="Needs" help={help("needs")}><Tags value={step.needs} onChange={(v) => set("needs", v)} choices={others} /></Field>
      <Section title="Options" collapsed={!(step.if || step.timeout || step.retries || step.expect || step.detach !== undefined || step.continue_on_error || step.env)}>
        <Field label="Only if" help={help("if")}><Combo mono value={step.if} onChange={(v) => set("if", v)} choices={ifChoices} /></Field>
        <div className="inline-fields">
          <Field label="Timeout" help={help("timeout")}><Text value={step.timeout} onChange={(v) => set("timeout", v)} placeholder="none" /></Field>
          <Field label="Retries" help={help("retries")}><Text value={step.retries} onChange={(v) => set("retries", v ? Number(v) : undefined)} placeholder="0" /></Field>
        </div>
        {(action === "do" || action === "run") && (
          <>
            <ExpectEditor value={step.expect} onChange={(v) => set("expect", v)} />
            <Field label="Detach" help={help("detach")}>
              <Choices value={step.detach === undefined ? null : String(step.detach)} onChange={(v) => set("detach", v === null ? undefined : v === "true")} allowNone="as the command says"
                choices={[{ value: "true", label: "start and go on" }, { value: "false", label: "wait for the end" }]} />
            </Field>
          </>
        )}
        <Field label="Go on after a failure" help={help("continue_on_error")}><Toggle value={step.continue_on_error} onChange={(v) => set("continue_on_error", v)} label="steps that need it still run" /></Field>
        <Field label="Environment" help={help("env")}><KeyValues value={step.env} onChange={(v) => set("env", Object.keys(v).length ? v : undefined)} /></Field>
      </Section>
      {part === "finally" && <p className="muted small">A finally step runs at the end of the phase in every case, cancel included.</p>}
    </div>
  );
}

function PhaseForm({ phase, index, phases, palette, write, onDeleted }: {
  phase: any; index: number; phases: any[]; palette: any; write: (fn: (d: any) => void) => void; onDeleted: () => void;
}) {
  const { schemas } = useSession();
  const help = (f: string) => helpText(schemas.testdef, ["pipeline", "phases", 0, f]);
  const set = (k: string, v: any) => write((d) => {
    const ph = d.pipeline.phases[index];
    if (k === "id" && v !== ph.id) for (const other of d.pipeline.phases) if (other.needs) other.needs = other.needs.map((n: string) => (n === ph.id ? v : n));
    if (v === undefined || v === "" || (Array.isArray(v) && !v.length)) delete ph[k];
    else ph[k] = v;
  });
  const stageChoices: Choice[] = Object.entries<any>(palette?.stages || {}).map(([s, t]) => ({ value: s, description: `stage type ${t}: ${Object.entries<any>(palette.components).filter(([, c]) => c.stage === s).map(([id]) => id).join(", ")}` }));
  const adhocChoices: Choice[] = Object.entries<any>(palette?.components || {}).filter(([, c]) => !c.stage).map(([id, c]) => ({ value: id, description: `role ${c.role}, ${c.lab_type}` }));
  const earlier = phases.slice(0, index);
  const fromChoices: Choice[] = earlier.flatMap((p: any) => {
    const comps = palette ? Object.entries<any>(palette.components).filter(([id, c]) => (c.stage && (p.stages || []).includes(c.stage)) || (p.components || []).includes(id)).map(([id]) => id) : [];
    return [...comps.map((c) => ({ value: `${p.id}:${c}/output/`, description: `outputs of ${c} in phase ${p.id}` })), { value: `${p.id}:*/output/`, description: `outputs of every component of ${p.id}` }];
  });
  const [open, setOpen] = useState(true);
  return (
    <div className="phase-form">
      <div className="form-title">
        <button className="fold" onClick={() => setOpen(!open)}>{open ? "▾" : "▸"}</button>
        <strong>Phase</strong>
        <input className="inline-name" key={phase.id} defaultValue={phase.id} onBlur={(e) => e.target.value && set("id", slug(e.target.value))} />
        <span className="muted small">locks {[...(phase.stages || []), ...(phase.components || [])].join(", ") || "nothing"}</span>
        <span className="spacer" />
        <button className="small" disabled={index === 0} onClick={() => write((d) => {
          const [ph] = d.pipeline.phases.splice(index, 1);
          d.pipeline.phases.splice(index - 1, 0, ph);
        })}>◀ move</button>
        <button className="small danger" onClick={() => {
          if (!window.confirm(`Delete phase ${phase.id} and its steps?`)) return;
          write((d) => {
            d.pipeline.phases.splice(index, 1);
            for (const p of d.pipeline.phases) if (p.needs) p.needs = p.needs.filter((n: string) => n !== phase.id);
          });
          onDeleted();
        }}>Delete</button>
      </div>
      {open && (
        <div className="phase-fields">
          <Field label="After phases" help={help("needs")}><Tags value={phase.needs} onChange={(v) => set("needs", v)} choices={phases.filter((p) => p.id !== phase.id).map((p) => ({ value: p.id }))} /></Field>
          <Field label="Stages" help={help("stages")}><Tags value={phase.stages} onChange={(v) => set("stages", v)} choices={stageChoices} /></Field>
          <Field label="Ad hoc components" help={help("components")}><Tags value={phase.components} onChange={(v) => set("components", v)} choices={adhocChoices} /></Field>
          <Field label="Timeout" help={help("timeout")}><Text value={phase.timeout} onChange={(v) => set("timeout", v)} placeholder="none" /></Field>
          {index > 0 && (
            <Field label="Inputs from earlier phases" help={help("inputs")} wide>
              {(phase.inputs || []).map((inp: any, i: number) => (
                <div key={i} className="with-button">
                  <Combo mono value={inp.from} onChange={(v) => set("inputs", phase.inputs.map((x: any, j: number) => (j === i ? { ...x, from: v } : x)))} choices={fromChoices} placeholder="phase:component/output/file" />
                  <span>→</span>
                  <Combo mono value={inp.to} onChange={(v) => set("inputs", phase.inputs.map((x: any, j: number) => (j === i ? { ...x, to: v } : x)))}
                    choices={[...(phase.components || []), ...Object.entries<any>(palette?.components || {}).filter(([, c]) => (phase.stages || []).includes(c.stage)).map(([id]) => id)].map((c: string) => ({ value: `${c}/input/data/`, description: `into the inputs of ${c}` }))} placeholder="component/input/..." />
                  <button className="icon" onClick={() => set("inputs", phase.inputs.filter((_: any, j: number) => j !== i))}>×</button>
                </div>
              ))}
              <button className="small" onClick={() => set("inputs", [...(phase.inputs || []), { from: fromChoices[0]?.value || "", to: "" }])}>+ Input</button>
            </Field>
          )}
        </div>
      )}
    </div>
  );
}

function SubForm({ name, sub, isTestdef, write, onRenamed }: { name: string; sub: any; isTestdef: boolean; write: (fn: (d: any) => void) => void; onRenamed: (n: string) => void }) {
  const { schemas } = useSession();
  const holder = (d: any) => (isTestdef ? d.pipeline.subpipelines : d.subpipelines);
  return (
    <div className="phase-form">
      <div className="form-title">
        <strong>Sub-pipeline</strong>
        <input className="inline-name" key={name} defaultValue={name} onBlur={(e) => {
          const to = slug(e.target.value);
          if (!to || to === name) return;
          write((d) => {
            const h = holder(d);
            const entries = Object.entries(h);
            for (const k of Object.keys(h)) delete h[k];
            for (const [k, v] of entries) h[k === name ? to : k] = v;
            const pl = isTestdef ? d.pipeline : d;
            for (const p of pl.phases || []) for (const s of [...(p.steps || []), ...(p.finally || [])]) if (s.uses === name) s.uses = to;
          });
          onRenamed(to);
        }} />
        <span className="spacer" />
        <button className="small danger" onClick={() => {
          if (!window.confirm(`Delete sub-pipeline ${name}? Steps using it will be invalid.`)) return;
          write((d) => delete holder(d)[name]);
        }}>Delete</button>
      </div>
      <Field label="Parameters" help={helpText(schemas.subpipelines, ["subpipelines", "_", "params"])}>
        <KeyValues value={sub.params} onChange={(v) => write((d) => {
          const s = holder(d)[name];
          if (Object.keys(v).length) s.params = v;
          else delete s.params;
        })} />
      </Field>
    </div>
  );
}

function PipelineSettings({ doc, pipeline, write, files }: { doc: Doc; pipeline: any; write: (fn: (d: any) => void) => void; files: any[] }) {
  const { schemas } = useSession();
  const help = (f: string) => helpText(schemas.testdef, ["pipeline", f]);
  const set = (k: string, v: any) => write((d) => {
    if (v === undefined || v === "" || (Array.isArray(v) && !v.length)) delete d.pipeline[k];
    else d.pipeline[k] = v;
  });
  return (
    <div className="pad">
      <h3>Pipeline</h3>
      <Field label="Timeout" help={help("timeout")}><Text value={pipeline.timeout} onChange={(v) => set("timeout", v)} placeholder="none" /></Field>
      <Field label="Shared sub-pipelines" help={help("include")}>
        <Tags value={pipeline.include} onChange={(v) => set("include", v)} choices={files.filter((f) => f.kind === "subpipelines").map((f) => ({ value: relPath(doc.path, f.path), description: f.path }))} />
      </Field>
      <Field label="Lock" help={help("reserve")}>
        <Choices value={pipeline.reserve} onChange={(v) => set("reserve", v ?? undefined)} allowNone="per phase (default)" choices={[{ value: "all", label: "everything at once", description: "every phase's resources at activation" }]} />
      </Field>
      <Field label="When a probed state is unexpected" help={help("on_state_mismatch")}>
        <Choices value={pipeline.on_state_mismatch} onChange={(v) => set("on_state_mismatch", v ?? undefined)} allowNone="warn (default)" choices={[{ value: "fail", label: "fail the step" }]} />
      </Field>
      <Field label="Expected baseline" help={help("baseline")}><Text mono value={pipeline.baseline} onChange={(v) => set("baseline", v)} /></Field>
      <p className="muted">Phases ({(pipeline.phases || []).length}) and sub-pipelines are edited in their own tabs above.</p>
    </div>
  );
}

function Flattened({ testdef, config }: { testdef: string; config: string | null }) {
  const { sid } = useSession();
  const [flat, setFlat] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    post(`edit/sessions/${sid}/flatten`, { testdef, setup_config: config }).then(setFlat).catch((e) => setError(e.message));
  }, [sid, testdef, config]);
  if (error) return <pre className="error-box">{error}</pre>;
  if (!flat) return <p className="muted pad">Expanding…</p>;
  return (
    <div className="pad flattened">
      <p>
        Setup <code>{flat.setup}</code>. Binding against the lab (locks ignored):{" "}
        {flat.binding ? Object.entries<any>({ ...flat.binding.stages, ...flat.binding.components }).map(([l, c]) => <code key={l}>{l}={c} </code>) : <span className="error-text">no binding</span>}
      </p>
      {flat.problems.length > 0 && <ul className="warn-list">{flat.problems.map((p: string) => <li key={p}>{p}</li>)}</ul>}
      {flat.phases.map((ph: any) => (
        <Section key={ph.id} title={`phase ${ph.id}${ph.needs.length ? ` (after ${ph.needs.join(", ")})` : ""}: ${ph.components.join(", ")}`}>
          <table className="grid compact">
            <thead><tr><th>Step</th><th>Needs</th><th>Action</th><th>On</th><th>Command, as it will run</th><th>From</th></tr></thead>
            <tbody>
              {ph.steps.flatMap((s: any) => s.targets.map((t: any, i: number) => (
                <tr key={s.id + i} className={s.part === "finally" ? "finally-row" : ""}>
                  {i === 0 && <td rowSpan={s.targets.length}><code>{s.part === "finally" ? "finally " : ""}{s.id}</code></td>}
                  {i === 0 && <td rowSpan={s.targets.length} className="small">{s.needs.join(", ")}</td>}
                  {i === 0 && <td rowSpan={s.targets.length}>{s.action}{s.do ? ` ${s.do}` : ""}</td>}
                  <td>{t.component ? <>{t.component}{t.concrete && <span className="muted small"> = {t.concrete}</span>}</> : "-"}</td>
                  <td className="mono small">{t.command ? summary(t.command) : s.action !== "do" ? summary(s.step) : ""}</td>
                  <td className="muted small">{t.source || ""}</td>
                </tr>
              )))}
            </tbody>
          </table>
        </Section>
      ))}
      <button className="small" onClick={() => attempt(() => post(`edit/sessions/${sid}/flatten`, { testdef, setup_config: config }).then(setFlat))}>Refresh</button>
    </div>
  );
}
