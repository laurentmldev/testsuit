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

// One run (sequencer README, section 13.1): its pipeline as a live graph of phases, sub-pipelines and steps,
// the logs of each step, its files before, during and after the run.
import { RunsCoverage } from "./Requirements";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { applyNodeChanges, Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps, type ReactFlowInstance } from "@xyflow/react";
import { get, getText, post, url, useLabels, useResource } from "../api";
import { FileBrowser } from "../files";
import { COMMENT_WIDTH, CommentNode, PinEdge, pinEdge } from "../comments";
import { layout, mergeGroupEdges } from "../graph";
import { ago, attempt, Badge, classes, ConsoleText, Elapsed, openInNewTab, setQuery, Spinner, STATE_COLORS, time, useTick, ViaTag } from "../util";
import { Named } from "../kinds";
import { ManualOperations, rerun } from "./Manual";
import { Interventions, PauseBanners, StepActions, StepModeControl } from "./Operator";
import { abort, ACTIVE, PriorityInput, repost, RunStateBadge } from "./Queue";
import { ExportButton, ReviewButtons, ReviewedVerdict, ReviewNotes, ReviewStateBadge, RunReviewTab } from "./Review";

const ENDED = ["succeeded", "failed", "error", "skipped"];
/** The id of a phase's finally lane in the graph (after "phase:<id>/"), a name no step or sub-pipeline can take. */
const FINALLY_LANE = "~finally";
/** The id of the frame around the other steps of a phase that has finally steps: their arrows to the finally lane
 *  merge into one from it. */
const STEPS_LANE = "~steps";

function worst(states: string[]): string {
  for (const s of ["error", "failed", "running", "ready", "pending", "skipped", "succeeded"]) if (states.includes(s)) return s;
  return states[0] || "pending";
}

export function stepKey(phase: string, id: string, slot?: string) {
  return `${phase}:${id}` + (slot ? `[${slot}]` : "");
}

/** The slot a step record ran for ('' for the whole step); runs recorded before slots were named say 'component'. */
export const slotOf = (record: any): string => record?.slot ?? record?.component ?? "";
/** Whether a selected key ('phase:id' or 'phase:id[slot]') is of this step: of it only, not of another step whose id
 *  starts the same ('deploy' and 'deploy_check'). */
export const isOfStep = (key: string | null | undefined, phase: string, id: string): boolean =>
  !!key && (key === `${phase}:${id}` || key.startsWith(`${phase}:${id}[`));

/** The height of a row of slot names on a step card, and the rows shown at most (more are scrolled to). */
const SLOT_ROW = 19;
const SLOT_ROWS_SHOWN = 6;
/** The rows the slot names of a step take on its card (150 px wide): none when it runs on one slot. */
function slotRows(names?: string[]): number {
  if (!names || names.length < 2) return 0;
  let rows = 1, width = 0;
  for (const n of names) {
    const w = n.length * 7 + 17;
    if (width && width + w > 134) { rows++; width = 0; }
    width += w + 3;
  }
  return Math.min(rows, SLOT_ROWS_SHOWN);
}

function StepNode({ data }: NodeProps) {
  const d = data as any;
  // its command runs through another component (a command's via): said on the step itself
  const vias = [...new Set<string>((d.items || []).map((i: any) => i.via).filter(Boolean))];
  // a file being sent (a REST API call's body file): its progress on the step
  const uploading = (d.items || []).find((i: any) => i.state === "running" && i.progress);
  return (
    <div className={classes("step-node", STATE_COLORS[d.state] || "muted", d.selected && "selected", d.part === "finally" && "finally",
      (d.items || []).some((i: any) => i.state === "running") && "pulsing")}
      title={d.title}>
      <Handle type="target" position={Position.Left} />
      <div className="step-id">{d.label}</div>
      {(d.items || []).some((i: any) => i.state === "running" && i.manual && !i.manual.outcome)
        ? <div className="step-action step-manual" title="Waits for a manual operation: open it from the banner above">✋ waits for you</div>
        : <div className="step-action">{d.action}</div>}
      {uploading && <UploadBar progress={uploading.progress} compact />}
      {vias.length > 0 && <div className="step-via">{vias.map((v) => <ViaTag key={v} via={v} title={`its command runs on ${v}, which reaches the component of its slot`} />)}</div>}
      {d.items?.length > 1 && (
        <div className="step-items nowheel" style={{ maxHeight: SLOT_ROWS_SHOWN * SLOT_ROW }}>
          {/* each slot of the step is picked here, its log then shown aside (no tabs there: they overflowed with many slots) */}
          {d.items.map((i: any) => (
            <span key={slotOf(i)} className={classes("chip", "slot-pick", STATE_COLORS[i.state] || "muted", d.selected && d.selectedSlot === slotOf(i) && "picked")}
              title={`${slotOf(i)}: ${i.state} (click to see its log)`}
              onClick={(e) => { e.stopPropagation(); d.onPickSlot?.(slotOf(i)); }}>{slotOf(i)}</span>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function sizeText(n: number): string {
  const units = ["B", "KiB", "MiB", "GiB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return i ? `${n.toFixed(1)} ${units[i]}` : `${n} B`;
}

/** The progress of a file a step sends (a REST API call's body file), as the sequencer last told it. */
export function UploadBar({ progress, compact }: { progress: { sent: number; total: number }; compact?: boolean }) {
  const pct = progress.total > 0 ? Math.min(100, Math.floor((progress.sent * 100) / progress.total)) : 100;
  const text = `${sizeText(progress.sent)} of ${sizeText(progress.total)} sent (${pct}%)`;
  return (
    <div className={classes("upload-progress", compact && "compact")} title={text}>
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><div style={{ width: `${pct}%` }} /></div>
      {compact ? <span className="small">{pct}%</span> : <span className="small">{text}</span>}
    </div>
  );
}

function FrameNode({ data }: NodeProps) {
  const d = data as any;
  return (
    <div className={classes("frame-node", d.kind, d.state && STATE_COLORS[d.state])}>
      {(d.kind === "finally" || d.kind === "sub" || d.kind === "steps") && <Handle type="target" position={Position.Left} />}
      <div className="frame-title">{d.label}{d.state && <Badge value={d.state} />}</div>
      {/* a sub-pipeline's arrows, merged into one when they all go to the same place (mergeGroupEdges) */}
      {(d.kind === "sub" || d.kind === "steps") && <Handle type="source" position={Position.Right} />}
    </div>
  );
}

const nodeTypes = { step: StepNode, frame: FrameNode, comment: CommentNode };
const edgeTypes = { pin: PinEdge };

/** About how tall a comment card of the run graph is (its Markdown at 12px, COMMENT_WIDTH wide): the room its
 *  sub-pipeline frame keeps for it, laid out before the card is drawn. */
function commentHeight(text: string): number {
  const perLine = Math.floor((COMMENT_WIDTH - 20) / 6.6);
  const lines = text.trim().split("\n").reduce((sum, line) => sum + Math.max(1, Math.ceil(line.length / perLine)), 0);
  return 14 + lines * 17;
}

export function RunGraph({ pipeline, records, selected, onSelect, focus }: {
  pipeline: any; records: any[]; selected: string | null; onSelect: (key: string, step: any, phase: string) => void;
  focus?: string | null;                         // a step ('phase:step') to bring into view, at the same zoom
}) {
  const flow = useRef<ReactFlowInstance | null>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const structure = useMemo(() => JSON.stringify(pipeline), [pipeline]);
  const byStep = useMemo(() => {
    const map: Record<string, any[]> = {};
    for (const r of records) (map[`${r.phase}:${r.id}`] ||= []).push(r);
    return map;
  }, [records]);
  // the slots of each step run on several: their names on its card make it taller, laid out again when they change
  const slotNames = useMemo(() => JSON.stringify(Object.fromEntries(Object.entries(byStep).filter(([, rs]) => rs.length > 1)
    .map(([k, rs]) => [k, rs.map(slotOf)]))), [byStep]);

  useEffect(() => {
    let cancelled = false;
    const lay: any[] = [];
    let edgeList: Edge[] = [];
    // the steps inside each sub-pipeline frame, at any depth: their arrows are merged into one from or to the frame
    const members: Record<string, string[]> = {};
    const slots: Record<string, string[]> = JSON.parse(slotNames);
    for (const phase of pipeline.phases) {
      const pid = `phase:${phase.id}`;
      lay.push({ id: pid, width: 200, height: 80 });
      for (const need of phase.needs) edgeList.push({ id: `${need}->${phase.id}`, source: `phase:${need}`, target: pid, className: "phase-edge" });
      const groups = new Set<string>();
      // the finally steps in a lane of their own, after the steps of the phase: they run in the end whatever
      // happened (cancel and kill included), their states shown live like the other steps
      const lane = `${pid}/${FINALLY_LANE}`;
      const hasFinally = phase.steps.some((s: any) => s.part === "finally");
      // and the other steps in a frame of their own then, beside it: one arrow from it to the lane
      const stepsLane = `${pid}/${STEPS_LANE}`;
      if (hasFinally) {
        lay.push({ id: stepsLane, width: 170, height: 60, parent: pid, minWidth: 170 });
        lay.push({ id: lane, width: 170, height: 60, parent: pid, minWidth: 170 });
        members[stepsLane] = phase.steps.filter((s: any) => s.part === "steps").map((s: any) => `${pid}/${s.id}`);
      }
      for (const s of phase.steps) {
        // nested sub-pipeline frames: 'up', then 'up.inner'...
        const parts = s.group ? s.group.split(".") : [];
        let parent = s.part === "finally" ? lane : hasFinally ? stepsLane : pid;
        for (let i = 1; i <= parts.length; i++) {
          const g = `${pid}/${parts.slice(0, i).join(".")}/${s.part}`;
          if (!groups.has(g)) {
            groups.add(g);
            lay.push({ id: g, width: 160, height: 60, parent });
          }
          (members[g] ||= []).push(`${pid}/${s.id}`);
          parent = g;
        }
        lay.push({ id: `${pid}/${s.id}`, width: 150, height: 56 + slotRows(slots[`${phase.id}:${s.id}`]) * SLOT_ROW, parent });
        for (const n of s.needs) edgeList.push({ id: `${phase.id}:${n}->${s.id}`, source: `${pid}/${n}`, target: `${pid}/${s.id}` });
      }
      // the finally lane after every last step of the phase
      if (hasFinally) {
        const lasts = phase.steps.filter((s: any) => s.part === "steps" && !phase.steps.some((o: any) => o.needs.includes(s.id)));
        for (const l of lasts) edgeList.push({ id: `${phase.id}:${l.id}~>finally`, source: `${pid}/${l.id}`, target: lane, className: "finally-edge" });
      }
    }
    // the comment cards of each sub-pipeline, at the bottom of its frame (one frame per 'uses' step): room for them
    // there, below its steps, and a frame wide enough for them side by side
    const groupCards: Record<string, { card: any; height: number }[]> = {};
    for (const phase of pipeline.phases) {
      for (const c of phase.comments || []) {
        const frame = c.group ? `phase:${phase.id}/${c.group}/${c.part || "steps"}` : "";
        if (frame && lay.some((n) => n.id === frame)) (groupCards[frame] ||= []).push({ card: c, height: commentHeight(c.text) });
      }
    }
    for (const [frame, cards] of Object.entries(groupCards)) {
      const n = lay.find((l) => l.id === frame);
      n.padding = `[top=36,left=16,bottom=${16 + Math.max(...cards.map((c) => c.height)) + 14},right=16]`;
      n.minWidth = 32 + cards.length * COMMENT_WIDTH + (cards.length - 1) * 12;
    }
    // one arrow from a sub-pipeline rather than one from each of its last steps (to the finally lane, or to the
    // steps after it), and one into it rather than one to each of its first steps
    edgeList = mergeGroupEdges(edgeList, members);
    const crossPhase = edgeList.filter((e) => e.className === "phase-edge");
    layout(lay, edgeList.filter((e) => e.className !== "phase-edge").concat(crossPhase).map((e) => ({ id: e.id, source: e.source, target: e.target }))).then((placed) => {
      if (cancelled) return;
      const list: Node[] = [];
      for (const n of lay) {
        const p = placed[n.id];
        if (!p) continue;
        const isFrame = n.id.startsWith("phase:") && (n.id.split("/").length === 1 || n.id.endsWith("/steps") || n.id.endsWith("/finally"));
        const [phaseId, rest] = [n.id.split("/")[0].slice(6), n.id.split("/").slice(1)];
        const base = { id: n.id, position: { x: p.x, y: p.y }, ...(n.parent ? { parentId: n.parent, extent: "parent" as const } : {}), draggable: false };
        if (rest.length === 1 && rest[0] === STEPS_LANE) {
          list.push({ ...base, type: "frame", data: { label: "steps", kind: "steps", phase: phaseId }, style: { width: p.width, height: p.height }, selectable: false });
        } else if (rest.length === 1 && rest[0] === FINALLY_LANE) {
          list.push({ ...base, type: "frame", data: { label: "finally · always runs", kind: "finally", phase: phaseId }, style: { width: p.width, height: p.height }, selectable: false });
        } else if (isFrame) {
          const label = rest.length ? `${rest[0]} (sub-pipeline${rest[1] === "finally" ? ", finally" : ""})` : `phase ${phaseId}`;
          list.push({ ...base, type: "frame", data: { label, kind: rest.length ? "sub" : "phase", phase: phaseId }, style: { width: p.width, height: p.height }, selectable: false });
        } else {
          const step = pipeline.phases.find((ph: any) => ph.id === phaseId).steps.find((s: any) => s.id === rest.join("/"));
          list.push({ ...base, type: "step", data: { step, phase: phaseId }, style: { width: p.width } });
        }
      }
      // the comment cards of each phase (notes, never run), side by side under its frame, and those of its
      // sub-pipelines at the bottom of theirs (or under the phase when their frame is not drawn)
      // (a card pinned to a step says which: the run graph is laid out otherwise than the Editor's)
      const cardData = (c: any) => (typeof c === "string" ? { text: c } : { text: c.text, pin: c.step || undefined, color: c.color || undefined });
      for (const phase of pipeline.phases) {
        const p = placed[`phase:${phase.id}`];
        if (!p) continue;
        let free = 0;
        (phase.comments || []).forEach((c: any, i: number) => {
          const frame = c.group ? `phase:${phase.id}/${c.group}/${c.part || "steps"}` : "";
          const id = `comment:${phase.id}:${i}`;
          const style = { width: COMMENT_WIDTH };
          const g = groupCards[frame] && placed[frame];
          if (g) {
            const at = groupCards[frame].findIndex((x) => x.card === c);
            const room = Math.max(...groupCards[frame].map((x) => x.height));
            list.push({ id, type: "comment", parentId: frame, extent: "parent", position: { x: 16 + at * (COMMENT_WIDTH + 12), y: g.height - room - 16 },
              data: cardData(c), style, draggable: false, selectable: false });
          } else {
            list.push({ id, type: "comment", position: { x: p.x + (free++) * (COMMENT_WIDTH + 12), y: p.y + p.height + 16 },
              data: cardData(c), style, draggable: false, selectable: false });
          }
        });
      }
      // a pinned card: a line to its step (an id of its phase, or of its sub-pipeline under its 'uses' step)
      const pins: Edge[] = [];
      for (const phase of pipeline.phases) {
        (phase.comments || []).forEach((c: any, i: number) => {
          // (pinned to a 'uses' step: to the frame of its sub-pipeline)
          const step = typeof c === "object" && c.step ? `phase:${phase.id}/${c.group ? `${c.group}.` : ""}${c.step}` : "";
          const node = step && [step, `${step}/steps`, `${step}/finally`].find((id) => list.some((n) => n.id === id));
          if (node) pins.push(pinEdge(`comment:${phase.id}:${i}`, node));
        });
      }
      setNodes(list);
      setEdges(pins.concat(edgeList.map((e) => ({
        ...e, markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
        style: e.className === "finally-edge" ? { strokeDasharray: "4 3", opacity: 0.5 } : e.className === "phase-edge" ? { strokeWidth: 2 } : undefined,
      }))));
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure, slotNames]);

  const live = nodes.map((n) => {
    if (n.type === "comment") return n;
    if (n.type === "frame") {
      const { phase, kind } = n.data as any;
      if (kind === "finally") {
        // the state of the finally steps once one has started, running while one runs
        const finals = new Set(pipeline.phases.find((ph: any) => ph.id === phase)?.steps.filter((s: any) => s.part === "finally").map((s: any) => s.id));
        const states = records.filter((r) => r.phase === phase && finals.has(r.id)).map((r) => r.state);
        return { ...n, data: { ...n.data, state: states.length ? worst(states) : undefined } };
      }
      if (kind !== "phase") return n;
      const states = records.filter((r) => r.phase === phase).map((r) => r.state);
      return { ...n, data: { ...n.data, state: states.length ? worst(states) : undefined } };
    }
    const { step, phase } = n.data as any;
    const items = byStep[`${phase}:${step.id}`] || [];
    const state = items.length ? worst(items.map((i) => i.state)) : "pending";
    const target = step.slot || (Array.isArray(step.for_each) ? step.for_each.join(", ") : step.for_each) || "";
    // an exploit step: how many of its checks are met, once it has run
    const checks: any[] | undefined = step.action === "exploit" ? items[0]?.exploit?.checks : undefined;
    const met = checks ? `${checks.filter(checkMet).length}/${checks.length} checks` : "";
    // a do: step's command parameters, once it has run (the values it ran with)
    const params: Record<string, any> | undefined = items.find((i: any) => i.lab_command?.params)?.lab_command?.params;
    const paramText = params ? Object.entries(params).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ") : "";
    return {
      ...n, data: {
        ...n.data, label: step.id.split(".").pop(), action: `${step.action === "do" ? step.do : step.action}${target ? ` · ${target}` : ""}${paramText ? ` · ${paramText.length > 40 ? paramText.slice(0, 39) + "…" : paramText}` : ""}${met ? ` · ${met}` : ""}`,
        state, items, part: step.part, selected: isOfStep(selected, phase, step.id),
        selectedSlot: isOfStep(selected, phase, step.id) ? (selected!.match(/\[(.*)\]$/)?.[1] ?? slotOf(items[0])) : null,
        onPickSlot: (slot: string) => onSelect(stepKey(phase, step.id, slot), step, phase),
        title: `${phase}:${step.id}\n${step.action}${step.do ? " " + step.do : ""}${paramText ? `\nparameters: ${paramText}` : ""}${step.if ? `\nif ${step.if}` : ""}\nneeds: ${step.needs.join(", ") || "-"}\n${items.map((i: any) => `${slotOf(i) || "-"}: ${i.state}${i.reason ? " (" + i.reason + ")" : ""}`).join("\n")}`,
      },
    };
  });

  const focusNode = focus ? `phase:${focus.slice(0, focus.indexOf(":"))}/${focus.slice(focus.indexOf(":") + 1)}` : null;
  useEffect(() => {
    const f = flow.current;
    if (!f || !focusNode || !nodes.some((n) => n.id === focusNode)) return;
    const n = f.getInternalNode(focusNode);
    if (!n?.measured.width) return;
    const { x, y } = n.internals.positionAbsolute;
    f.setCenter(x + n.measured.width / 2, y + (n.measured.height || 0) / 2, { zoom: f.getZoom(), duration: 400 });
  }, [focusNode, nodes]);

  return (
    <div className="graph-box">
      <ReactFlow nodes={live} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} fitView minZoom={0.2} onInit={(f) => { flow.current = f as any; }}
        // the sizes React Flow measures are kept in the nodes: a node given again without them is hidden until
        // measured again, which does not happen when its size did not change (the graph went blank on an update)
        onNodesChange={(changes) => setNodes((ns) => applyNodeChanges(changes.filter((c) => c.type === "dimensions"), ns))} nodesConnectable={false} proOptions={{ hideAttribution: true }}
        onNodeClick={(_, n) => {
          if (n.type !== "step") return;
          const { step, phase } = n.data as any;
          const items = byStep[`${phase}:${step.id}`] || [];
          onSelect(stepKey(phase, step.id, slotOf(items[0])), step, phase);
        }}>
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

/** The lines of a log rendered at a time: a big log renders slowly, so it is shown by windows of lines, the next
 *  ones rendered when scrolling reaches the bottom (or the top) of those shown. */
const LOG_LINES_SHOWN = 2000;

export function LogTail({ runId, step, live }: { runId: string; step: string; live: boolean }) {
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState(false);          // the first answer came: an empty log is then "no output yet"
  const [error, setError] = useState<string | null>(null);
  const offset = useRef(0);
  const box = useRef<HTMLPreElement>(null);
  // follow: a live log shows its end as it grows; an ended one opens at its start, to read down
  const [follow, setFollow] = useState(live);
  const [win, setWin] = useState<{ start: number; end: number } | null>(null);   // the lines shown
  const keepFromBottom = useRef<number | null>(null);   // lines added above: keep what was in view where it was
  useEffect(() => {
    setText("");
    setLoaded(false);
    setError(null);
    setWin(null);
    offset.current = 0;
    let stop = false;
    let busy = false;                                     // one request at a time: a big log takes more than a tick
    const tick = async () => {
      if (busy) return;
      busy = true;
      try {
        const { text: chunk, size } = await getText(`runs/${runId}/logs/${encodeURIComponent(step)}?offset=${offset.current}`);
        if (stop) return;
        if (chunk) setText((t) => t + chunk);
        offset.current = size ?? offset.current + chunk.length;
        setLoaded(true);
      } catch (e: any) {
        if (!stop) setError(e.message);
      } finally {
        busy = false;
      }
    };
    tick();
    const timer = live ? window.setInterval(tick, 1000) : undefined;
    return () => {
      stop = true;
      if (timer) window.clearInterval(timer);
    };
  }, [runId, step, live]);
  const lines = useMemo(() => {
    const all = text.split("\n");
    if (all.length && all[all.length - 1] === "") all.pop();
    return all;
  }, [text]);
  const total = lines.length;
  // the window: at the end when following, else from where it was (at first, the start)
  const shownWin = follow
    ? { start: Math.min(win?.start ?? Infinity, Math.max(0, total - LOG_LINES_SHOWN)), end: total }
    : win ?? { start: 0, end: Math.min(total, LOG_LINES_SHOWN) };
  const { start, end } = shownWin;
  const shown = useMemo(() => <ConsoleText text={lines.slice(start, end).join("\n")} />, [lines, start, end]);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    if (follow) el.scrollTop = el.scrollHeight;
    else if (keepFromBottom.current !== null) {
      el.scrollTop = el.scrollHeight - keepFromBottom.current;
      keepFromBottom.current = null;
    }
  }, [shown, follow]);
  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
    const atTop = el.scrollTop < 40;
    if (follow && !atBottom) {                            // the user scrolled up: stop following, keep the lines in view
      setFollow(false);
      if (atTop && start > 0) {
        keepFromBottom.current = el.scrollHeight - el.scrollTop;
        setWin({ start: Math.max(0, start - LOG_LINES_SHOWN), end });
      } else setWin(shownWin);
      return;
    }
    if (!follow && atBottom && end < total) setWin({ start, end: Math.min(total, end + LOG_LINES_SHOWN) });
    else if (!follow && atTop && start > 0) {
      keepFromBottom.current = el.scrollHeight - el.scrollTop;
      setWin({ start: Math.max(0, start - LOG_LINES_SHOWN), end });
    }
  };
  const followNow = (on: boolean) => {
    setFollow(on);
    setWin(on ? null : shownWin);
  };
  return (
    <div className="log-tail">
      <div className="file-view-head">
        <code className="log-step" title={step}>{step}</code>
        {loaded && offset.current > 0 && <span className="muted small" title={`The size of the log: ${total.toLocaleString()} lines`}>{logSize(offset.current)}</span>}
        <label className="toggle" title="Show the end of the log, as it grows"><input type="checkbox" checked={follow} onChange={(e) => followNow(e.target.checked)} /> follow</label>
        <a className="button small" href={url(`runs/${runId}/logs/${encodeURIComponent(step)}`)} target="_blank" rel="noreferrer">raw</a>
      </div>
      {error && <div className="error-box">{error}</div>}
      <pre className="log" ref={box} onScroll={onScroll}>
        {start > 0 && <div className="log-more">{start.toLocaleString()} lines above: scroll up to show them</div>}
        {text ? shown : error ? "" : loaded ? "(no output yet)" : <Spinner text="Loading the log…" />}
        {end < total && <div className="log-more">{(total - end).toLocaleString()} lines below: scroll down to show them</div>}
      </pre>
    </div>
  );
}

/** A size in bytes, for a person. */
function logSize(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} kB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** In: "in 42 s", "in 2 min", or "now". */
function inTime(iso?: string | null): string {
  if (!iso) return "";
  const s = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  return s <= 0 ? "now" : s < 90 ? `in ${s} s` : `in ${Math.round(s / 60)} min`;
}

const clock = (iso?: string | null) => (iso ? new Date(iso).toLocaleTimeString() : "");

/** Why the run, or its next phase, has not started: since when, what is missing, when it is checked again, and
 * a button to check now (README section 9). */
function WaitBanner({ run, onRetry }: { run: any; onRetry: () => void }) {
  const w = run.wait;
  useTick(true);
  const [busy, setBusy] = useState(false);
  const retry = async () => {
    setBusy(true);
    await attempt(() => post(`runs/${run.id}/retry`), "Checking again now");
    setBusy(false);
    window.setTimeout(onRetry, 600);
  };
  const what = w.phase ? <>Phase <strong>{w.phase}</strong> could not start yet</> : <>The run has not started yet</>;
  const tries = w.max_attempts && w.attempts ? ` (failed ${w.attempts} of ${w.max_attempts} allowed times: the run ends in error at the ${w.max_attempts}${w.max_attempts === 2 ? "nd" : w.max_attempts === 3 ? "rd" : "th"})` : "";
  let when: ReactNode;
  if (w.cause === "selftest_running") when = <><Spinner /> Self-testing what it needs before it takes it…</>;
  else if (w.cause === "selftest" && w.every >= 30) when = <>A self-test failed{tries}. What failed is given back and tried again at {clock(w.next_check)} ({inTime(w.next_check)}), or now with Retry.</>;
  else if (w.cause === "selftest") when = <>A self-test failed{tries}. What failed is out of service until a self-test passes: by itself when it turns idle again (at most once a minute), or now with Retry, which tests it again. Other resources are checked every {w.every} s.</>;
  else if (w.cause === "requeued") when = <>The self-test failed{tries}: the run is back at the head of the queue, and is checked on any lab change or every {w.every} s (next {inTime(w.next_check)}).</>;
  else when = <>Checked again every {w.every} s, and at once when the lab changes: it starts as soon as what it needs is free.</>;
  return (
    <div className="banner info wait-banner">
      <div className="wait-head">
        <span>{what}{w.since && <span className="muted"> · waiting since {clock(w.since)} (<Elapsed from={w.since} />)</span>}</span>
        <span className="spacer" />
        {w.cause !== "selftest_running" && <button className="small" disabled={busy} onClick={retry} title="Check now whether it can start, instead of at the next check">{busy ? "Checking…" : "Retry now"}</button>}
      </div>
      {w.reasons?.length > 0 && <ul className="wait-reasons">{w.reasons.map((x: string) => <li key={x}>{x}</li>)}</ul>}
      <div className="small">{when}{w.checked_at && <span className="muted"> Last check {clock(w.checked_at)}.</span>}</div>
    </div>
  );
}

export function RunPage({ id, tab, editor, file, line }: { id: string; tab: string; editor?: boolean; file?: string | null; line?: number | null }) {
  const run = useResource<any>(`runs/${id}`, ["run", "phase", "step", "manual_operation", "pause", "intervention", "review"], (d) => !d.run || String(d.run).startsWith(id));
  const pipeline = useResource<any>(`runs/${id}/pipeline`, []);
  const labelOf = useLabels();
  const [selected, setSelected] = useState<string | null>(null);
  // the step running now is followed (selected, its log shown, brought into view) until another step is picked by
  // hand; picking a step that is running (or the button) follows again, to the next step when it starts
  const [pinned, setPinned] = useState(false);
  const r = run.data;
  const records: any[] = r?.steps || [];
  const live = r && (ACTIVE.includes(r.state) || ["queued", "waiting_resources"].includes(r.state));
  const running = records.filter((s) => s.state === "running").sort((a, b) => String(b.started || "").localeCompare(String(a.started || "")))[0];
  const runningKey = running ? stepKey(running.phase, running.id, slotOf(running)) : null;
  // whether a key ('phase:id' for the whole step, or 'phase:id[slot]') is of a step running now
  const isRunningKey = (key: string | null) => !!key && records.some((s) => s.state === "running"
    && (stepKey(s.phase, s.id, slotOf(s)) === key || key === `${s.phase}:${s.id}`));
  const pick = (key: string | null) => {
    setSelected(key);
    setPinned(!(live && isRunningKey(key)));
  };
  const followRunning = () => {
    if (runningKey) setSelected(runningKey);
    setPinned(false);
  };
  // a step that starts is selected when following (not on a resume: the running step picked stays selected)
  useEffect(() => {
    if (!pinned && runningKey && runningKey !== selected) setSelected(runningKey);
  }, [runningKey]);
  // the finally steps of each phase ('phase:step'), marked as such in the list of steps
  const finals = useMemo(() => new Set<string>((pipeline.data?.phases || []).flatMap((ph: any) => ph.steps.filter((s: any) => s.part === "finally").map((s: any) => `${ph.id}:${s.id}`))), [pipeline.data]);
  const recordFor = (key: string) => records.find((s) => stepKey(s.phase, s.id, slotOf(s)) === key);
  // the log to show: the selected record, or the first slot of a step run on several; none before it starts
  const logRecord = selected ? recordFor(selected) || records.find((s) => selected === `${s.phase}:${s.id}`) : null;
  const logKey = logRecord ? stepKey(logRecord.phase, logRecord.id, slotOf(logRecord)) : null;
  if (run.error) return <div className="page"><div className="error-box">{run.error}</div></div>;
  if (!r) return <div className="page muted">Loading…</div>;
  const tabs: [string, string][] = [["pipeline", "Pipeline"], ["steps", `Steps (${records.length})`], ["files", "Files"], ["resources", "Resources"]];
  if (r.results) tabs.push(["results", "Results"]);
  tabs.push(["requirements", "Requirements"]);
  const ended = ["done", "cancelled"].includes(r.state);
  if (ended) tabs.push(["review", r.review?.state === "open" ? "Review (open)" : "Review"]);
  // the steps and checks as reviewed (SPEC §11.1): the run's own records stay as they ran
  const reviewedState = (s: any) => r.review?.steps?.find((x: any) => x.step === stepKey(s.phase, s.id, slotOf(s)));
  return (
    <div className="page run-page">
      <div className="page-head">
        <h2>{r.testdef}</h2>
        <RunStateBadge run={r} />
        <ReviewedVerdict verdict={r.review?.verdict ?? r.verdict} raw={r.review?.raw_verdict} />
        <ReviewStateBadge review={r.review} />
        <span className="spacer" />
        {r.state === "queued" || r.state === "waiting_resources" ? <>priority <PriorityInput run={r} onDone={run.refresh} /></> : null}
        {live && <StepModeControl run={r} onChanged={run.refresh} />}
        {live && <button onClick={() => abort(r, "cancel")}>Cancel</button>}
        {live && <button className="danger" onClick={() => abort(r, "kill")}>Kill</button>}
        {["done", "cancelled"].includes(r.state) && r.campaign_run && <button title="Run its datapack again in its campaign, unchanged: a new run, this one kept as an earlier attempt"
          onClick={() => rerun(r).then((n: any) => { if (n?.id) window.location.hash = `#/runs/${n.id}`; })}>Run again</button>}
        {["done", "cancelled"].includes(r.state) && !r.campaign_run && <button title="Clean its outputs and put the datapack back in the queue" onClick={() => repost(r).then(() => { run.refresh(); pipeline.refresh(); })}>Re-post</button>}
        {r.results && <a className="button" href={url(`runs/${r.id}/files/results.zip?results=true`)}>Results zip</a>}
        {ended && <ExportButton kind="run" id={r.id} />}
      </div>
      <div className="run-facts">
        <span>run <code>{r.id}</code></span>
        <span title={r.name}>datapack <code>{r.name}</code></span>
        <span title={time(r.submitted_at)}>submitted {ago(r.submitted_at)}</span>
        {r.activated_at && <span>{ENDED.includes(r.verdict) || r.ended_at ? "ran" : "running for"} <Elapsed from={r.activated_at} to={r.ended_at} /></span>}
        {r.phase && <span>phase <strong>{r.phase}</strong></span>}
        {r.binding && <span className="run-binding">on {Object.entries(r.binding.stage_slots || r.binding.realms || r.binding.stages || {}).map(([l, c]) => <span key={"s" + l}><Named kind="stage_slot" name={l} />=<Named kind="stage" name={c as string} /></span>)}
          {Object.entries(r.binding.slots || r.binding.components || {}).map(([l, c]) => <span key={l}><Named kind="slot" name={l} />=<Named kind="component" name={c as string}>{labelOf(c as string)}</Named></span>)}</span>}
        <Badge value={r.conform ? "conform" : "non-conform"} />
      </div>
      {r.campaign_run && <div className="muted small">part of campaign run <a href={`#/campaigns/${r.campaign_run}`}>{r.campaign_run}</a>
        {r.rerun_of && <> · runs again the datapack of run <a href={`#/runs/${r.rerun_of}`}>{r.rerun_of}</a></>}</div>}
      <ManualOperations runId={r.id} records={records} onAnswered={run.refresh} />
      <PauseBanners run={r} onChanged={run.refresh} />
      {r.reason && <div className="banner warn">{r.reason}</div>}
      {r.wait ? <WaitBanner run={r} onRetry={run.refresh} /> : r.waiting_for && <div className="banner info">Waiting for: {r.waiting_for}</div>}
      {(r.nonconform || []).length > 0 && <details className="small"><summary>Why it is non-conform</summary><ul>{r.nonconform.map((n: string) => <li key={n}>{n}</li>)}</ul></details>}
      {(r.warnings || []).length > 0 && <ul className="warn-list">{r.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>}
      <Interventions items={r.interventions} />
      <div className="tabs">
        {tabs.map(([key, label]) => <button key={key} className={tab === key ? "selected" : ""} onClick={() => setQuery("tab", key)}>{label}</button>)}
      </div>
      {tab === "pipeline" && (
        <div className="split run-split">
          <div className="split-main">
            {pipeline.data ? <RunGraph pipeline={pipeline.data} records={records} selected={selected} onSelect={(k) => pick(k)}
              focus={!pinned && running ? `${running.phase}:${running.id}` : null} />
              : <p className="muted">{pipeline.error || "Loading the pipeline…"}</p>}
          </div>
          <div className="split-side">
            {selected ? (
              <>
                {live && pinned && <button className="small" onClick={followRunning} title="Select the step running now, and keep following the steps as they run (clicking a running step does it too)">Follow the running step</button>}
                {live && !pinned && runningKey && <p className="muted small">Following the running step: click another to stop, a running one to follow again.</p>}
                <StepActions run={r} record={logRecord} part={finals.has(`${logRecord?.phase}:${logRecord?.id}`) ? "finally" : undefined} onChanged={run.refresh} />
                <StepFacts record={logRecord} records={records.filter((s) => isOfStep(selected, s.phase, s.id))} />
                {editor && logRecord && <EditLabCommand record={logRecord} />}
                <ExploitChecks runId={r.id} record={logRecord} run={r} onReviewed={run.refresh} />
                {ended && logRecord && <StepReview run={r} record={logRecord} onDone={run.refresh} />}
                {logKey && <LogTail key={logKey} runId={r.id} step={logKey} live={!!live && !ENDED.includes(logRecord?.state)} />}
              </>
            ) : <p className="muted">Click a step to see its log. Colors: green succeeded, blue pulsing running, red failed or error, grey pending or skipped; the finally steps, in their dashed lane after the steps, always run at the end, cancel and kill included.</p>}
          </div>
        </div>
      )}
      {tab === "steps" && (
        <div className="split run-split">
          <div className="split-main scroll">
            <table className="grid compact">
              <thead><tr><th>Step</th><th>State</th><th>Started</th><th>Duration</th><th>Exit</th><th>Tries</th><th>Reason</th></tr></thead>
              <tbody>
                {records.map((s) => {
                  const key = stepKey(s.phase, s.id, slotOf(s));
                  return (
                    <tr key={key} className={classes("clickable", selected === key && "selected")} onClick={() => pick(key)}>
                      <td><code>{key}</code>{finals.has(`${s.phase}:${s.id}`) && <> <span className="chip finally-tag" title="a finally step: it runs at the end of its phase whatever happened">finally</span></>}{s.via && <> <ViaTag via={s.via} title={`its command ran on ${s.via}, which reaches the component of slot ${slotOf(s)}`} /></>}</td><td><ReviewedVerdict verdict={reviewedState(s)?.state ?? s.state} raw={reviewedState(s)?.raw_state} /></td><td>{s.started ? time(s.started) : ""}</td>
                      <td>{s.started ? <Elapsed from={s.started} to={s.ended || (live ? null : r.ended_at)} /> : ""}</td><td>{s.exit_status ?? ""}</td><td>{s.attempts || ""}</td>
                      <td className="reason">{s.reason}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {records.length === 0 && <p className="muted">No step has started yet.</p>}
          </div>
          <div className="split-side">
            {ended && logRecord && <StepReview run={r} record={logRecord} onDone={run.refresh} />}
            {editor && logRecord && <EditLabCommand record={logRecord} />}
            {logKey && <LogTail key={logKey} runId={r.id} step={logKey} live={!!live && !ENDED.includes(logRecord?.state)} />}
          </div>
        </div>
      )}
      {tab === "files" && <FileBrowser base={`runs/${r.id}/files`} plot={editor ? `plot/runs/${r.id}` : undefined}
        initial={file || undefined} line={line} />}
      {tab === "resources" && <ResourcesTab runId={r.id} ended={!live} />}
      {tab === "requirements" && <RunsCoverage path={`runs/${r.id}/requirements`} events={["run", "step"]} title="The requirements this test and its checks verify, by this run" />}
      {tab === "review" && ended && <RunReviewTab run={r} onChanged={run.refresh} />}
      {tab === "results" && <FileBrowser base={`runs/${r.id}/files`} query="results=true&list=true" plot={editor ? `plot/runs/${r.id}` : undefined} />}
    </div>
  );
}

/** Where a lab command is defined, as the editor opens it: the file, its item, and the command to open there. The
 *  sources are those of the lab's resolution ("class M", "host H", "stage S"); a command its component lacks, or
 *  defined in a file this lab session cannot edit, opens its component in the inventory. */
function commandPlace(ctx: any, lc: { name: string; component: string; source: string }) {
  const [kind, ...rest] = String(lc.source || "").split(" ");
  const name = rest.join(" ");
  if (kind === "class" && ctx?.lab?.library?.[name]?.source)
    return { file: ctx.lab.library[name].source, item: `component:${name}`, command: lc.name };
  if (kind === "stage" && ctx?.inventory) return { file: ctx.inventory, item: `stage:${name}`, command: lc.name };
  if (kind === "host" && ctx?.inventory) return { file: ctx.inventory, item: `host:${name}`, command: lc.name };
  return ctx?.inventory ? { file: ctx.inventory, item: `host:${lc.component}`, command: lc.name } : {};
}

/** The lab command a failed 'do:' step ran, and a button opening the lab editor (in a new tab) on it, where it is
 *  defined: its class, its stage's or its own overrides in the inventory, or a host it extends. */
function EditLabCommand({ record }: { record: any }) {
  const lc = record?.lab_command;
  if (!lc || !["failed", "error"].includes(record.state)) return null;
  const where = lc.source ? `defined in ${lc.source}` : `not defined for ${lc.component}`;
  const edit = () => openInNewTab((async () => {
    const s = await attempt(() => post("edit/sessions", { repo: "lab", local: true }));
    if (!s) return null;
    const ctx = await get<any>(`edit/sessions/${s.id}/context`).catch(() => null);
    return { path: `edit/${s.id}`, query: commandPlace(ctx, lc) };
  })());
  return (
    <div className="edit-lab-command small">
      <span>lab command <code>{lc.name}</code> of <code>{lc.component}</code> <span className="muted">({where})</span></span>
      <button className="small" onClick={edit}
        title={`Open the lab config in the Editor (in place), in a new browser tab, on the command '${lc.name}' ${lc.source ? `of ${lc.source}` : `(to implement it on ${lc.component})`}`}>
        Edit the command
      </button>
    </div>
  );
}

const checkMet = (c: any) => ["SUCCESS", "ACCEPTED"].includes(String(c.status || "").toUpperCase());

/** A check's key in a review (SPEC §11.1): kind, step, criteria, scenario, check. */
const checkKey = (step: string, c: any) => `check|${step}|${c.criteria || ""}|${c.scenario || ""}|${c.check || ""}`;

/** An exploit step's checks (the criteria it ran on the run's outputs): each one's status as reviewed (accept,
 *  reject, comment once the run ended), and its report (as reviewed). */
function ExploitChecks({ runId, record, run, onReviewed }: { runId: string; record: any; run?: any; onReviewed?: () => void }) {
  const x = record?.exploit;
  if (!x) return null;
  const step = stepKey(record.phase, record.id, slotOf(record));
  const review = run?.review;
  const ended = run && ["done", "cancelled"].includes(run.state);
  // each check as reviewed: its status, and the raw one when a review changed it
  const checks: any[] = (x.checks || []).map((c: any) => {
    const r = review?.checks?.find((y: any) => checkKey(y.step, y) === checkKey(step, c));
    return r?.raw_status ? { ...c, status: r.status, raw_status: r.raw_status } : c;
  });
  const bad = checks.filter((c) => !checkMet(c));
  return (
    <div className="exploit-checks small">
      <div>
        <strong>{checks.length - bad.length} of {checks.length}</strong> check(s) met{review?.checks?.some((c: any) => c.raw_status) ? " (as reviewed)" : ""}
        {x.report && <> · <a href={url(`runs/${runId}/exploit/${encodeURIComponent(`${record.phase}:${record.id}`)}/report`)} target="_blank" rel="noreferrer">open the report</a></>}
        {x.folder && <span className="muted"> · results in <code>{x.folder}</code></span>}
      </div>
      {checks.length > 0 && (
        <table className="grid compact">
          <thead><tr><th>Criteria</th><th>Check</th><th>Status</th></tr></thead>
          <tbody>
            {[...bad, ...checks.filter(checkMet)].map((c, i) => (
              <tr key={i}>
                <td><code>{c.criteria}</code>{c.scenario && !String(c.criteria).endsWith(c.scenario) && <span className="muted"> · {c.scenario}</span>}</td>
                <td>{c.check || <span className="muted">the whole scenario</span>}
                  <ReviewNotes notes={review?.targets?.[checkKey(step, c)]} />
                  {ended && <ReviewButtons runId={runId} review={review} raw={c.raw_status || c.status} current={c.status}
                    target={{ kind: "check", step, criteria: c.criteria || "", scenario: c.scenario || "", check: c.check || "" }}
                    label={`check ${c.check || c.scenario}`} onDone={() => onReviewed?.()} />}</td>
                <td><span className={classes("badge", checkMet(c) ? "ok" : "bad")}>{String(c.status).toLowerCase()}</span>
                  {c.raw_status && <div className="muted" title="as the run gave it">raw {String(c.raw_status).toLowerCase()} <span className="reviewed-mark">✎</span></div>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** A step of an ended run: its state as reviewed, its review notes, accept, reject or comment on it. */
function StepReview({ run, record, onDone }: { run: any; record: any; onDone: () => void }) {
  const key = stepKey(record.phase, record.id, slotOf(record));
  const reviewed = run.review?.steps?.find((x: any) => x.step === key);
  if (!["succeeded", "failed", "error"].includes(record.state)) return null;
  return (
    <div className="step-review small">
      {reviewed?.raw_state && <div>As reviewed: <ReviewedVerdict verdict={reviewed.state} raw={reviewed.raw_state} />{reviewed.reviewed === "checks" && <span className="muted"> (by its checks)</span>}</div>}
      <ReviewNotes notes={run.review?.targets?.[`step|${key}`]} />
      <ReviewButtons runId={run.id} review={run.review} raw={record.state} current={reviewed?.state ?? record.state}
        target={{ kind: "step", step: key }} label={`step ${key}`} onDone={onDone} />
    </div>
  );
}

function StepFacts({ record, records }: { record: any; records: any[] }) {
  if (!record && !records.length) return <p className="muted">This step has not started.</p>;
  return (
    <div className="step-facts">
      {records.length > 1 && record && (
        <div className="small">
          slot <code>{slotOf(record)}</code> <span className="muted">· {records.length} slots: click a slot name on the step to see its log</span>
        </div>
      )}
      {record && (
        <div className="small">
          <Badge value={record.state} /> {record.reason}
          {record.exit_status !== null && record.exit_status !== undefined && <> · exit {record.exit_status}</>}
          {record.attempts > 1 && <> · {record.attempts} tries</>}
          {record.started && <> · <Elapsed from={record.started} to={record.ended || null} /></>}
          {record.state === "running" && record.progress && <UploadBar progress={record.progress} />}
          {record.via && (
            <div className="step-via-note">
              <ViaTag via={record.via} title={`its command ran on ${record.via}, not on the component of its slot`}>run on</ViaTag>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── resources: what the datapack expects of the lab against what the lab has ────────────

function needStatus(need: any): [string, string] {
  if (need.bound) return ["bound", "ok"];
  if (need.problem || !need.candidates.length) return ["missing in lab", "bad"];
  if (need.candidates.some((c: any) => !c.why)) return ["available", "ok"];
  return ["unavailable", "bad"];
}

function CandidateState({ c }: { c: any }) {
  if (c.bound) return <span className="chip ok">bound</span>;
  if (!c.why) return <span className="chip ok">available</span>;
  return <span className="chip bad">unavailable</span>;
}

function CompChip({ c, logical }: { c: any; logical?: string }) {
  const labelOf = useLabels();
  const notes = [c.role && `role ${c.role}`, c.extends && `extends ${c.extends}`, `lock ${c.lock}`, c.maintenance && "in maintenance", c.held_by?.length && `held by ${c.held_by.join(", ")}`];
  const color = c.maintenance || ["broken", "offline"].includes(c.state) ? "bad" : c.held_by?.length ? "wait" : STATE_COLORS[c.state] || "muted";
  return (
    <span className={classes("chip", color)} title={notes.filter(Boolean).join(" · ")}>
      {labelOf(c.id)}{logical && <> = {logical}</>} · {c.state}
    </span>
  );
}

function ResourcesTab({ runId, ended }: { runId: string; ended: boolean }) {
  const labelOf = useLabels();
  const res = useResource<any>(`runs/${runId}/resources`, ["run", "component", "resource", "lab"]);
  const d = res.data;
  if (res.error) return <div className="error-box">{res.error}</div>;
  if (!d) return <p className="muted">Loading…</p>;
  const needs = [...d.stage_slots, ...d.slots];
  const ok = needs.filter((n) => needStatus(n)[1] === "ok").length;
  return (
    <div className="scroll resources-tab">
      <div className={classes("banner", ok === needs.length ? "" : "warn")}>
        <strong>{ok} of {needs.length}</strong> needed resources {d.state === "running" || d.state === "reserved" ? "bound or available" : "available now"}.
        {d.waiting_for && <span>Waiting for: {d.waiting_for}</span>}
        {d.reserve === "all" ? <span className="muted">Everything is reserved when the run starts.</span>
          : <span className="muted">Each phase reserves what it needs when it starts.</span>}
        {ended && <span className="muted">The run has ended: availability is the lab's as it is now.</span>}
      </div>
      {d.problems.length > 0 && (
        <div className="banner warn list">
          <strong>The lab cannot provide:</strong>
          <ul className="problems">{d.problems.map((p: string) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
      {d.stage_slots.length > 0 && <h3>Stage slots</h3>}
      {d.stage_slots.map((s: any) => {
        const [label, color] = needStatus(s);
        return (
          <div key={s.logical} className="need">
            <div className="need-head">
              <code><Named kind="stage_slot" name={s.logical} /></code> <span className="muted">stage slot, on a lab stage of type</span> <code><Named kind="stage_type" name={s.type} /></code>
              <span className={classes("chip", color)}>{label}{s.bound && <> on {s.bound}</>}</span>
              <span className="spacer" />
              <span className="muted small">{s.phases.length ? `phases ${s.phases.join(", ")}` : "used by no phase"}</span>
            </div>
            {s.slots.length > 0 && (
              <div className="small muted">slots {s.slots.map((c: any, i: number) => <span key={c.logical}>{i > 0 && ", "}<Named kind="slot" name={c.logical} /> (<Named kind="role" name={c.role} />, {c.slot_class ? <Named kind="slot_class" name={c.slot_class} /> : "any class"})</span>)}</div>
            )}
            {s.problem ? <div className="small bad-text">{s.problem}</div> : (
              <table className="grid compact">
                <thead><tr><th>Lab stage</th><th>State</th><th>Why not</th><th>Components</th></tr></thead>
                <tbody>
                  {s.candidates.map((c: any) => {
                    const logicalOf = Object.fromEntries(Object.entries(c.roles).map(([l, id]) => [id, l]));
                    return (
                      <tr key={c.id}>
                        <td><code>{c.id}</code></td>
                        <td><CandidateState c={c} /></td>
                        <td className="reason">{c.why || ""}</td>
                        <td>{c.components.map((k: any) => <CompChip key={k.id} c={k} logical={logicalOf[k.id] as string | undefined} />)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        );
      })}
      {d.slots.length > 0 && <h3>Ad hoc slots</h3>}
      {d.slots.map((n: any) => {
        const [label, color] = needStatus(n);
        return (
          <div key={n.logical} className="need">
            <div className="need-head">
              <code><Named kind="slot" name={n.logical} /></code> <span className="muted">role</span> <code><Named kind="role" name={n.role} /></code>
              {n.slot_class && <><span className="muted">class</span> <code><Named kind="slot_class" name={n.slot_class} /></code></>}
              <span className={classes("chip", color)}>{label}{n.bound && <> on {labelOf(n.bound)}</>}</span>
              <span className="spacer" />
              <span className="muted small">{n.phases.length ? `phases ${n.phases.join(", ")}` : "used by no phase"}</span>
            </div>
            {n.problem ? <div className="small bad-text">{n.problem}</div> : (
              <table className="grid compact">
                <thead><tr><th>Lab component</th><th>State</th><th>Why not</th><th>Component</th></tr></thead>
                <tbody>
                  {n.candidates.map((c: any) => (
                    <tr key={c.id}>
                      <td title={c.id}>{labelOf(c.id)}</td>
                      <td><CandidateState c={c} /></td>
                      <td className="reason">{c.why || ""}</td>
                      <td><CompChip c={c} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        );
      })}
      {needs.length === 0 && <p className="muted">This datapack needs no lab resource.</p>}
    </div>
  );
}
