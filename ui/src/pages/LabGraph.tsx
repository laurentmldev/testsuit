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
  Background, BaseEdge, Controls, EdgeLabelRenderer, Handle, Position, ReactFlow,
  type Edge, type EdgeProps, type Node, type NodeChange, type NodeProps,
} from "@xyflow/react";
import { put, url, useResource } from "../api";
import { Dialog } from "../fields";
import { layoutWithRoutes, type Point } from "../graph";
import { ComponentIcon } from "../labicons";
import { ago, attempt, Badge, classes, navigate, STATE_COLORS, time } from "../util";

export interface LabView {
  links: LabLink[];
  icons: Record<string, { builtin?: string; file?: string; error?: string }>;
  runs: Record<string, RunInfo>;
  components: Record<string, Activity[]>;
}

export interface LabLink { source: string; target: string; kind: "via" | "uses" | "link"; label: string; why: string[]; stages: string[] }
interface RunInfo { id: string; name?: string; testdef?: string; state: string; phase?: string; waiting_for?: string; stages: Record<string, string>; slots: Record<string, string> }
interface Activity { run: string; phase: string; step: string; slot: string; state: string; started?: string; command: string }

// card sizes: compact (picture, name, status), and expanded (everything)
const COMPACT = { width: 164, height: 132 };
const EXPANDED_W = 250;
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

/** Height of an expanded card: its head, then a line per step it runs (two at most) or per run holding it. */
function expandedHeight(activity: number, held: boolean) {
  return 150 + 26 * Math.min(activity, 2) + (activity > 2 ? 18 : 0) + (held ? 26 : 0);
}

function ComponentNode({ data }: NodeProps) {
  const { comp, icon, activity, runs, shared, held, expanded, onToggle, onDetails } = data as any;
  // the run holding its stage shows on the stage's title: not again on each of its components
  const stageRuns = new Set<string>(Object.values(runs as Record<string, RunInfo>).filter((r) => (comp.stages || []).some((s: string) => Object.values(r.stages).includes(s))).map((r) => r.id));
  const color = STATE_COLORS[comp.state] || "muted";
  const src = icon?.file ? url(`lab/icons/${encodeURIComponent(comp.id)}`) : null;
  const running = (activity as Activity[]).find((a) => a.state === "running") || activity[0];
  const status = (
    <div className="lab-node-tags">
      <Badge value={comp.state} title={comp.detail || ""} />
      {running && <span className={classes("pulse", running.state === "running" ? "run" : "wait")} title={`runs ${running.command || running.step} (${running.phase}:${running.step})`} />}
      {comp.hold && <Badge value="hold" title={comp.note || ""} />}
      {comp.maintenance && <Badge value="maintenance" title={comp.note || ""} />}
    </div>
  );
  const handles = <><Handle type="target" position={Position.Top} className="lab-handle" /><Handle type="source" position={Position.Bottom} className="lab-handle" /></>;
  if (!expanded) {
    return (
      <div className={classes("lab-node compact", color, (comp.hold || comp.maintenance) && "off", activity.length > 0 && "busy")}
        title={`${comp.label || comp.id} (${comp.id})${comp.description ? "\n" + comp.description : ""}${running ? `\nruns ${running.command || running.step}` : ""}\nClick: expand`}>
        {handles}
        <span className={classes("lab-icon xl", color)}><ComponentIcon icon={icon} src={src} size={52} /></span>
        <strong className="lab-name">{comp.label || comp.id}</strong>
        {status}
      </div>
    );
  }
  const otherRuns = (held as any[]).filter((h) => !stageRuns.has(h.run));
  return (
    <div className={classes("lab-node expanded", color, (comp.hold || comp.maintenance) && "off", activity.length > 0 && "busy")}
      title={`${comp.label || comp.id} (${comp.id})${comp.description ? "\n" + comp.description : ""}\nClick: collapse`}>
      {handles}
      <div className="lab-node-head">
        <span className={classes("lab-icon xl", color)}><ComponentIcon icon={icon} src={src} size={52} /></span>
        <div className="lab-node-names">
          <strong>{comp.label || comp.id}</strong>
          <span className="muted small">{[comp.label && comp.label !== comp.id ? comp.id : null, comp.role].filter(Boolean).join(" · ")}</span>
          <span className="muted small">{comp.host}</span>
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
      {otherRuns.length > 0 && activity.length === 0 && (
        <div className="lab-activity">{otherRuns.map((h: any) => <RunChip key={h.run} run={runs[h.run]} id={h.run} />)}</div>
      )}
      <div className="lab-node-foot">
        <button type="button" className="small" onClick={(e) => { e.stopPropagation(); onDetails(); }} title="State, probe report, runs, links and actions">Details…</button>
        <button type="button" className="small link" onClick={(e) => { e.stopPropagation(); onToggle(); }}>collapse</button>
      </div>
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

// ── edges: routed by ELK with horizontal and vertical segments; a horizontal one hops over the vertical ones it
// crosses (a bridge) ──────────────────────────────────────────────────────────

const HOP = 5;

type Segment = { a: Point; b: Point; edge: string };

/** The SVG path of a route, with a bridge where a horizontal segment crosses a vertical one of another edge. */
function bridged(id: string, route: Point[], verticals: Segment[]): string {
  let d = `M ${route[0].x},${route[0].y}`;
  for (let i = 1; i < route.length; i++) {
    const a = route[i - 1];
    const b = route[i];
    if (Math.abs(a.y - b.y) < 0.5 && Math.abs(a.x - b.x) > 2 * HOP + 2) {
      const dir = Math.sign(b.x - a.x);
      const lo = Math.min(a.x, b.x) + HOP + 1;
      const hi = Math.max(a.x, b.x) - HOP - 1;
      const xs = verticals
        .filter((v) => v.edge !== id && v.a.x > lo && v.a.x < hi && Math.min(v.a.y, v.b.y) < a.y - 1 && Math.max(v.a.y, v.b.y) > a.y + 1)
        .map((v) => v.a.x)
        .sort((p, q) => (p - q) * dir);
      let last = -Infinity;
      for (const x of xs) {
        if (Math.abs(x - last) < 2 * HOP + 2) continue;       // two crossings too close: one bridge
        last = x;
        d += ` L ${x - dir * HOP},${a.y} A ${HOP} ${HOP} 0 0 ${dir > 0 ? 1 : 0} ${x + dir * HOP},${a.y}`;
      }
    }
    d += ` L ${b.x},${b.y}`;
  }
  return d;
}

/** Where a route's label goes: the middle of its longest segment. */
function labelPoint(route: Point[]): Point {
  let best = { x: route[0].x, y: route[0].y };
  let length = -1;
  for (let i = 1; i < route.length; i++) {
    const l = Math.abs(route[i].x - route[i - 1].x) + Math.abs(route[i].y - route[i - 1].y);
    if (l > length) {
      length = l;
      best = { x: (route[i].x + route[i - 1].x) / 2, y: (route[i].y + route[i - 1].y) / 2 };
    }
  }
  return best;
}

function LinkEdge({ id, data, markerEnd, style }: EdgeProps) {
  const d = data as any;
  if (!d.path) return null;
  return (
    <>
      <BaseEdge id={id} path={d.path} markerEnd={markerEnd} style={style} interactionWidth={14} />
      {d.text && (
        <EdgeLabelRenderer>
          <div className={classes("lab-edge-label", d.kind)} style={{ transform: `translate(-50%, -50%) translate(${d.at.x}px, ${d.at.y}px)`, zIndex: 1000, opacity: d.opacity ?? 1 }}
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

type Rect = { x: number; y: number; width: number; height: number };
type Arrangement = Record<string, { x?: number; y?: number; opacity?: number }>;

/** A route with horizontal and vertical segments between two cards a user placed: out of the bottom of one into
 * the top of the other when one is below the other, else from side to side. */
function squareRoute(s: Rect, t: Rect): Point[] {
  const sx = s.x + s.width / 2;
  const tx = t.x + t.width / 2;
  const sy = s.y + s.height / 2;
  const ty = t.y + t.height / 2;
  let points: Point[];
  if (t.y >= s.y + s.height + 8 || s.y >= t.y + t.height + 8) {
    const down = t.y > s.y;
    const a = { x: sx, y: down ? s.y + s.height : s.y };
    const b = { x: tx, y: down ? t.y : t.y + t.height };
    const mid = (a.y + b.y) / 2;
    points = [a, { x: sx, y: mid }, { x: tx, y: mid }, b];
  } else {
    const right = t.x > s.x;
    const a = { x: right ? s.x + s.width : s.x, y: sy };
    const b = { x: right ? t.x : t.x + t.width, y: ty };
    const mid = (a.x + b.x) / 2;
    points = [a, { x: mid, y: sy }, { x: mid, y: ty }, b];
  }
  return points.filter((p, i) => i === 0 || Math.abs(p.x - points[i - 1].x) + Math.abs(p.y - points[i - 1].y) > 0.5);
}

const AREA_PAD = { left: 18, right: 18, bottom: 18 };
const AREA_MIN_W = 200;

export function LabGraph({ comps, stages, view, onComponent, onStage, onLinks }: {
  comps: any[]; stages: any[]; view: LabView | null;
  onComponent: (id: string) => void; onStage: (id: string) => void; onLinks: (links: LabLink[]) => void;
}) {
  const [placed, setPlaced] = useState<Record<string, Rect> | null>(null);
  const [routes, setRoutes] = useState<Record<string, Point[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const saved = useResource<{ components: Arrangement; saved_at?: string }>("lab/layout", ["lab_layout"]);
  const [draft, setDraft] = useState<Arrangement | null>(null);      // while arranging
  const [picked, setPicked] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const toggle = (id: string) => setExpanded((all) => {
    const next = new Set(all);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const [flow, setFlow] = useState<ReactFlowInstance<any, any> | null>(null);
  const [fitted, setFitted] = useState(false);
  useEffect(() => {                // the whole lab in view once placed; not again when a card is expanded
    if (!flow || !placed || fitted || !saved.data) return;
    const t = window.setTimeout(() => {
      flow.fitView({ padding: 0.06, maxZoom: 1.1 });
      setFitted(true);
    }, 60);
    return () => window.clearTimeout(t);
  }, [flow, placed, fitted, saved.data]);
  const runs = view?.runs || {};
  const activity = view?.components || {};
  const byId = useMemo(() => Object.fromEntries(comps.map((c) => [c.id, c])), [comps]);
  const links = useMemo(() => (view?.links || []).filter((l) => byId[l.source] && byId[l.target]), [view, byId]);
  const groups = useMemo(() => mergeLinks(links), [links]);
  const parentOf = (c: any) => (c.stages?.length === 1 ? `stage:${c.stages[0]}` : c.stages?.length ? undefined : "adhoc");
  const headOf = (s: any) => ((s.holders || []).length ? 84 : 58);
  const size = (c: any) => {
    if (!expanded.has(c.id)) return COMPACT;
    const busy = (activity[c.id] || []).length;
    const ofStages = (run: string) => (c.stages || []).some((s: string) => Object.values(runs[run]?.stages || {}).includes(s));
    const held = !busy && (c.holders || []).some((h: any) => !ofStages(h.run));
    return { width: EXPANDED_W, height: expandedHeight(busy, held) };
  };
  // the automatic layout changes with what is drawn and the sizes, not with the states
  const structure = JSON.stringify([stages.map((s) => [s.id, s.components, headOf(s)]),
    comps.map((c) => [c.id, c.stages, size(c)]), links.map((l) => [l.source, l.target])]);
  useEffect(() => {
    let cancelled = false;
    const nodes: any[] = [];
    for (const s of stages) nodes.push({ id: `stage:${s.id}`, width: AREA_MIN_W, height: 120, padding: `[top=${headOf(s)},left=18,bottom=18,right=18]`, minWidth: AREA_MIN_W });
    if (comps.some((c) => !c.stages?.length)) nodes.push({ id: "adhoc", width: AREA_MIN_W, height: 120, padding: "[top=58,left=18,bottom=18,right=18]", minWidth: AREA_MIN_W });
    for (const c of comps) nodes.push({ id: `c:${c.id}`, ...size(c), parent: parentOf(c) });
    const edges = groups.map((group, i) => {
      const directed = group.find((l) => l.kind === "via") || group.find((l) => l.kind === "uses") || group[0];
      return { id: `e${i}`, source: `c:${directed.source}`, target: `c:${directed.target}` };
    });
    layoutWithRoutes(nodes, edges, "DOWN", {
      "elk.edgeRouting": "ORTHOGONAL", "elk.spacing.nodeNode": "30", "elk.layered.spacing.nodeNodeBetweenLayers": "56",
      "elk.spacing.edgeNode": "18", "elk.spacing.edgeEdge": "12", "elk.layered.spacing.edgeNodeBetweenLayers": "18",
      "elk.layered.spacing.edgeEdgeBetweenLayers": "12", "elk.spacing.componentComponent": "40",
    })
      .then((r) => {
        if (cancelled) return;
        setPlaced(r.nodes);
        setRoutes(r.routes);
      })
      .catch((e) => {
        console.error("lab diagram layout:", e);
        if (!cancelled) setPlaced({});
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure]);

  // where each card is: where a user placed it (saved for everyone, or being arranged), else where ELK put it
  const arrangement: Arrangement = draft ?? saved.data?.components ?? {};
  const manual = Object.values(arrangement).some((a) => a.x !== undefined);
  const autoRect = (c: any): Rect | null => {
    const p = placed?.[`c:${c.id}`];
    if (!p) return null;
    const parent = parentOf(c);
    const q = parent ? placed?.[parent] : undefined;
    return { ...p, x: p.x + (q?.x || 0), y: p.y + (q?.y || 0) };
  };
  const rects: Record<string, Rect> = {};
  for (const c of comps) {
    const auto = autoRect(c);
    if (!auto) continue;
    const a = arrangement[c.id];
    rects[c.id] = a?.x !== undefined && a?.y !== undefined ? { ...size(c), x: a.x, y: a.y } : auto;
  }
  const opacity = (id: string) => arrangement[id]?.opacity ?? 1;
  const areaRect = (id: string, head: number): Rect | null => {
    if (!manual) return placed?.[id] ?? null;
    const members = comps.filter((c) => parentOf(c) === id && rects[c.id]).map((c) => rects[c.id]);
    if (!members.length) return placed?.[id] ?? null;
    const x0 = Math.min(...members.map((r) => r.x)) - AREA_PAD.left;
    const y0 = Math.min(...members.map((r) => r.y)) - head;
    const x1 = Math.max(...members.map((r) => r.x + r.width)) + AREA_PAD.right;
    const y1 = Math.max(...members.map((r) => r.y + r.height)) + AREA_PAD.bottom;
    return { x: x0, y: y0, width: Math.max(x1 - x0, AREA_MIN_W), height: y1 - y0 };
  };

  const startArranging = () => {
    setDraft(Object.fromEntries(comps.filter((c) => rects[c.id]).map((c) => [c.id, { x: rects[c.id].x, y: rects[c.id].y, opacity: opacity(c.id) }])));
    setPicked(null);
  };
  const saveArrangement = async () => {
    setSaving(true);
    const done = await attempt(() => put("lab/layout", { components: draft }), "lab arrangement saved for everyone");
    setSaving(false);
    if (!done) return;
    saved.setData(done);
    setDraft(null);
    setPicked(null);
  };
  const resetArrangement = async () => {
    if (!window.confirm("Back to the automatic layout, for everyone? Positions and opacities set by hand are forgotten.")) return;
    const done = await attempt(() => put("lab/layout", { components: {} }), "lab back to the automatic layout");
    if (done) saved.setData(done);
    setDraft(null);
    setPicked(null);
  };
  // the nodes are rebuilt at each render: the sizes React Flow measures are kept here, or it never shows them
  const [measured, setMeasured] = useState<Record<string, { width: number; height: number }>>({});
  const onNodesChange = (changes: NodeChange[]) => {
    const sizes = changes.filter((ch) => ch.type === "dimensions" && ch.dimensions) as any[];
    if (sizes.length) setMeasured((m) => ({ ...m, ...Object.fromEntries(sizes.map((ch) => [ch.id, ch.dimensions])) }));
    if (!draft) return;
    setDraft((current) => {
      if (!current) return current;
      const next = { ...current };
      for (const ch of changes) {
        if (ch.type !== "position" || !ch.position) continue;
        if (ch.id.startsWith("c:")) {
          const id = ch.id.slice(2);
          next[id] = { ...next[id], x: ch.position.x, y: ch.position.y };
        } else {                                  // a stage area: its components move with it
          const area = nodesById[ch.id];
          if (!area) continue;
          const dx = ch.position.x - area.position.x;
          const dy = ch.position.y - area.position.y;
          for (const c of comps.filter((c) => parentOf(c) === ch.id && rects[c.id])) {
            next[c.id] = { ...next[c.id], x: rects[c.id].x + dx, y: rects[c.id].y + dy };
          }
        }
      }
      return next;
    });
  };

  const nodes: Node[] = [];
  if (placed) {
    const area = (id: string, head: number, data: any) => {
      const r = areaRect(id, head);
      if (r) nodes.push({ id, type: "area", position: { x: r.x, y: r.y }, data, style: { width: r.width, height: r.height }, draggable: !!draft, selectable: false, zIndex: 0 });
    };
    for (const s of stages) area(`stage:${s.id}`, headOf(s), { stage: s, runs });
    if (comps.some((c) => !c.stages?.length)) area("adhoc", 58, { adhoc: true, runs });
    for (const c of comps) {
      const r = rects[c.id];
      if (!r) continue;
      nodes.push({
        id: `c:${c.id}`, type: "component", position: { x: r.x, y: r.y }, style: { width: r.width, height: r.height, opacity: opacity(c.id) },
        draggable: !!draft, selectable: false, zIndex: expanded.has(c.id) ? 3 : 2, className: picked === c.id ? "lab-picked" : undefined,
        data: { comp: c, icon: view?.icons?.[c.id], activity: activity[c.id] || [], runs, shared: (c.stages?.length || 0) > 1 ? c.stages : null, held: c.holders || [],
          expanded: expanded.has(c.id), onToggle: () => toggle(c.id), onDetails: () => onComponent(c.id) },
      });
    }
  }
  for (const n of nodes) if (measured[n.id]) n.measured = measured[n.id];
  const nodesById = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const routeOf = (i: number, group: LabLink[]): Point[] | null => {
    const directed = group.find((l) => l.kind === "via") || group.find((l) => l.kind === "uses") || group[0];
    if (!manual) return routes[`e${i}`] ?? null;
    const s = rects[directed.source];
    const t = rects[directed.target];
    return s && t ? squareRoute(s, t) : null;
  };
  const paths = groups.map((group, i) => routeOf(i, group));
  const verticals: Segment[] = paths.flatMap((r, i) => (r ? r.slice(1).map((b, k) => ({ a: r[k], b, edge: `e${i}` })) : []))
    .filter((sg) => Math.abs(sg.a.x - sg.b.x) < 0.5);
  const edges: Edge[] = groups.map((group, i) => {
    const kinds = new Set(group.map((l) => l.kind));
    const directed = group.find((l) => l.kind === "via") || group.find((l) => l.kind === "uses");
    const physical = kinds.has("link");
    const text = [...new Set(group.map((l) => l.kind === "link" ? l.label : l.kind))].filter(Boolean).join(" · ");
    const title = group.map((l) => `${byId[l.source]?.label || l.source} → ${byId[l.target]?.label || l.target}: ${l.why.join("; ")}`).join("\n") + "\nClick: why this link";
    const main = physical ? "link" : directed!.kind;
    const color = main === "via" ? "var(--accent)" : main === "uses" ? "var(--muted)" : "var(--text)";
    const ends = directed || group[0];
    const route = paths[i];
    const faded = Math.min(opacity(ends.source), opacity(ends.target));
    return {
      id: `e${i}`, type: "link", source: `c:${ends.source}`, target: `c:${ends.target}`, zIndex: 1,
      data: { text, title, kind: main, links: group, open: () => onLinks(group), opacity: faded,
        path: route ? bridged(`e${i}`, route, verticals) : null, at: route ? labelPoint(route) : null },
      markerEnd: directed ? { type: "arrowclosed" as any, width: 14, height: 14, color } : undefined,
      style: { stroke: color, strokeWidth: physical ? 2.2 : 1.5, strokeDasharray: physical ? undefined : main === "via" ? "6 4" : "2 4", opacity: (physical ? 0.75 : 1) * faded },
    };
  });
  const pickedOpacity = picked ? (draft?.[picked]?.opacity ?? 1) : 1;

  return (
    <>
      <div className="lab-graph-tools">
        {draft ? (
          <>
            <span className="muted small">Drag the cards and the stage areas; click a card to set its opacity (its links follow).</span>
            {picked && (
              <label className="small lab-opacity">
                <strong>{byId[picked]?.label || picked}</strong> opacity
                <input type="range" min={0.1} max={1} step={0.05} value={pickedOpacity}
                  onChange={(e) => setDraft((d) => (d ? { ...d, [picked]: { ...d[picked], opacity: Number(e.target.value) } } : d))} />
                {Math.round(pickedOpacity * 100)}%
              </label>
            )}
            <span className="spacer" />
            <button className="small" onClick={() => { setDraft(null); setPicked(null); }}>Cancel</button>
            <button className="small primary" disabled={saving} onClick={saveArrangement} title="Every user of the Lab page sees this arrangement">Save for everyone</button>
          </>
        ) : (
          <>
            {manual && <span className="muted small" title={saved.data?.saved_at ? `saved ${time(saved.data.saved_at)}` : ""}>arranged by hand{saved.data?.saved_at ? `, ${ago(saved.data.saved_at)}` : ""}</span>}
            <span className="spacer" />
            <button className="small" onClick={() => setExpanded(new Set(comps.map((c) => c.id)))} title="Every card with its role, host, lock and current command">Expand all</button>
            <button className="small" disabled={!expanded.size} onClick={() => setExpanded(new Set())} title="Every card as picture, name and status">Collapse all</button>
            <button className="small" disabled={!placed} onClick={startArranging} title="Move cards and stages, set opacities, and save the arrangement for every user">Arrange…</button>
            {manual && <button className="small" onClick={resetArrangement} title="Forget the arrangement made by hand, for everyone">Automatic layout</button>}
          </>
        )}
      </div>
      <div className={classes("graph-box lab-graph", draft && "arranging")}>
        {!placed && <p className="muted" style={{ padding: 16 }}>Drawing the lab…</p>}
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onInit={setFlow} onNodesChange={onNodesChange}
          minZoom={0.15} maxZoom={2} nodesConnectable={false} proOptions={{ hideAttribution: true }}
          onNodeClick={(e, n) => {
            if (draft) {
              if (n.type === "component") setPicked((n.data as any).comp.id);
              return;
            }
            if (n.type === "component") toggle((n.data as any).comp.id);
            else if ((n.data as any).stage && (e.target as HTMLElement).closest(".lab-area-head")) onStage((n.data as any).stage.id);
          }}
          onEdgeClick={(_, e) => !draft && onLinks((e.data as any).links)}>
          <Background gap={16} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <div className="lab-legend muted small">
        <span><svg width="34" height="8"><line x1="0" y1="4" x2="34" y2="4" stroke="var(--text)" strokeWidth="2.2" opacity=".75" /></svg> wired (tsq_links)</span>
        <span><svg width="34" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="var(--accent)" strokeWidth="1.5" strokeDasharray="6 4" /><path d="M27 0l7 4-7 4z" fill="var(--accent)" /></svg> runs through (via)</span>
        <span><svg width="34" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="var(--muted)" strokeWidth="1.5" strokeDasharray="2 4" /><path d="M27 0l7 4-7 4z" fill="var(--muted)" /></svg> uses (its commands name it)</span>
        <span><span className="pulse run" /> runs a command now</span>
        <span>Click a card to expand it, a stage title, a link or a run for details.</span>
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
