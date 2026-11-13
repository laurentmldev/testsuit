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
// components outside every stage (as the components shared by stages), the links between components deduced from the lab configuration (probe: via; cmd: keys
// naming another component; link: tsq_links), and live: states, holds, the run holding each stage and the command each
// component runs now. A click on a box, an area, a link or a run gives its details or opens its page.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { ReactFlowInstance } from "@xyflow/react";
import {
  Background, BaseEdge, Controls, EdgeLabelRenderer, Handle, NodeToolbar, Position, ReactFlow,
  type Edge, type EdgeProps, type Node, type NodeChange, type NodeProps,
} from "@xyflow/react";
import { put, url, useResource } from "../api";
import { Dialog } from "../fields";
import { layoutWithRoutes, type Point } from "../graph";
import { ComponentIcon } from "../labicons";
import { ago, attempt, Badge, classes, navigate, STATE_COLORS, time, ViaTag } from "../util";

export interface LabView {
  links: LabLink[];
  icons: Record<string, { builtin?: string; file?: string; error?: string }>;
  runs: Record<string, RunInfo>;
  components: Record<string, Activity[]>;
}

export interface LabLink { source: string; target: string; kind: "probe" | "cmd" | "link"; label: string; details: LinkDetail[]; stages: string[] }
export interface LinkDetail { title: string; commands: CmdSegment[][] }
/** A piece of a command as it runs: literal text, or the value of a key (``key``), or why it has none. */
export interface CmdSegment { text: string; key: string | null; error?: string }
interface RunInfo { id: string; name?: string; testdef?: string; state: string; phase?: string; waiting_for?: string; stages: Record<string, string>; slots: Record<string, string> }
// via: the component its command runs through; for: on that component, the component the step is for
interface Activity { run: string; phase: string; step: string; slot: string; state: string; started?: string; command: string; via?: string; for?: string }

/** What a component's probe and commands run through (``through``: on another component, its ``via``), and what
 *  the probes and commands of others run through it (``relays``), from the ``probe`` links of the lab. */
export function viasOf(links: LabLink[] | undefined, id: string) {
  const probe = (links || []).filter((l) => l.kind === "probe");
  return {
    through: probe.filter((l) => l.target === id).map((l) => ({ other: l.source, what: l.details.map((d) => d.title) })),
    relays: probe.filter((l) => l.source === id).map((l) => ({ other: l.target, what: l.details.map((d) => d.title) })),
  };
}

/** The probes and commands that do not run on their own component: run through this one, or this one's run
 *  through another. */
export function ViaList({ vias, labelOf, onComponent }: { vias: ReturnType<typeof viasOf>; labelOf: (id: string) => string; onComponent?: (id: string) => void }) {
  if (!vias.through.length && !vias.relays.length) return null;
  const name = (id: string) => onComponent
    ? <button type="button" className="link" onClick={(e) => { e.stopPropagation(); onComponent(id); }}>{labelOf(id)}</button> : <strong>{labelOf(id)}</strong>;
  return (
    <div className="via-list small">
      {vias.through.map((v) => (
        <div key={`t:${v.other}`}><ViaTag via={v.other} label={labelOf(v.other)} title={`${v.what.join(", ")}: run on ${v.other}, which reaches this component`}>runs via</ViaTag>{" "}
          <span className="muted">{v.what.join(", ")}</span></div>
      ))}
      {vias.relays.map((v) => (
        <div key={`r:${v.other}`}><span className="via-tag" title={`${v.what.join(", ")} of ${v.other} run on this component, which reaches ${v.other}`}><span className="via-arrow">↪</span>runs for</span>{" "}
          {name(v.other)} <span className="muted">{v.what.join(", ")}</span></div>
      ))}
    </div>
  );
}

/** A step a component runs now: through another component (via), or on this one for another (for). */
function ActivityWhere({ a, labelOf }: { a: Activity; labelOf: (id: string) => string }) {
  if (a.for) return <ViaTag via={a.for} label={labelOf(a.for)} title={`this step is for ${a.for}: its command runs here, which reaches ${a.for}`}>for</ViaTag>;
  if (a.via) return <ViaTag via={a.via} label={labelOf(a.via)} title={`its command runs on ${a.via}, which reaches this component`} />;
  return null;
}

// a card: picture, name and status; the rest floats beside it when it is pointed at
const COMPACT = { width: 164, height: 132 };
const SHOWN_KEY = "tsq.lab.linktypes";     // the link types shown, in the browser
const KIND_HELP: Record<string, string> = {
  probe: "the other component's probe or some of its commands run through this one (via)",
  cmd: "the other component's commands, probe or variables name this one (stage.roles…, lab.components…)",
  link: "wired to it (tsq_links)",
};

const textOf = (segments: CmdSegment[]) => segments.map((s) => s.text).join("");

/** A command as it will run: each value from a key shows that key (and its template) when pointed at. */
export function Rendered({ segments }: { segments: CmdSegment[] }) {
  return (
    <span className="rendered">
      {segments.map((seg, i) => seg.key === null && !seg.error ? <span key={i}>{seg.text}</span>
        : <span key={i} className={seg.error ? "key-value error-text" : "key-value"} title={seg.error ? `{{ ${seg.key ?? ""} }}: ${seg.error}` : `{{ ${seg.key} }}`}>{seg.text}</span>)}
    </span>
  );
}

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
  const { comp, icon, activity, runs, shared, held, arranging, onProbe, vias, labelOf } = data as any;
  const [hovered, setHovered] = useState(false);
  // the run holding its stage shows on the stage's title: not again on each of its components
  const stageRuns = new Set<string>(Object.values(runs as Record<string, RunInfo>).filter((r) => (comp.stages || []).some((s: string) => Object.values(r.stages).includes(s))).map((r) => r.id));
  const color = STATE_COLORS[comp.state] || "muted";
  const src = icon?.file ? url(`lab/icons/${encodeURIComponent(comp.id)}`) : null;
  const running = (activity as Activity[]).find((a) => a.state === "running") || activity[0];
  const otherRuns = (held as any[]).filter((h) => !stageRuns.has(h.run));
  return (
    <div className={classes("lab-node compact", color, (comp.hold || comp.maintenance) && "off", activity.length > 0 && "busy")}
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <Handle type="target" position={Position.Top} className="lab-handle" /><Handle type="source" position={Position.Bottom} className="lab-handle" />
      <span className={classes("lab-icon xl", color)}><ComponentIcon icon={icon} src={src} size={52} /></span>
      <strong className="lab-name">{comp.label || comp.id}</strong>
      <div className="lab-node-tags">
        {arranging ? <Badge value={comp.state} />
          : <button type="button" className="link state-button" title="Its probe: what was tested, the answer, and why this state"
            onClick={(e) => { e.stopPropagation(); onProbe(); }}><Badge value={comp.state} /></button>}
        {running && <span className={classes("pulse", running.state === "running" ? "run" : "wait")} />}
        {comp.hold && <Badge value="hold" title={comp.note || ""} />}
        {comp.maintenance && <Badge value="maintenance" title={comp.note || ""} />}
      </div>
      {/* the detailed card, floating beside it while it is pointed at (not scaled with the diagram) */}
      <NodeToolbar isVisible={hovered && !arranging} position={Position.Right} offset={10} className="lab-hover">
        <div className="lab-node-head">
          <span className={classes("lab-icon xl", color)}><ComponentIcon icon={icon} src={src} size={44} /></span>
          <div className="lab-node-names">
            <strong>{comp.label || comp.id}</strong>
            <span className="muted small">{[comp.label && comp.label !== comp.id ? comp.id : null, comp.role].filter(Boolean).join(" · ")}</span>
            <span className="muted small">{comp.host_alias ? `${comp.host_alias} (${comp.host})` : comp.host}</span>
          </div>
        </div>
        {comp.description && <div className="lab-hover-text">{comp.description}</div>}
        <div className="lab-node-tags wrap">
          <Badge value={comp.state} />
          {comp.expected && comp.expected !== comp.state && <span className="warning-text">expected {comp.expected}</span>}
          {comp.hold && <Badge value="hold" />}
          {comp.maintenance && <Badge value="maintenance" />}
          {comp.lock !== "exclusive" && <span className="chip">lock {comp.lock}</span>}
          {shared && <span className="chip">in {shared.length} stages</span>}
        </div>
        {(comp.detail || comp.probed_at) && (
          <div className="muted small lab-hover-text">{comp.detail}{comp.detail && comp.probed_at ? " · " : ""}{comp.probed_at ? `probed ${ago(comp.probed_at)}` : ""}</div>
        )}
        {(comp.hold || comp.maintenance) && comp.note && <div className="muted small lab-hover-text">{comp.note}</div>}
        <ViaList vias={vias} labelOf={labelOf} />
        {activity.slice(0, 3).map((a: Activity) => (
          <div key={`${a.run}:${a.phase}:${a.step}:${a.for || ""}`} className="lab-activity">
            <span className={classes("pulse", a.state === "running" ? "run" : "wait")} />
            <code>{a.command || a.step}</code>
            <ActivityWhere a={a} labelOf={labelOf} />
            <span className="muted small">{a.phase}:{a.step} · {runName(runs[a.run], a.run)}</span>
          </div>
        ))}
        {activity.length > 3 && <div className="muted small">and {activity.length - 3} more steps</div>}
        {otherRuns.length > 0 && activity.length === 0 && (
          <div className="lab-activity"><span className="muted small">held by</span> {otherRuns.map((h: any) => <span key={h.run} className="chip run">{runName(runs[h.run], h.run)}</span>)}</div>
        )}
        <div className="muted small lab-hover-foot">Click the card for its details and actions, its state for its probe.</div>
      </NodeToolbar>
    </div>
  );
}

/** The host name (tsq_hosts) the components of an address all give, if they do. */
function sharedAlias(members: any[]): string {
  const alias = members[0]?.host_alias || "";
  return members.every((c) => (c?.host_alias || "") === alias) ? alias : "";
}

function AreaNode({ data }: NodeProps) {
  const { stage, runs, host, alias, members } = data as any;
  if (host) {
    return (
      <div className="lab-area host" title={`${members.length} components at the address ${alias ? `${alias} (${host})` : host}: ${members.join(", ")}`}>
        <div className="lab-host-head"><code>{alias || host}</code><span className="muted small">{members.length} components</span></div>
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
const HOST_HEAD = 30;                                 // a box around the components that share an address
const HOST_PAD = 10;

export function LabGraph({ comps, stages, view, onComponent, onProbe, onStage, onLinks }: {
  comps: any[]; stages: any[]; view: LabView | null;
  onComponent: (id: string) => void; onProbe: (id: string) => void; onStage: (id: string) => void; onLinks: (links: LabLink[]) => void;
}) {
  const [placed, setPlaced] = useState<Record<string, Rect> | null>(null);
  const [routes, setRoutes] = useState<Record<string, Point[]>>({});
  const saved = useResource<{ components: Arrangement; saved_at?: string }>("lab/layout", ["lab_layout"]);
  const [draft, setDraft] = useState<Arrangement | null>(null);      // while arranging
  const [picked, setPicked] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [shownKinds, setShownKinds] = useState<Set<string>>(() => {     // this viewer's choice, kept in the browser
    try {
      const kept = JSON.parse(window.localStorage.getItem(SHOWN_KEY) || "null");
      if (Array.isArray(kept)) return new Set(kept);
    } catch {
      /* no storage: all shown */
    }
    return new Set(["link", "probe", "cmd"]);
  });
  const showKind = (kind: string, on: boolean) => setShownKinds((all) => {
    const next = new Set(all);
    if (on) next.add(kind);
    else next.delete(kind);
    try {
      window.localStorage.setItem(SHOWN_KEY, JSON.stringify([...next]));
    } catch {
      /* not kept */
    }
    return next;
  });
  const [flow, setFlow] = useState<ReactFlowInstance<any, any> | null>(null);
  const [fitted, setFitted] = useState(false);
  useEffect(() => {                // the whole lab in view once placed
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
  // a component of one stage is in its area; an ad hoc one (in no stage) and a shared one are outside every stage
  const parentOf = (c: any) => (c.stages?.length === 1 ? `stage:${c.stages[0]}` : undefined);
  const headOf = (s: any) => ((s.holders || []).length ? 84 : 58);
  // the components of an area that share an address, in a box of their own
  const hostGroups: Record<string, string[]> = {};
  for (const c of comps) {
    if (!c.host) continue;
    const id = `host:${parentOf(c) ?? ""}:${c.host}`;
    (hostGroups[id] ||= []).push(c.id);
  }
  for (const id of Object.keys(hostGroups)) if (hostGroups[id].length < 2) delete hostGroups[id];
  const hostOf: Record<string, string> = Object.fromEntries(Object.entries(hostGroups).flatMap(([h, ids]) => ids.map((c) => [c, h])));
  const hostParent = (h: string) => parentOf(byId[hostGroups[h][0]]);
  /** What holds a node in the layout: a component in its address box or its area, an address box in its area. */
  const holderOf = (id: string): string | undefined => id.startsWith("c:") ? (hostOf[id.slice(2)] ?? parentOf(byId[id.slice(2)]))
    : id.startsWith("host:") ? hostParent(id) : undefined;
  /** The components inside an area or an address box: they move with it. */
  const membersOf = (id: string) => id.startsWith("host:") ? (hostGroups[id] || []).map((c) => byId[c]) : comps.filter((c) => parentOf(c) === id);
  const size = (_c: any) => COMPACT;
  // the automatic layout changes with what is drawn and the sizes, not with the states
  const structure = JSON.stringify([stages.map((s) => [s.id, s.components, headOf(s)]),
    comps.map((c) => [c.id, c.stages, size(c), hostOf[c.id]]), links.map((l) => [l.source, l.target])]);
  useEffect(() => {
    let cancelled = false;
    const nodes: any[] = [];
    for (const s of stages) nodes.push({ id: `stage:${s.id}`, width: AREA_MIN_W, height: 120, padding: `[top=${headOf(s)},left=18,bottom=18,right=18]`, minWidth: AREA_MIN_W });
    for (const h of Object.keys(hostGroups)) nodes.push({ id: h, width: 160, height: 80, padding: `[top=${HOST_HEAD},left=${HOST_PAD},bottom=${HOST_PAD},right=${HOST_PAD}]`, parent: hostParent(h) });
    for (const c of comps) nodes.push({ id: `c:${c.id}`, ...size(c), parent: holderOf(`c:${c.id}`) });
    const edges = groups.map((group, i) => {
      const directed = group.find((l) => l.kind === "probe") || group.find((l) => l.kind === "cmd") || group[0];
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
  /** Where ELK put a node, on the whole diagram (it gives each node relative to what holds it). */
  const absolute = (id: string): Rect | null => {
    const p = placed?.[id];
    if (!p) return null;
    const holder = holderOf(id);
    const q = holder ? absolute(holder) : null;
    return { ...p, x: p.x + (q?.x || 0), y: p.y + (q?.y || 0) };
  };
  const autoRect = (c: any): Rect | null => absolute(`c:${c.id}`);
  const rects: Record<string, Rect> = {};
  for (const c of comps) {
    const auto = autoRect(c);
    if (!auto) continue;
    const a = arrangement[c.id];
    rects[c.id] = a?.x !== undefined && a?.y !== undefined ? { ...size(c), x: a.x, y: a.y } : auto;
  }
  const opacity = (id: string) => arrangement[id]?.opacity ?? 1;
  const around = (members: Rect[], head: number, pad: { left: number; right: number; bottom: number }, minWidth: number): Rect => {
    const x0 = Math.min(...members.map((r) => r.x)) - pad.left;
    const y0 = Math.min(...members.map((r) => r.y)) - head;
    const x1 = Math.max(...members.map((r) => r.x + r.width)) + pad.right;
    const y1 = Math.max(...members.map((r) => r.y + r.height)) + pad.bottom;
    return { x: x0, y: y0, width: Math.max(x1 - x0, minWidth), height: y1 - y0 };
  };
  const hostRects: Record<string, Rect> = {};
  for (const h of Object.keys(hostGroups)) {
    const members = hostGroups[h].map((c) => rects[c]).filter(Boolean);
    const r = !manual ? absolute(h) : members.length ? around(members, HOST_HEAD, { left: HOST_PAD, right: HOST_PAD, bottom: HOST_PAD }, 0) : null;
    if (r) hostRects[h] = r;
  }
  const areaRect = (id: string, head: number): Rect | null => {
    if (!manual) return absolute(id);
    const members = [...comps.filter((c) => parentOf(c) === id && !hostOf[c.id] && rects[c.id]).map((c) => rects[c.id]),
      ...Object.keys(hostRects).filter((h) => hostParent(h) === id).map((h) => hostRects[h])];
    if (!members.length) return absolute(id);
    return around(members, head, AREA_PAD, AREA_MIN_W);
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
        } else {                                  // a stage area or an address box: its components move with it
          const area = nodesById[ch.id];
          if (!area) continue;
          const dx = ch.position.x - area.position.x;
          const dy = ch.position.y - area.position.y;
          for (const c of membersOf(ch.id).filter((c) => rects[c.id])) {
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
    for (const [h, r] of Object.entries(hostRects)) {
      nodes.push({ id: h, type: "area", position: { x: r.x, y: r.y }, data: { host: byId[hostGroups[h][0]].host, alias: sharedAlias(hostGroups[h].map((c) => byId[c])), members: hostGroups[h].map((c) => byId[c].label || c) },
        style: { width: r.width, height: r.height }, draggable: !!draft, selectable: false, zIndex: 0 });
    }
    for (const c of comps) {
      const r = rects[c.id];
      if (!r) continue;
      nodes.push({
        id: `c:${c.id}`, type: "component", position: { x: r.x, y: r.y }, style: { width: r.width, height: r.height, opacity: opacity(c.id) },
        draggable: !!draft, selectable: false, zIndex: 2, className: picked === c.id ? "lab-picked" : undefined,
        data: { comp: c, icon: view?.icons?.[c.id], activity: activity[c.id] || [], runs, shared: (c.stages?.length || 0) > 1 ? c.stages : null, held: c.holders || [],
          arranging: !!draft, onProbe: () => onProbe(c.id), vias: viasOf(view?.links, c.id), labelOf: (id: string) => byId[id]?.label || id },
      });
    }
  }
  for (const n of nodes) if (measured[n.id]) n.measured = measured[n.id];
  const nodesById = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const routeOf = (i: number, group: LabLink[]): Point[] | null => {
    const directed = group.find((l) => l.kind === "probe") || group.find((l) => l.kind === "cmd") || group[0];
    if (!manual) return routes[`e${i}`] ?? null;
    const s = rects[directed.source];
    const t = rects[directed.target];
    return s && t ? squareRoute(s, t) : null;
  };
  // the link types shown: hiding some keeps the layout as it is (the links still place the cards)
  const visible = groups.map((group) => group.filter((l) => shownKinds.has(l.kind)));
  const paths = groups.map((group, i) => (visible[i].length ? routeOf(i, group) : null));
  const verticals: Segment[] = paths.flatMap((r, i) => (r ? r.slice(1).map((b, k) => ({ a: r[k], b, edge: `e${i}` })) : []))
    .filter((sg) => Math.abs(sg.a.x - sg.b.x) < 0.5);
  const edges: Edge[] = groups.flatMap((all, i) => {
    const group = visible[i];
    if (!group.length) return [];
    const kinds = new Set(group.map((l) => l.kind));
    const directed = group.find((l) => l.kind === "probe") || group.find((l) => l.kind === "cmd");
    const physical = kinds.has("link");
    const text = [...new Set(group.map((l) => l.kind === "link" ? l.label
      : l.kind === "probe" ? `via: ${l.details.map((d) => d.title.replace(/^command /, "").replace("status probe", "probe")).join(", ")}` : l.kind))].filter(Boolean).join(" · ");
    const title = group.map((l) => `${byId[l.source]?.label || l.source} → ${byId[l.target]?.label || l.target} (${l.kind})\n${l.details.map((d) => `  ${d.title}${d.commands.length ? ": " + d.commands.map(textOf).join(" ; ") : ""}`).join("\n")}`).join("\n") + "\nClick: details";
    const main = physical ? "link" : directed!.kind;
    const color = main === "probe" ? "var(--accent)" : main === "cmd" ? "var(--muted)" : "var(--text)";
    const ends = all.find((l) => l.kind === "probe") || all.find((l) => l.kind === "cmd") || all[0];
    const route = paths[i];
    const faded = Math.min(opacity(ends.source), opacity(ends.target));
    return [{
      id: `e${i}`, type: "link", source: `c:${ends.source}`, target: `c:${ends.target}`, zIndex: 1,
      data: { text, title, kind: main, links: group, open: () => onLinks(group), opacity: faded,
        path: route ? bridged(`e${i}`, route, verticals) : null, at: route ? labelPoint(route) : null },
      markerEnd: directed ? { type: "arrowclosed" as any, width: 14, height: 14, color } : undefined,
      style: { stroke: color, strokeWidth: physical ? 2.2 : 1.5, strokeDasharray: physical ? undefined : main === "probe" ? "6 4" : "2 4", opacity: (physical ? 0.75 : 1) * faded },
    }];
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
            if (n.type === "component") onComponent((n.data as any).comp.id);
            else if ((n.data as any).stage && (e.target as HTMLElement).closest(".lab-area-head")) onStage((n.data as any).stage.id);
          }}
          onEdgeClick={(_, e) => !draft && onLinks((e.data as any).links)}>
          <Background gap={16} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <div className="lab-legend muted small">
        <span className="lab-legend-title" title="Click a link type to hide or show it">Links:</span>
        {([
          ["link", "wired (tsq_links)", <svg width="34" height="8"><line x1="0" y1="4" x2="34" y2="4" stroke="var(--text)" strokeWidth="2.2" opacity=".75" /></svg>],
          ["probe", "via (probe or command run through it)", <svg width="34" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="var(--accent)" strokeWidth="1.5" strokeDasharray="6 4" /><path d="M27 0l7 4-7 4z" fill="var(--accent)" /></svg>],
          ["cmd", "cmd (named by a command)", <svg width="34" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="var(--muted)" strokeWidth="1.5" strokeDasharray="2 4" /><path d="M27 0l7 4-7 4z" fill="var(--muted)" /></svg>],
        ] as [string, string, ReactNode][]).map(([kind, label, sample]) => (
          <button key={kind} type="button" className={classes("lab-legend-kind", !shownKinds.has(kind) && "hidden")} aria-pressed={shownKinds.has(kind)}
            title={`${shownKinds.has(kind) ? "Hide" : "Show"} the links of this type: ${KIND_HELP[kind]}`} onClick={() => showKind(kind, !shownKinds.has(kind))}>
            {sample} {label} <span className="muted">({links.filter((l) => l.kind === kind).length})</span>
          </button>
        ))}
        <span><span className="pulse run" /> runs a command now</span>
        <span>Point at a card for more; click it, a stage title, a link or a run for details, a state for its probe.</span>
      </div>
    </>
  );
}

// ── dialogs ─────────────────────────────────────────────────────────────────

/** Why two components are linked: what in the lab configuration says so. */
export function LinksDialog({ links, labelOf, onClose, onComponent }: { links: LabLink[]; labelOf: (id: string) => string; onClose: () => void; onComponent: (id: string) => void }) {
  const ends = [...new Set(links.flatMap((l) => [l.source, l.target]))];
  return (
    <Dialog title={`Link ${ends.map(labelOf).join(" – ")}`} onClose={onClose} wide>
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
            <td><span className={classes("chip", l.kind === "probe" ? "run" : l.kind === "link" ? "ok" : "")} title={KIND_HELP[l.kind]}>{l.kind === "probe" ? "via" : l.kind}{l.label ? `: ${l.label}` : ""}</span></td>
            <td className="small">
              {(l.details || []).map((d) => (
                <div key={d.title} className="lab-link-detail">
                  <strong>{d.title}</strong>
                  {d.commands.map((c, i) => <div key={i} className="lab-link-command"><Rendered segments={c} /></div>)}
                </div>
              ))}
              {l.stages.length > 0 && <div className="muted">in {l.stages.join(", ")}</div>}
            </td>
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
  const vias = viasOf(view?.links, comp.id);
  return (
    <Dialog title={comp.label || comp.id} onClose={onClose} wide
      footer={<>
        {actions}
        <span className="spacer" />
        <button onClick={onTable} title="The Table tab, on this component">Show in the table</button>
        {onEdit && <button onClick={onEdit} title="Open the lab inventory in the Editor, on this component">Edit in the Editor</button>}
      </>}>
      <div className="lab-dialog-head">
        <span className={classes("lab-icon big", STATE_COLORS[comp.state] || "muted")}><ComponentIcon icon={icon} src={src} size={44} /></span>
        <div>
          <div><code>{comp.id}</code> · role <code>{comp.role}</code> · extends <code>{comp.extends}</code> · host <code>{comp.host_alias ? `${comp.host_alias} (${comp.host})` : comp.host}</code> · lock {comp.lock}</div>
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
      {(vias.through.length > 0 || vias.relays.length > 0) && (
        <>
          <h4>Run through another component</h4>
          <ViaList vias={vias} labelOf={labelOf} onComponent={onComponent} />
        </>
      )}
      <h4>Runs</h4>
      {!(comp.holders?.length) && !activity.length ? <p className="muted">Free: no run holds it.</p> : (
        <table className="grid compact">
          <tbody>
            {(comp.holders || []).map((h: any) => (
              <tr key={h.run}>
                <td><RunChip run={runs[h.run]} id={h.run} /></td>
                <td className="small">{h.run} · lock {h.mode}{runs[h.run] && <> · {runs[h.run].state}{runs[h.run].phase ? `, phase ${runs[h.run].phase}` : ""}</>}</td>
                <td className="small">{activity.filter((a) => a.run === h.run).map((a) => <div key={`${a.step}:${a.slot}:${a.for || ""}`}><span className={classes("pulse", a.state === "running" ? "run" : "wait")} /> <code>{a.command || a.step}</code> <ActivityWhere a={a} labelOf={labelOf} /> <span className="muted">step {a.phase}:{a.step}, slot {a.slot}{a.started ? `, since ${ago(a.started)}` : ""}</span></div>)}</td>
              </tr>
            ))}
            {activity.filter((a) => !(comp.holders || []).some((h: any) => h.run === a.run)).map((a) => (
              <tr key={`${a.run}:${a.step}:${a.slot}:${a.for || ""}`}><td><RunChip run={runs[a.run]} id={a.run} /></td><td className="small">{a.run}</td>
                <td className="small"><span className={classes("pulse", a.state === "running" ? "run" : "wait")} /> <code>{a.command || a.step}</code> <ActivityWhere a={a} labelOf={labelOf} /> <span className="muted">step {a.phase}:{a.step}, slot {a.slot}</span></td></tr>
            ))}
          </tbody>
        </table>
      )}
      <h4>Links</h4>
      {links.length ? <LinkList links={links} labelOf={labelOf} onComponent={onComponent} me={comp.id} /> : <p className="muted">None found in the lab configuration (tsq_links, via, keys naming another component).</p>}
    </Dialog>
  );
}

/** A stage of the diagram: what it is, the run holding it, and its actions. */
export function StageDialog({ stage, view, labelOf, actions, onClose, onComponent }: {
  stage: any; view: LabView | null; labelOf: (id: string) => string; actions: ReactNode; onClose: () => void; onComponent: (id: string) => void;
}) {
  const runs = view?.runs || {};
  return (
    <Dialog title={`Stage ${stage.id}`} onClose={onClose} wide footer={<>{actions}<span className="spacer" /></>}>
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
    </Dialog>
  );
}
