// Editors of command definitions (component types, stage and host overrides, datapack types), ready checks
// (wait_for) and expected results, used by every form that has commands.
import { useState } from "react";
import { Choices, Combo, Field, helpText, Section, Tags, Text, Toggle, type Choice } from "../fields";
import { classes, Markdown } from "../util";
import { clone, useSession } from "./state";

const CMD_PATH = ["component_types", "_", "commands", "_"];

export const WELL_KNOWN = ["start", "stop", "ready", "install", "version", "deploy", "collect", "kill"];

export function commandKind(spec: any): string {
  if (!spec || typeof spec !== "object") return "run";
  for (const k of ["builtin", "wait_for", "signal", "run"]) if (k in spec) return k;
  return Object.keys(spec).find((k) => !["timeout", "detach", "expect", "env"].includes(k)) || "run";
}

export function summary(spec: any): string {
  if (!spec || typeof spec !== "object") return String(spec ?? "");
  const kind = commandKind(spec);
  if (kind === "run") return `${spec.detach ? "start " : ""}${spec.run ?? ""}`;
  if (kind === "builtin") return `built-in ${spec.builtin}`;
  if (kind === "signal") return `signal ${spec.signal}${spec.timeout ? `, then KILL after ${spec.timeout}` : ""}`;
  if (kind === "wait_for") {
    const w = spec.wait_for || {};
    const check = Object.keys(w).find((k) => !["timeout", "interval", "component"].includes(k));
    return `wait for ${check || "?"} ${check ? JSON.stringify(w[check]) : ""}${w.timeout || spec.timeout ? ` (${w.timeout || spec.timeout})` : ""}`;
  }
  return `${kind}: ${JSON.stringify(spec[kind])}`;
}

export function useCommandHelp() {
  const { schemas } = useSession();
  return (field: string) => helpText(schemas.types, [...CMD_PATH, field]);
}

export function nameChoices(schemas: any, existing: string[]): Choice[] {
  const props = schemas.types?.properties?.component_types?.additionalProperties?.properties?.commands?.properties || {};
  return WELL_KNOWN.filter((n) => !existing.includes(n)).map((n) => ({ value: n, description: (props[n]?.markdownDescription || "").split("\n\n")[0] }));
}

export function ExpectEditor({ value, onChange }: { value: any; onChange: (v: any) => void }) {
  const help = useCommandHelp();
  const v = value || {};
  const set = (k: string, x: any) => {
    const next = { ...v, [k]: x };
    if (x === "" || x === undefined || (Array.isArray(x) && !x.length)) delete next[k];
    onChange(Object.keys(next).length ? next : undefined);
  };
  const codes = v.exit === undefined ? [] : Array.isArray(v.exit) ? v.exit.map(String) : [String(v.exit)];
  return (
    <div className="inline-fields">
      <Field label="Exit codes" help={help("expect")}>
        <Tags value={codes} onChange={(l) => set("exit", l.map((c) => (/^\d+$/.test(c) ? Number(c) : c)))}
          choices={[{ value: "0", description: "success" }, { value: "1" }, { value: "any", description: "accept every exit code" }]} placeholder="default 0" />
      </Field>
      <Field label="Output matches" help="Regular expression the standard output must match (multi-line).">
        <Text mono value={v.stdout} onChange={(x) => set("stdout", x)} placeholder="regex, optional" />
      </Field>
    </div>
  );
}

const WAIT_KINDS: Choice[] = [
  { value: "state", description: "the monitored state of the component" },
  { value: "log", description: "a line in a log file on the component" },
  { value: "tcp", description: "a TCP port accepting connections" },
  { value: "http", description: "an HTTP answer" },
  { value: "file", description: "a file that exists on the component" },
  { value: "cmd", description: "a command exiting with 0" },
];

export function WaitForEditor({ value, onChange, withComponent, components }: {
  value: any; onChange: (v: any) => void; withComponent?: boolean; components?: Choice[];
}) {
  const { ctx, schemas } = useSession();
  const v = value || {};
  const kinds = [...WAIT_KINDS, ...(ctx.ready_checks || []).map((r: string) => ({ value: r, description: "plugin ready check" }))];
  const kind = Object.keys(v).find((k) => !["timeout", "interval", "component"].includes(k)) || "";
  const waitHelp = (k: string) => helpText(schemas.types, [...CMD_PATH, "wait_for", k]);
  const setKind = (k: string | null) => {
    const next: any = { ...v };
    if (kind) delete next[kind];
    if (k) next[k] = k === "state" ? "running" : k === "log" ? { file: "{{ component.output_dir }}/app.log", regex: "READY" } : k === "http" ? { url: "http://{{ component.host }}:8080/health" } : k === "tcp" ? "{{ component.host }}:8080" : "";
    onChange(next);
  };
  const set = (k: string, x: any) => {
    const next = { ...v, [k]: x };
    if (x === "" || x === undefined) delete next[k];
    onChange(next);
  };
  const val = v[kind];
  return (
    <div className="sub-form">
      <Field label="Wait for" help={helpText(schemas.types, [...CMD_PATH, "wait_for"])}>
        <Choices value={kind} onChange={setKind} choices={kinds} />
      </Field>
      {kind === "state" && (
        <Field label="State" help={waitHelp("state")}>
          <Tags value={Array.isArray(val) ? val : val ? [val] : []} onChange={(l) => set("state", l.length === 1 ? l[0] : l)}
            choices={["running", "idle", "configured", "broken", "offline", "unknown"].map((s) => ({ value: s }))} />
        </Field>
      )}
      {kind === "log" && (
        <>
          <Field label="Log file" help="Path of the log file on the component (templates allowed)."><Text mono value={val?.file} onChange={(x) => set("log", { ...val, file: x })} /></Field>
          <Field label="Line matching" help="Regular expression (grep -E) a line must match."><Text mono value={val?.regex} onChange={(x) => set("log", { ...val, regex: x })} /></Field>
        </>
      )}
      {kind === "http" && (
        <>
          <Field label="URL" help={waitHelp("http")}><Text mono value={typeof val === "string" ? val : val?.url} onChange={(x) => set("http", { ...(typeof val === "object" ? val : {}), url: x })} /></Field>
          <Field label="Status" help="Expected HTTP status (default 200)."><Text value={val?.status} onChange={(x) => set("http", { ...(typeof val === "object" ? val : { url: val }), status: x ? Number(x) : undefined })} placeholder="200" /></Field>
          <Field label="Body matches" help="Optional regular expression on the body."><Text mono value={val?.regex} onChange={(x) => set("http", { ...(typeof val === "object" ? val : { url: val }), regex: x || undefined })} /></Field>
        </>
      )}
      {(kind === "tcp" || kind === "file" || kind === "cmd") && (
        <Field label={kind === "tcp" ? "host:port" : kind === "file" ? "Path" : "Command"} help={waitHelp(kind)}>
          <Text mono value={typeof val === "object" ? JSON.stringify(val) : val} onChange={(x) => set(kind, x)} />
        </Field>
      )}
      {kind && !WAIT_KINDS.some((k) => k.value === kind) && (
        <Field label="Value" help="Given to the plugin ready check as is."><Text mono value={typeof val === "object" ? JSON.stringify(val) : val} onChange={(x) => set(kind, x)} /></Field>
      )}
      <div className="inline-fields">
        <Field label="Timeout" help="Then the step is `error` (default 60 s)."><Text value={v.timeout} onChange={(x) => set("timeout", x)} placeholder="60s" /></Field>
        <Field label="Interval" help="Time between checks (default 0.5 s)."><Text value={v.interval} onChange={(x) => set("interval", x)} placeholder="0.5s" /></Field>
      </div>
      {withComponent && (
        <Field label="Component checked" help="The logical component the check is about, when the step has no component.">
          <Combo value={v.component} onChange={(x) => set("component", x)} choices={components || []} />
        </Field>
      )}
    </div>
  );
}

const KINDS: Choice[] = [
  { value: "run", label: "shell command", description: "Run a command on the component (or start it, detached)." },
  { value: "wait_for", label: "wait for", description: "Wait until a check passes: state, log line, port, URL…" },
  { value: "signal", label: "signal", description: "Signal the processes the run started (TERM, then KILL)." },
  { value: "builtin", label: "built-in", description: "deploy, collect or kill, as linux_system does them." },
];

export function CommandEditor({ name, spec, onChange }: { name: string; spec: any; onChange: (v: any) => void }) {
  const help = useCommandHelp();
  const { ctx } = useSession();
  const kinds = [...KINDS, ...(ctx.step_types || []).map((s: string) => ({ value: s, label: s, description: "plugin step type" }))];
  const kind = commandKind(spec);
  const s = spec && typeof spec === "object" ? spec : {};
  const set = (k: string, x: any) => {
    const next = { ...s, [k]: x };
    if (x === "" || x === undefined || x === false) delete next[k];
    onChange(next);
  };
  const setKind = (k: string | null) => {
    if (!k || k === kind) return;
    const keep: any = {};
    if (s.timeout) keep.timeout = s.timeout;
    onChange({
      ...keep,
      ...(k === "run" ? { run: "" } : k === "wait_for" ? { wait_for: { log: { file: "{{ component.output_dir }}/app.log", regex: "READY" }, timeout: "30s" } }
        : k === "signal" ? { signal: "TERM", timeout: "10s" } : k === "builtin" ? { builtin: WELL_KNOWN.includes(name) && ["deploy", "collect", "kill"].includes(name) ? name : "deploy" } : { [k]: {} }),
    });
  };
  return (
    <div className="sub-form">
      <Field label="Does" help={help("run")?.split("\n\n")[0]}>
        <Choices value={kind} onChange={setKind} choices={kinds} />
      </Field>
      {kind === "run" && (
        <>
          <Field label="Command" help={help("run")} wide>
            <Text mono multiline value={s.run} onChange={(x) => set("run", x)} placeholder="sudo systemctl start my-app" />
          </Field>
          <div className="inline-fields">
            <Field label="Detach" help={help("detach")}><Toggle value={s.detach} onChange={(x) => set("detach", x)} label="start and go on" /></Field>
            <Field label="Timeout" help={help("timeout")}><Text value={s.timeout} onChange={(x) => set("timeout", x)} placeholder="none" /></Field>
          </div>
          <ExpectEditor value={s.expect} onChange={(x) => set("expect", x)} />
        </>
      )}
      {kind === "wait_for" && <WaitForEditor value={s.wait_for} onChange={(x) => set("wait_for", x)} />}
      {kind === "signal" && (
        <div className="inline-fields">
          <Field label="Signal" help={help("signal")}><Combo value={s.signal} onChange={(x) => set("signal", x)} choices={["TERM", "INT", "HUP", "USR1", "KILL"].map((v) => ({ value: v }))} /></Field>
          <Field label="Then KILL after" help="Grace period before KILL (default 10 s)."><Text value={s.timeout} onChange={(x) => set("timeout", x)} placeholder="10s" /></Field>
        </div>
      )}
      {kind === "builtin" && (
        <Field label="Built-in" help={help("builtin")}>
          <Choices value={s.builtin} onChange={(x) => x && set("builtin", x)} choices={[
            { value: "deploy", description: "push the inputs to <remote_root>/<component>/input" },
            { value: "collect", description: "pull <remote_root>/<component>/output into the results" },
            { value: "kill", description: "kill every process tree the run started" },
          ]} />
        </Field>
      )}
      {!["run", "wait_for", "signal", "builtin"].includes(kind) && (
        <Field label="Options" help="Given to the plugin step type (YAML / JSON)."><Text mono multiline value={JSON.stringify(s[kind] ?? {})} onChange={(x) => {
          try {
            set(kind, JSON.parse(x));
          } catch {
            /* typing */
          }
        }} /></Field>
      )}
    </div>
  );
}

/** Commands by name: inherited ones (read-only, can be overridden) and own ones (editable). */
export function CommandsEditor({ value, onChange, inherited, title, help }: {
  value: Record<string, any> | null | undefined; onChange: (v: Record<string, any>) => void;
  inherited?: Record<string, { spec: any; from: string }>; title?: string; help?: string;
}) {
  const { schemas } = useSession();
  const own = value || {};
  const [openName, setOpenName] = useState<string | null>(null);
  const [adding, setAdding] = useState("");
  const names = [...new Set([...Object.keys(inherited || {}), ...Object.keys(own)])];
  const set = (name: string, spec: any) => onChange({ ...own, [name]: spec });
  const remove = (name: string) => {
    const next = clone(own);
    delete next[name];
    onChange(next);
  };
  const add = (name: string) => {
    const n = name.trim();
    if (!n) return;
    set(n, inherited?.[n] ? clone(inherited[n].spec) : n === "stop" ? { signal: "TERM", timeout: "10s" } : n === "ready" ? { wait_for: { log: { file: "{{ component.output_dir }}/app.log", regex: "READY" }, timeout: "30s" } } : { run: "" });
    setOpenName(n);
    setAdding("");
  };
  const wellKnown = nameChoices(schemas, Object.keys(own));
  return (
    <Section title={title || "Commands"} help={help}>
      <table className="grid compact commands">
        <tbody>
          {names.map((name) => {
            const mine = name in own;
            const from = inherited?.[name];
            return [
              <tr key={name} className={classes("clickable", !mine && "inherited", openName === name && "selected")} onClick={() => setOpenName(openName === name ? null : name)}>
                <td><code>{name}</code></td>
                <td className="mono small">{summary(mine ? own[name] : from?.spec)}</td>
                <td className="muted small">{mine ? (from ? `overrides ${from.from}` : "defined here") : `from ${from?.from}`}</td>
                <td>
                  {mine ? <button className="small" onClick={(e) => {
                    e.stopPropagation();
                    remove(name);
                  }}>{from ? "Use the inherited one" : "Remove"}</button>
                    : <button className="small" onClick={(e) => {
                      e.stopPropagation();
                      add(name);
                    }}>Override</button>}
                </td>
              </tr>,
              openName === name && mine && (
                <tr key={name + ":edit"} className="editing"><td colSpan={4}><CommandEditor name={name} spec={own[name]} onChange={(s) => set(name, s)} /></td></tr>
              ),
              openName === name && !mine && from && (
                <tr key={name + ":view"} className="editing"><td colSpan={4}><pre className="mono small">{JSON.stringify(from.spec, null, 1)}</pre><Markdown text={wellKnown.find((w) => w.value === name)?.description} /></td></tr>
              ),
            ];
          })}
        </tbody>
      </table>
      <div className="add-row">
        <Combo value={adding} onChange={setAdding} choices={wellKnown} placeholder="add a command: start, stop, ready…" onPick={add} />
        {adding && <button className="small" onClick={() => add(adding)}>Add</button>}
      </div>
    </Section>
  );
}
