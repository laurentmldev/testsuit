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

// The Lab page's diagram (sequencer README, section 13.1): each stage an area holding its components, the ad hoc
// components in their own, the links between components deduced from the lab configuration (via, keys naming
// another component, tsq_links), and live: states, holds, the run holding each stage and the command each
// component runs now. A click on a box, an area, a link or a run gives its details or opens its page.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { ReactFlowInstance } from "@xyflow/react";
import {
  Background, BaseEdge, Controls, EdgeLabelRenderer, Handle, Position, ReactFlow, useInternalNode,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import { url } from "../api";
import { Dialog } from "../fields";
import { layout } from "../graph";
import { ComponentIcon } from "../labicons";
import { ago, Badge, classes, navigate, STATE_COLORS, time } from "../util";

export interface LabView {
  links: LabLink[];
  icons: Record<string, { builtin?: string; file?: string; error?: string }>;
  runs: Record<string, RunInfo>;
  components: Record<string, Activity[]>;
}

export interface LabLink { source: string; target: string; kind: "via" | "uses" | "link"; label: string; why: string[]; stages: string[] }
interface RunInfo { id: string; name?: string; testdef?: string; state: string; phase?: string; waiting_for?: string; stages: Record<string, string>; slots: Record<string, string> }
interface Activity { run: string; phase: string; step: string; slot: string; state: string; started?: string; command: string }

const NODE_W = 236;
const KIND_HELP: Record<string, string> = {
  via: "runs through it: its probe or some of its commands run on the other component (via)",
  uses: "names it: its commands, probe or variables use the other component (stage.roles…, lab.components…)",
  link: "wired to it (tsq_links)",
};

export const runName = (run?: RunInfo | null, id?: string) => run?.name || run?.testdef || id || "";

function RunChip({ run, id, children }: { run?: RunInfo; id: string; children?: ReactNode }) {
  return (
    <button type="button" className="run-chip" title={`Run ${id}${run ? `\ndatapack ${runName(run)}, ${run.state}${run.phase ? `, phase ${run.phase}` : ""}` : ""}\nClick: open the run`}
      onClick={(e) => { e.stopPropagation(); navigate(`runs/${id}`); }}>
      {children ?? <>▶ {runName(run, id)}</>}
    </button>
  );
}

// ── nodes ──────────────────────────────────────────────────────────────────

function ComponentNode({ data }: NodeProps) {
  const { comp, icon, activity, runs, shared, held } = data as any;
  // the run holding its stage shows on the stage's title: not again on each of its components
  const stageRuns = new Set<string>(Object.values(runs as Record<string, RunInfo>).filter((r) => (comp.stages || []).some((s: string) => Object.values(r.stages).includes(s))).map((r) => r.id));
  const color = STATE_COLORS[comp.state] || "muted";
  const src = icon?.file ? url(`lab/icons/${encodeURIComponent(comp.id)}`) : null;
  return (
    <div className={classes("lab-node", color, (comp.hold || comp.maintenance) && "off", activity.length > 0 && "busy")}
      title={`${comp.label || comp.id} (${comp.id})${comp.description ? "\n" + comp.description : ""}\nClick: details and actions`}>
      <Handle type="target" position={Position.Top} className="lab-handle" />
      <div className="lab-node-head">
        <span className={classes("lab-icon", color)}><ComponentIcon icon={icon} src={src} size={30} /></span>
        <div className="lab-node-names">
          <strong>{comp.label || comp.id}</strong>
          <span className="muted small">{[comp.label && comp.label !== comp.id ? comp.id : null, comp.role, comp.host !== comp.id ? comp.host : null].filter(Boolean).join(" · ")}</span>
        </div>
      </div>
      <div className="lab-node-tags">
        <Badge value={comp.state} title={comp.detail || ""} />
        {comp.expected && comp.expected !== comp.state && <span className="warning-text" title={`the run holding it expects it ${comp.expected}`}>expected {comp.expected}</span>}
        {comp.hold && <Badge value="hold" title={comp.note || ""} />}
        {comp.maintenance && <Badge value="maintenance" title={comp.note || ""} />}
        {comp.lock !== "exclusive" && <span className="chip" title={comp.lock === "none" ? "never locked" : "several runs can hold it at once"}>lock {comp.lock}</span>}
        {shared && <span className="chip" title={`in the stages ${shared.join(", ")}`}>in {shared.length} stages</span>}
      </div>
      {activity.slice(0, 2).map((a: Activity) => (
        <div key={`${a.run}:${a.phase}:${a.step}`} className="lab-activity" title={`${a.phase}:${a.step} for slot ${a.slot} (${a.state})${a.started ? `, since ${time(a.started)}` : ""}`}>
          <span className={classes("pulse", a.state === "running" ? "run" : "wait")} />
          <code>{a.command || a.step}</code>
          {!stageRuns.has(a.run) && <RunChip run={runs[a.run]} id={a.run}>{runName(runs[a.run], a.run)}</RunChip>}
        </div>
      ))}
      {activity.length > 2 && <div className="muted small">and {activity.length - 2} more steps</div>}
      {held.filter((h: any) => !stageRuns.has(h.run)).length > 0 && activity.length === 0 && (
        <div className="lab-activity">{held.filter((h: any) => !stageRuns.has(h.run)).map((h: any) => <RunChip key={h.run} run={runs[h.run]} id={h.run} />)}</div>
      )}
      <Handle type="source" position={Position.Bottom} className="lab-handle" />
    </div>
  );
}

function AreaNode({ data }: NodeProps) {
  const { stage, runs, adhoc } = data as any;
  if (adhoc) {
    return (
      <div className="lab-area adhoc">
        <div className="lab-area-head"><strong>Ad hoc components</strong><span className="muted small">in no stage</span></div>
      </div>
    );
  }
  const held = (stage.holders || []) as any[];
  return (
    <div className={classes("lab-area", held.length > 0 && "held", (stage.hold || stage.maintenance) && "off")}
      title={`Stage ${stage.id}${stage.description ? "\n" + stage.description : ""}\nClick its title: details and actions`}>
      <div className="lab-area-head" data-stage={stage.id}>
        <strong>Stage {stage.id}</strong>
        <code className="small">{stage.type}</code>
        {stage.hold && <Badge value="hold" title={stage.note || ""} />}
        {stage.maintenance && <Badge value="maintenance" title={stage.note || ""} />}
        <span className="spacer" />
        {held.length === 0 && !stage.hold && !stage.maintenance && <span className="muted small">free</span>}
      </div>
      {held.length > 0 && (
        <div className="lab-area-runs">
          {held.map((h) => {
            const r = runs[h.run];
            return (
              <RunChip key={h.run} run={r} id={h.run}>
                ▶ <strong>{runName(r, h.run)}</strong>{r && <span className="muted"> · {r.state}{r.phase ? ` · ${r.phase}` : ""}</span>}
              </RunChip>
            );
          })}
        </div>
      )}
    </div>
  );
}

const nodeTypes = { component: ComponentNode, area: AreaNode };

// ── edges: drawn from border to border, so that their arrow shows ──────────────

function border(node: any, toward: { x: number; y: number }) {
  const w = node.measured?.width ?? node.width ?? 0;
  const h = node.measured?.height ?? node.height ?? 0;
  const cx = node.internals.positionAbsolute.x + w / 2;
  const cy = node.internals.positionAbsolute.y + h / 2;
  const dx = toward.x - cx;
  const dy = toward.y - cy;
  if (!dx && !dy) return { x: cx, y: cy };
  const scale = 1 / Math.max(Math.abs(dx) / (w / 2 + 4), Math.abs(dy) / (h / 2 + 4));
  return { x: cx + dx * scale, y: cy + dy * scale };
}

function center(node: any) {
  const w = node.measured?.width ?? 0;
  const h = node.measured?.height ?? 0;
  return { x: node.internals.positionAbsolute.x + w / 2, y: node.internals.positionAbsolute.y + h / 2 };
}

function LinkEdge({ id, source, target, data, markerEnd, style }: EdgeProps) {
  const s = useInternalNode(source);
  const t = useInternalNode(target);
  if (!s || !t) return null;
  const a = border(s, center(t));
  const b = border(t, center(s));
  const path = `M ${a.x},${a.y} L ${b.x},${b.y}`;
  const d = data as any;
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={14} />
      {d.text && (
        <EdgeLabelRenderer>
          <div className={classes("lab-edge-label", d.kind)} style={{ transform: `translate(-50%, -50%) translate(${(a.x + b.x) / 2}px, ${(a.y + b.y) / 2}px)`, zIndex: 1000 }}
            title={d.title} onClick={d.open}>{d.text}</div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const edgeTypes = { link: LinkEdge };

/** The links between two components, whichever way, as one edge: its style from the strongest kind. */
function mergeLinks(links: LabLink[]) {
  const pairs = new Map<string, LabLink[]>();
  for (const l of links) {
    const key = [l.source, l.target].sort().join("\u0000");
    pairs.set(key, [...(pairs.get(key) || []), l]);
  }
  return [...pairs.values()];
}

// ── the diagram ─────────────────────────────────────────────────────────────

export function LabGraph({ comps, stages, view, onComponent, onStage, onLinks }: {
  comps: any[]; stages: any[]; view: LabView | null;
  onComponent: (id: string) => void; onStage: (id: string) => void; onLinks: (links: LabLink[]) => void;
}) {
  const [placed, setPlaced] = useState<Record<string, { x: number; y: number; width: number; height: number }> | null>(null);
  const [flow, setFlow] = useState<ReactFlowInstance<any, any> | null>(null);
  useEffect(() => {                // the whole lab in view once it is placed (and again when it changes shape)
    if (!flow || !placed) return;
    const t = window.setTimeout(() => flow.fitView({ padding: 0.06, maxZoom: 1.1 }), 60);
    return () => window.clearTimeout(t);
  }, [flow, placed]);
  const runs = view?.runs || {};
  const activity = view?.components || {};
  const byId = useMemo(() => Object.fromEntries(comps.map((c) => [c.id, c])), [comps]);
  const links = useMemo(() => (view?.links || []).filter((l) => byId[l.source] && byId[l.target]), [view, byId]);
  const parentOf = (c: any) => (c.stages?.length === 1 ? `stage:${c.stages[0]}` : c.stages?.length ? undefined : "adhoc");
  const height = (c: any) => {
    const busy = (activity[c.id] || []).length;
    const ofStages = (run: string) => (c.stages || []).some((s: string) => Object.values(runs[run]?.stages || {}).includes(s));
    const held = !busy && (c.holders || []).some((h: any) => !ofStages(h.run));
    return 76 + 26 * Math.min(busy, 2) + (busy > 2 ? 18 : 0) + (held ? 26 : 0);
  };
  // the layout changes with what is drawn and the heights, not with the states
  const structure = JSON.stringify([stages.map((s) => [s.id, s.components, (s.holders || []).length > 0]),
    comps.map((c) => [c.id, c.stages, height(c)]), links.map((l) => [l.source, l.target])]);
  useEffect(() => {
    let cancelled = false;
    const nodes: any[] = [];
    for (const s of stages) nodes.push({ id: `stage:${s.id}`, width: 300, height: 120, padding: `[top=${(s.holders || []).length ? 74 : 46},left=18,bottom=18,right=18]` });
    if (comps.some((c) => !c.stages?.length)) nodes.push({ id: "adhoc", width: 300, height: 120, padding: "[top=46,left=18,bottom=18,right=18]" });
    for (const c of comps) nodes.push({ id: `c:${c.id}`, width: NODE_W, height: height(c), parent: parentOf(c) });
    const edges = mergeLinks(links).map((group, i) => ({ id: `e${i}`, source: `c:${group[0].source}`, target: `c:${group[0].target}` }));
    layout(nodes, edges, "RIGHT", { "elk.spacing.nodeNode": "34", "elk.layered.spacing.nodeNodeBetweenLayers": "60", "elk.spacing.componentComponent": "40" })
      .then((p) => !cancelled && setPlaced(p))
      .catch((e) => {
        console.error("lab diagram layout:", e);
        if (!cancelled) setPlaced({});
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure]);

  const nodes: Node[] = [];
  if (placed) {
    for (const s of stages) {
      const p = placed[`stage:${s.id}`];
      if (p) nodes.push({ id: `stage:${s.id}`, type: "area", position: { x: p.x, y: p.y }, data: { stage: s, runs }, style: { width: p.width, height: p.height }, draggable: false, selectable: false, zIndex: 0 });
    }
    const a = placed.adhoc;
    if (a) nodes.push({ id: "adhoc", type: "area", position: { x: a.x, y: a.y }, data: { adhoc: true, runs }, style: { width: a.width, height: a.height }, draggable: false, selectable: false, zIndex: 0 });
    for (const c of comps) {
      const p = placed[`c:${c.id}`];
      if (!p) continue;
      const parent = parentOf(c);
      nodes.push({
        id: `c:${c.id}`, type: "component", position: { x: p.x, y: p.y }, ...(parent && placed[parent] ? { parentId: parent, extent: "parent" as const } : {}),
        style: { width: p.width, height: p.height }, draggable: false, zIndex: 2,
        data: { comp: c, icon: view?.icons?.[c.id], activity: activity[c.id] || [], runs, shared: (c.stages?.length || 0) > 1 ? c.stages : null, held: c.holders || [] },
      });
    }
  }
  const edges: Edge[] = mergeLinks(links).map((group, i) => {
    const kinds = new Set(group.map((l) => l.kind));
    const directed = group.find((l) => l.kind === "via") || group.find((l) => l.kind === "uses");
    const physical = kinds.has("link");
    const text = [...new Set(group.map((l) => l.kind === "link" ? l.label : l.kind))].filter(Boolean).join(" · ");
    const title = group.map((l) => `${byId[l.source]?.label || l.source} → ${byId[l.target]?.label || l.target}: ${l.why.join("; ")}`).join("\n") + "\nClick: why this link";
    const main = physical ? "link" : directed!.kind;
    const color = main === "via" ? "var(--accent)" : main === "uses" ? "var(--muted)" : "var(--text)";
    return {
      id: `e${i}`, type: "link", source: `c:${(directed || group[0]).source}`, target: `c:${(directed || group[0]).target}`, zIndex: 1,
      data: { text, title, kind: main, links: group, open: () => onLinks(group) },
      markerEnd: directed ? { type: "arrowclosed" as any, width: 14, height: 14, color } : undefined,
      style: { stroke: color, strokeWidth: physical ? 2.2 : 1.5, strokeDasharray: physical ? undefined : main === "via" ? "6 4" : "2 4", opacity: physical ? 0.75 : 1 },
    };
  });

  return (
    <>
      <div className="graph-box lab-graph">
        {!placed && <p className="muted" style={{ padding: 16 }}>Drawing the lab…</p>}
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onInit={setFlow}
          minZoom={0.15} maxZoom={2} nodesConnectable={false} proOptions={{ hideAttribution: true }}
          onNodeClick={(e, n) => {
            if (n.type === "component") onComponent((n.data as any).comp.id);
            else if ((n.data as any).stage && (e.target as HTMLElement).closest(".lab-area-head")) onStage((n.data as any).stage.id);
          }}
          onEdgeClick={(_, e) => onLinks((e.data as any).links)}>
          <Background gap={16} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <div className="lab-legend muted small">
        <span><svg width="34" height="8"><line x1="0" y1="4" x2="34" y2="4" stroke="var(--text)" strokeWidth="2.2" opacity=".75" /></svg> wired (tsq_links)</span>
        <span><svg width="34" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="var(--accent)" strokeWidth="1.5" strokeDasharray="6 4" /><path d="M27 0l7 4-7 4z" fill="var(--accent)" /></svg> runs through (via)</span>
        <span><svg width="34" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="var(--muted)" strokeWidth="1.5" strokeDasharray="2 4" /><path d="M27 0l7 4-7 4z" fill="var(--muted)" /></svg> uses (its commands name it)</span>
        <span><span className="pulse run" /> runs a command now</span>
        <span>Click a box, a stage title, a link or a run for details.</span>
      </div>
    </>
  );
}

// ── dialogs ─────────────────────────────────────────────────────────────────

/** Why two components are linked: what in the lab configuration says so. */
export function LinksDialog({ links, labelOf, onClose, onComponent }: { links: LabLink[]; labelOf: (id: string) => string; onClose: () => void; onComponent: (id: string) => void }) {
  const ends = [...new Set(links.flatMap((l) => [l.source, l.target]))];
  return (
    <Dialog title={`Link ${ends.map(labelOf).join(" – ")}`} onClose={onClose}>
      <LinkList links={links} labelOf={labelOf} onComponent={onComponent} />
    </Dialog>
  );
}

function LinkList({ links, labelOf, onComponent, me }: { links: LabLink[]; labelOf: (id: string) => string; onComponent: (id: string) => void; me?: string }) {
  const name = (id: string) => id === me ? <strong>{labelOf(id)}</strong>
    : <button type="button" className="link" onClick={() => onComponent(id)}>{labelOf(id)}</button>;
  return (
    <table className="grid compact">
      <tbody>
        {links.map((l) => (
          <tr key={`${l.source}:${l.target}:${l.kind}`}>
            <td style={{ whiteSpace: "nowrap" }}>{name(l.source)} → {name(l.target)}</td>
            <td><span className={classes("chip", l.kind === "via" ? "run" : l.kind === "link" ? "ok" : "")} title={KIND_HELP[l.kind]}>{l.kind}{l.label ? `: ${l.label}` : ""}</span></td>
            <td className="small">{l.why.join("; ")}{l.stages.length > 0 && <span className="muted"> (in {l.stages.join(", ")})</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A component of the diagram: what it is, its state, what runs on it, its links, and its actions. */
export function ComponentDialog({ comp, view, labelOf, actions, onClose, onProbe, onComponent, onTable, onEdit }: {
  comp: any; view: LabView | null; labelOf: (id: string) => string; actions: ReactNode; onClose: () => void;
  onProbe: () => void; onComponent: (id: string) => void; onTable: () => void; onEdit?: () => void;
}) {
  const icon = view?.icons?.[comp.id];
  const src = icon?.file ? url(`lab/icons/${encodeURIComponent(comp.id)}`) : null;
  const runs = view?.runs || {};
  const activity = view?.components?.[comp.id] || [];
  const links = (view?.links || []).filter((l) => l.source === comp.id || l.target === comp.id);
  return (
    <Dialog title={comp.label || comp.id} onClose={onClose} wide
      footer={<>
        <button onClick={onTable} title="The Table tab, on this component">Show in the table</button>
        {onEdit && <button onClick={onEdit} title="Open the lab inventory in the Editor, on this component">Edit in the Editor</button>}
      </>}>
      <div className="lab-dialog-head">
        <span className={classes("lab-icon big", STATE_COLORS[comp.state] || "muted")}><ComponentIcon icon={icon} src={src} size={44} /></span>
        <div>
          <div><code>{comp.id}</code> · role <code>{comp.role}</code> · extends <code>{comp.extends}</code> · host <code>{comp.host}</code> · lock {comp.lock}</div>
          <div className="muted small">{comp.stages?.length ? <>in stage{comp.stages.length > 1 ? "s" : ""} {comp.stages.join(", ")}</> : "ad hoc component (in no stage)"}</div>
          {comp.description && <p className="description">{comp.description}</p>}
          {icon?.error && <div className="warning-text">{icon.error}</div>}
        </div>
      </div>
      <h4>State</h4>
      <p>
        <button className="link state-button" onClick={onProbe} title="What was tested, the answer, and why this state"><Badge value={comp.state} /></button>
        {comp.expected && comp.expected !== comp.state && <span className="warning-text"> expected {comp.expected}</span>}
        {comp.hold && <> <Badge value="hold" /></>}{comp.maintenance && <> <Badge value="maintenance" /></>}
        {comp.note && <span className="muted"> “{comp.note}”</span>}
        <span className="muted small" title={time(comp.probed_at)}> {comp.probed_at ? `probed ${ago(comp.probed_at)}` : "not probed yet"}</span>
        {comp.detail && <span className="muted small"> · {comp.detail}</span>}
      </p>
      <h4>Runs</h4>
      {!(comp.holders?.length) && !activity.length ? <p className="muted">Free: no run holds it.</p> : (
        <table className="grid compact">
          <tbody>
            {(comp.holders || []).map((h: any) => (
              <tr key={h.run}>
                <td><RunChip run={runs[h.run]} id={h.run} /></td>
                <td className="small">{h.run} · lock {h.mode}{runs[h.run] && <> · {runs[h.run].state}{runs[h.run].phase ? `, phase ${runs[h.run].phase}` : ""}</>}</td>
                <td className="small">{activity.filter((a) => a.run === h.run).map((a) => <div key={a.step}><span className={classes("pulse", a.state === "running" ? "run" : "wait")} /> <code>{a.command || a.step}</code> <span className="muted">step {a.phase}:{a.step}, slot {a.slot}{a.started ? `, since ${ago(a.started)}` : ""}</span></div>)}</td>
              </tr>
            ))}
            {activity.filter((a) => !(comp.holders || []).some((h: any) => h.run === a.run)).map((a) => (
              <tr key={`${a.run}:${a.step}`}><td><RunChip run={runs[a.run]} id={a.run} /></td><td className="small">{a.run}</td>
                <td className="small"><span className={classes("pulse", a.state === "running" ? "run" : "wait")} /> <code>{a.command || a.step}</code> <span className="muted">step {a.phase}:{a.step}, slot {a.slot}</span></td></tr>
            ))}
          </tbody>
        </table>
      )}
      <h4>Links</h4>
      {links.length ? <LinkList links={links} labelOf={labelOf} onComponent={onComponent} me={comp.id} /> : <p className="muted">None found in the lab configuration (tsq_links, via, keys naming another component).</p>}
      <h4>Actions</h4>
      {actions}
    </Dialog>
  );
}

/** A stage of the diagram: what it is, the run holding it, and its actions. */
export function StageDialog({ stage, view, labelOf, actions, onClose, onComponent }: {
  stage: any; view: LabView | null; labelOf: (id: string) => string; actions: ReactNode; onClose: () => void; onComponent: (id: string) => void;
}) {
  const runs = view?.runs || {};
  return (
    <Dialog title={`Stage ${stage.id}`} onClose={onClose} wide>
      <p>type <code>{stage.type}</code>{stage.hold && <> <Badge value="hold" /></>}{stage.maintenance && <> <Badge value="maintenance" /></>}{stage.note && <span className="muted"> “{stage.note}”</span>}</p>
      {stage.description && <p className="description">{stage.description}</p>}
      <h4>Components</h4>
      <p>{stage.components.map((c: string, i: number) => <span key={c}>{i > 0 && ", "}<button type="button" className="link" onClick={() => onComponent(c)}>{labelOf(c)}</button></span>)}</p>
      <h4>Runs</h4>
      {!(stage.holders?.length) ? <p className="muted">Free: no run holds it.</p> : (
        <table className="grid compact">
          <tbody>
            {stage.holders.map((h: any) => {
              const r = runs[h.run];
              return (
                <tr key={h.run}>
                  <td><RunChip run={r} id={h.run} /></td>
                  <td className="small">{h.run}{r && <> · {r.state}{r.phase ? `, phase ${r.phase}` : ""}{r.testdef ? <>, test definition <code>{r.testdef}</code></> : null}</>}</td>
                  <td className="small">{r && Object.entries(r.slots).filter(([, c]) => stage.components.includes(c)).map(([slot, c]) => <span key={slot} className="chip" title={`slot ${slot} bound to ${c}`}>{slot} → {labelOf(c)}</span>)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <h4>Actions</h4>
      {actions}
    </Dialog>
  );
}
