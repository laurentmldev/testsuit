// One run (sequencer README, section 13.1): its pipeline as a live graph of phases, sub-pipelines and steps,
// the logs of each step, its files before, during and after the run.
import { useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import { getText, url, useLabels, useResource } from "../api";
import { FileBrowser } from "../files";
import { layout } from "../graph";
import { ago, Badge, classes, duration, setQuery, STATE_COLORS, time } from "../util";
import { abort, ACTIVE, PriorityInput, repost } from "./Queue";

const ENDED = ["succeeded", "failed", "error", "skipped"];

function worst(states: string[]): string {
  for (const s of ["error", "failed", "running", "ready", "pending", "skipped", "succeeded"]) if (states.includes(s)) return s;
  return states[0] || "pending";
}

export function stepKey(phase: string, id: string, slot?: string) {
  return `${phase}:${id}` + (slot ? `[${slot}]` : "");
}

/** The slot a step record ran for ('' for the whole step); runs recorded before slots were named say 'component'. */
export const slotOf = (record: any): string => record?.slot ?? record?.component ?? "";

function StepNode({ data }: NodeProps) {
  const d = data as any;
  return (
    <div className={classes("step-node", STATE_COLORS[d.state] || "muted", d.selected && "selected", d.part === "finally" && "finally")}
      title={d.title}>
      <Handle type="target" position={Position.Left} />
      <div className="step-id">{d.label}</div>
      <div className="step-action">{d.action}</div>
      {d.items?.length > 1 && (
        <div className="step-items">
          {d.items.map((i: any) => <span key={slotOf(i)} className={classes("chip", STATE_COLORS[i.state] || "muted")} title={`${slotOf(i)}: ${i.state}`}>{slotOf(i)}</span>)}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function FrameNode({ data }: NodeProps) {
  const d = data as any;
  return (
    <div className={classes("frame-node", d.kind, d.state && STATE_COLORS[d.state])}>
      <div className="frame-title">{d.label}{d.state && <Badge value={d.state} />}</div>
    </div>
  );
}

const nodeTypes = { step: StepNode, frame: FrameNode };

export function RunGraph({ pipeline, records, selected, onSelect }: {
  pipeline: any; records: any[]; selected: string | null; onSelect: (key: string, step: any, phase: string) => void;
}) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const structure = useMemo(() => JSON.stringify(pipeline), [pipeline]);
  const byStep = useMemo(() => {
    const map: Record<string, any[]> = {};
    for (const r of records) (map[`${r.phase}:${r.id}`] ||= []).push(r);
    return map;
  }, [records]);

  useEffect(() => {
    let cancelled = false;
    const lay: any[] = [];
    const edgeList: Edge[] = [];
    for (const phase of pipeline.phases) {
      const pid = `phase:${phase.id}`;
      lay.push({ id: pid, width: 200, height: 80 });
      for (const need of phase.needs) edgeList.push({ id: `${need}->${phase.id}`, source: `phase:${need}`, target: pid, className: "phase-edge" });
      const groups = new Set<string>();
      for (const s of phase.steps) {
        // nested sub-pipeline frames: 'up', then 'up.inner'...
        const parts = s.group ? s.group.split(".") : [];
        let parent = pid;
        for (let i = 1; i <= parts.length; i++) {
          const g = `${pid}/${parts.slice(0, i).join(".")}/${s.part}`;
          if (!groups.has(g)) {
            groups.add(g);
            lay.push({ id: g, width: 160, height: 60, parent });
          }
          parent = g;
        }
        lay.push({ id: `${pid}/${s.id}`, width: 150, height: 56, parent });
        for (const n of s.needs) edgeList.push({ id: `${phase.id}:${n}->${s.id}`, source: `${pid}/${n}`, target: `${pid}/${s.id}` });
      }
      // finally steps after every step of the phase
      const finals = phase.steps.filter((s: any) => s.part === "finally" && !s.needs.length);
      const lasts = phase.steps.filter((s: any) => s.part === "steps" && !phase.steps.some((o: any) => o.needs.includes(s.id)));
      for (const f of finals) for (const l of lasts) edgeList.push({ id: `${phase.id}:${l.id}~>${f.id}`, source: `${pid}/${l.id}`, target: `${pid}/${f.id}`, className: "finally-edge" });
    }
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
        if (isFrame) {
          const label = rest.length ? `${rest[0]} (sub-pipeline${rest[1] === "finally" ? ", finally" : ""})` : `phase ${phaseId}`;
          list.push({ ...base, type: "frame", data: { label, kind: rest.length ? "sub" : "phase", phase: phaseId }, style: { width: p.width, height: p.height }, selectable: false });
        } else {
          const step = pipeline.phases.find((ph: any) => ph.id === phaseId).steps.find((s: any) => s.id === rest.join("/"));
          list.push({ ...base, type: "step", data: { step, phase: phaseId }, style: { width: p.width } });
        }
      }
      setNodes(list);
      setEdges(edgeList.map((e) => ({
        ...e, markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
        style: e.className === "finally-edge" ? { strokeDasharray: "4 3", opacity: 0.5 } : e.className === "phase-edge" ? { strokeWidth: 2 } : undefined,
      })));
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure]);

  const live = nodes.map((n) => {
    if (n.type === "frame") {
      const phase = (n.data as any).phase;
      if ((n.data as any).kind !== "phase") return n;
      const states = records.filter((r) => r.phase === phase).map((r) => r.state);
      return { ...n, data: { ...n.data, state: states.length ? worst(states) : undefined } };
    }
    const { step, phase } = n.data as any;
    const items = byStep[`${phase}:${step.id}`] || [];
    const state = items.length ? worst(items.map((i) => i.state)) : "pending";
    const target = step.slot || (Array.isArray(step.for_each) ? step.for_each.join(", ") : step.for_each) || "";
    return {
      ...n, data: {
        ...n.data, label: step.id.split(".").pop(), action: `${step.action === "do" ? step.do : step.action}${target ? ` · ${target}` : ""}`,
        state, items, part: step.part, selected: selected?.startsWith(`${phase}:${step.id}`),
        title: `${phase}:${step.id}\n${step.action}${step.do ? " " + step.do : ""}${step.if ? `\nif ${step.if}` : ""}\nneeds: ${step.needs.join(", ") || "-"}\n${items.map((i: any) => `${slotOf(i) || "-"}: ${i.state}${i.reason ? " (" + i.reason + ")" : ""}`).join("\n")}`,
      },
    };
  });

  return (
    <div className="graph-box">
      <ReactFlow nodes={live} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.2} nodesConnectable={false} proOptions={{ hideAttribution: true }}
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

export function LogTail({ runId, step, live }: { runId: string; step: string; live: boolean }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const offset = useRef(0);
  const box = useRef<HTMLPreElement>(null);
  const [follow, setFollow] = useState(true);
  useEffect(() => {
    setText("");
    setError(null);
    offset.current = 0;
    let stop = false;
    const tick = async () => {
      try {
        const { text: chunk, size } = await getText(`runs/${runId}/logs/${encodeURIComponent(step)}?offset=${offset.current}`);
        if (stop) return;
        if (chunk) setText((t) => t + chunk);
        offset.current = size ?? offset.current + chunk.length;
      } catch (e: any) {
        if (!stop) setError(e.message);
      }
    };
    tick();
    const timer = live ? window.setInterval(tick, 1000) : undefined;
    return () => {
      stop = true;
      if (timer) window.clearInterval(timer);
    };
  }, [runId, step, live]);
  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [text, follow]);
  return (
    <div className="log-tail">
      <div className="file-view-head">
        <code>{step}</code>
        <span className="spacer" />
        <label className="toggle"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
        <a className="button small" href={url(`runs/${runId}/logs/${encodeURIComponent(step)}`)} target="_blank" rel="noreferrer">raw</a>
      </div>
      {error && <div className="error-box">{error}</div>}
      <pre className="log" ref={box}>{text || (error ? "" : "(no output yet)")}</pre>
    </div>
  );
}

export function RunPage({ id, tab }: { id: string; tab: string }) {
  const run = useResource<any>(`runs/${id}`, ["run", "phase", "step"], (d) => !d.run || String(d.run).startsWith(id));
  const pipeline = useResource<any>(`runs/${id}/pipeline`, []);
  const labelOf = useLabels();
  const [selected, setSelected] = useState<string | null>(null);
  const r = run.data;
  const records: any[] = r?.steps || [];
  const live = r && (ACTIVE.includes(r.state) || ["queued", "waiting_resources"].includes(r.state));
  const recordFor = (key: string) => records.find((s) => stepKey(s.phase, s.id, slotOf(s)) === key);
  // the log to show: the selected record, or the first slot of a step run on several; none before it starts
  const logRecord = selected ? recordFor(selected) || records.find((s) => selected === `${s.phase}:${s.id}`) : null;
  const logKey = logRecord ? stepKey(logRecord.phase, logRecord.id, slotOf(logRecord)) : null;
  if (run.error) return <div className="page"><div className="error-box">{run.error}</div></div>;
  if (!r) return <div className="page muted">Loading…</div>;
  const tabs: [string, string][] = [["pipeline", "Pipeline"], ["steps", `Steps (${records.length})`], ["files", "Files"], ["resources", "Resources"]];
  if (r.results) tabs.push(["results", "Results"]);
  return (
    <div className="page run-page">
      <div className="page-head">
        <h2>{r.testdef}</h2>
        <Badge value={r.state} />
        <Badge value={r.verdict} />
        <span className="spacer" />
        {r.state === "queued" || r.state === "waiting_resources" ? <>priority <PriorityInput run={r} onDone={run.refresh} /></> : null}
        {live && <button onClick={() => abort(r, "cancel")}>Cancel</button>}
        {live && <button className="danger" onClick={() => abort(r, "kill")}>Kill</button>}
        {["done", "cancelled"].includes(r.state) && <button title="Clean its outputs and put the datapack back in the queue" onClick={() => repost(r).then(() => { run.refresh(); pipeline.refresh(); })}>Re-post</button>}
        {r.results && <a className="button" href={url(`runs/${r.id}/files/results.zip?results=true`)}>Results zip</a>}
      </div>
      <div className="run-facts">
        <span>run <code>{r.id}</code></span>
        <span title={r.name}>datapack <code>{r.name}</code></span>
        <span title={time(r.submitted_at)}>submitted {ago(r.submitted_at)}</span>
        {r.activated_at && <span>{ENDED.includes(r.verdict) || r.ended_at ? "ran" : "running for"} {duration(r.activated_at, r.ended_at)}</span>}
        {r.phase && <span>phase <strong>{r.phase}</strong></span>}
        {r.binding && <span title="stage slot = lab stage, slot = lab component">on {Object.entries(r.binding.stage_slots || r.binding.realms || r.binding.stages || {}).map(([l, c]) => `${l}=${c}`).join(", ")} {Object.entries(r.binding.slots || r.binding.components || {}).map(([l, c]) => `${l}=${labelOf(c as string)}`).join(", ")}</span>}
        <Badge value={r.conform ? "conform" : "non-conform"} />
      </div>
      {r.reason && <div className="banner warn">{r.reason}</div>}
      {r.waiting_for && <div className="banner info">Waiting for: {r.waiting_for}</div>}
      {(r.nonconform || []).length > 0 && <details className="small"><summary>Why it is non-conform</summary><ul>{r.nonconform.map((n: string) => <li key={n}>{n}</li>)}</ul></details>}
      {(r.warnings || []).length > 0 && <ul className="warn-list">{r.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>}
      <div className="tabs">
        {tabs.map(([key, label]) => <button key={key} className={tab === key ? "selected" : ""} onClick={() => setQuery("tab", key)}>{label}</button>)}
      </div>
      {tab === "pipeline" && (
        <div className="split">
          <div className="split-main">
            {pipeline.data ? <RunGraph pipeline={pipeline.data} records={records} selected={selected} onSelect={(k) => setSelected(k)} />
              : <p className="muted">{pipeline.error || "Loading the pipeline…"}</p>}
          </div>
          <div className="split-side">
            {selected ? (
              <>
                <StepFacts record={logRecord} records={records.filter((s) => selected.startsWith(`${s.phase}:${s.id}`))} onPick={setSelected} />
                {logKey && <LogTail key={logKey} runId={r.id} step={logKey} live={!!live && !ENDED.includes(logRecord?.state)} />}
              </>
            ) : <p className="muted">Click a step to see its log. Colors: green succeeded, blue running, red failed or error, grey pending or skipped; dashed arrows lead to the finally steps.</p>}
          </div>
        </div>
      )}
      {tab === "steps" && (
        <div className="split">
          <div className="split-main scroll">
            <table className="grid compact">
              <thead><tr><th>Step</th><th>State</th><th>Started</th><th>Duration</th><th>Exit</th><th>Tries</th><th>Reason</th></tr></thead>
              <tbody>
                {records.map((s) => {
                  const key = stepKey(s.phase, s.id, slotOf(s));
                  return (
                    <tr key={key} className={classes("clickable", selected === key && "selected")} onClick={() => setSelected(key)}>
                      <td><code>{key}</code></td><td><Badge value={s.state} /></td><td>{s.started ? time(s.started) : ""}</td>
                      <td>{s.started ? duration(s.started, s.ended || null) : ""}</td><td>{s.exit_status ?? ""}</td><td>{s.attempts || ""}</td>
                      <td className="reason">{s.reason}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {records.length === 0 && <p className="muted">No step has started yet.</p>}
          </div>
          <div className="split-side">
            {logKey && <LogTail key={logKey} runId={r.id} step={logKey} live={!!live && !ENDED.includes(logRecord?.state)} />}
          </div>
        </div>
      )}
      {tab === "files" && <FileBrowser base={`runs/${r.id}/files`} />}
      {tab === "resources" && <ResourcesTab runId={r.id} ended={!live} />}
      {tab === "results" && <FileBrowser base={`runs/${r.id}/files`} query="results=true&list=true" />}
    </div>
  );
}

function StepFacts({ record, records, onPick }: { record: any; records: any[]; onPick: (k: string) => void }) {
  if (!record && !records.length) return <p className="muted">This step has not started.</p>;
  return (
    <div className="step-facts">
      {records.length > 1 && (
        <div className="segmented">
          {records.map((s) => (
            <button key={slotOf(s)} className={slotOf(record) === slotOf(s) ? "selected" : ""} onClick={() => onPick(stepKey(s.phase, s.id, slotOf(s)))}>
              {slotOf(s)} <Badge value={s.state} />
            </button>
          ))}
        </div>
      )}
      {record && (
        <div className="small">
          <Badge value={record.state} /> {record.reason}
          {record.exit_status !== null && record.exit_status !== undefined && <> · exit {record.exit_status}</>}
          {record.attempts > 1 && <> · {record.attempts} tries</>}
          {record.started && <> · {duration(record.started, record.ended || null)}</>}
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
              <code>{s.logical}</code> <span className="muted">stage slot, on a lab stage of type</span> <code>{s.type}</code>
              <span className={classes("chip", color)}>{label}{s.bound && <> on {s.bound}</>}</span>
              <span className="spacer" />
              <span className="muted small">{s.phases.length ? `phases ${s.phases.join(", ")}` : "used by no phase"}</span>
            </div>
            {s.slots.length > 0 && (
              <div className="small muted">slots {s.slots.map((c: any) => `${c.logical} (role ${c.role}, ${c.type || "any type"})`).join(", ")}</div>
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
              <code>{n.logical}</code> <span className="muted">role</span> <code>{n.role}</code>
              {n.type && <><span className="muted">type</span> <code>{n.type}</code></>}
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
