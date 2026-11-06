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

// Editors of command definitions (lab components, stage and host overrides), ready checks
// (wait_for) and expected results, used by every form that has commands.
import { useState } from "react";
import { Choices, Combo, Field, helpText, KeyValues, Section, Tags, Text, Toggle, type Choice } from "../fields";
import { CodeEditor } from "../monaco";
import { classes, Markdown } from "../util";
import { clone, useSession, viaChoices } from "./state";

const CMD_PATH = ["components", "_", "commands", "_"];

export const WELL_KNOWN = ["start", "stop", "ready", "install", "version", "deploy", "collect", "kill"];

export function commandKind(spec: any): string {
  if (!spec || typeof spec !== "object") return "run";
  for (const k of ["builtin", "wait_for", "signal", "run", "python", "http"]) if (k in spec) return k;
  return Object.keys(spec).find((k) => !["timeout", "detach", "expect", "env", "via"].includes(k)) || "run";
}

export function pythonSummary(py: any): string {
  if (!py || typeof py !== "object") return "";
  if (py.script) return String(py.script).split("/").pop() || "";
  const first = String(py.code || "").split("\n").find((l) => l.trim() && !l.startsWith("import ")) || "";
  return `code: ${first.trim().slice(0, 40)}`;
}

/** The `python` action of a step or a command: a script on the target, or inline code edited as Python. */
export function PythonEditor({ value, onChange, help, uri }: {
  value: any; onChange: (v: any) => void; help: (field: string) => string | undefined; uri: string;
}) {
  const v = value && typeof value === "object" ? value : {};
  const inline = "code" in v;
  const set = (k: string, x: any) => {
    const next = { ...v, [k]: x };
    if (x === "" || x === undefined || (Array.isArray(x) && !x.length)) delete next[k];
    onChange(next);
  };
  return (
    <>
      <Field label="Source" help={help("")}>
        <Choices value={inline ? "code" : "script"} onChange={(k) => {
          if (!k || k === (inline ? "code" : "script")) return;
          const { code, script, ...rest } = v;
          onChange(k === "code" ? { ...rest, code: code ?? "import sys\n\nprint(keys['run']['id'])\nsys.exit(0)\n" }
            : { ...rest, script: script ?? "{{ component.input_dir }}/check.py" });
        }} choices={[
          { value: "script", label: "a script file", description: "on the target, usually an input of the component" },
          { value: "code", label: "inline code", description: "written here, the keys as `keys`" },
        ]} />
      </Field>
      {inline ? (
        <Field label="Code" help={help("code")} wide>
          <div className="code-box"><CodeEditor uri={uri} language="python" value={String(v.code ?? "")} onChange={(x) => onChange({ ...v, code: x })} height={200} /></div>
        </Field>
      ) : (
        <>
          <Field label="Script" help={help("script")} wide><Text keys mono value={v.script} onChange={(x) => set("script", x)} placeholder="{{ component.input_dir }}/check.py" /></Field>
          <Field label="Arguments" help={help("args")}><Tags keys value={v.args} onChange={(x) => set("args", x)} placeholder="{{ component.output_dir }}" /></Field>
        </>
      )}
      <Field label="Interpreter" help={help("interpreter")}>
        <Combo mono value={v.interpreter} onChange={(x) => set("interpreter", x)} choices={[
          { value: "python3", description: "the default on a component" },
          { value: "{{ component.vars.venv }}/bin/python", description: "a virtual environment named by a component variable" },
        ]} />
      </Field>
    </>
  );
}

export function summary(spec: any): string {
  if (!spec || typeof spec !== "object") return String(spec ?? "");
  const kind = commandKind(spec);
  if (kind === "run") return `${spec.detach ? "start " : ""}${spec.run ?? ""}${spec.via ? ` (via ${spec.via})` : ""}`;
  if (kind === "python") return `python ${pythonSummary(spec.python)}`;
  if (kind === "http") return `${httpSummary(spec.http)}${spec.via ? ` (via ${spec.via})` : ""}`;
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
  return (field: string) => helpText(schemas.library, [...CMD_PATH, field]);
}

export function nameChoices(schemas: any, existing: string[]): Choice[] {
  const props = schemas.library?.properties?.components?.additionalProperties?.properties?.commands?.properties || {};
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

/** A short line for an HTTP request: its method and URL. */
export function httpSummary(h: any): string {
  if (!h) return "";
  if (typeof h === "string") return `GET ${h}`;
  return `${String(h.method || "GET").toUpperCase()} ${h.url || ""}`;
}

/** Parses a JSON value typed in a field; undefined while it is not valid JSON yet. */
function parseJson(text: string): { ok: boolean; value?: any } {
  if (!text.trim()) return { ok: true, value: undefined };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

/** A JSON value edited as text, kept as typed until it parses. */
function JsonField({ value, onChange, placeholder }: { value: any; onChange: (v: any) => void; placeholder?: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value === undefined ? "" : JSON.stringify(value, null, 1));
  const parsed = parseJson(shown);
  return <Text keys mono multiline value={shown} invalid={!parsed.ok} placeholder={placeholder} onChange={(x) => {
    setDraft(x);
    const p = parseJson(x);
    if (p.ok) onChange(p.value);
  }} onBlur={() => setDraft(null)} />;
}

/** An HTTP request (a REST API call): the `http` of a command, a step, a status probe or a wait_for.
 * withSave: commands and steps keep the answer with the results. */
export function HttpEditor({ value, onChange, help, withSave }: {
  value: any; onChange: (v: any) => void; help: (field: string) => string | undefined; withSave?: boolean;
}) {
  const v = typeof value === "string" ? { url: value } : value && typeof value === "object" ? value : {};
  const set = (k: string, x: any) => {
    const next = { ...v, [k]: x };
    if (x === "" || x === undefined || x === null || (typeof x === "object" && !Array.isArray(x) && !Object.keys(x).length)) delete next[k];
    onChange(next);
  };
  const expect = v.expect || {};
  const setExpect = (k: string, x: any) => {
    const next = { ...expect, [k]: x };
    if (x === "" || x === undefined || (Array.isArray(x) && !x.length)) delete next[k];
    set("expect", Object.keys(next).length ? next : undefined);
  };
  const bodyKind = "json" in v ? "json" : "body" in v ? "body" : "none";
  const statuses: string[] = (Array.isArray(expect.status) ? expect.status : expect.status !== undefined ? [expect.status] : []).map(String);
  return (
    <>
      <div className="inline-fields">
        <Field label="Method" help={help("method")}>
          <Combo value={v.method} onChange={(x) => set("method", x ? x.toUpperCase() : undefined)} placeholder="GET"
            choices={["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"].map((m) => ({ value: m }))} />
        </Field>
        <Field label="URL" help={help("url")} wide>
          <Text keys mono value={v.url} onChange={(x) => set("url", x)} placeholder="{{ component.vars.api }}/status" />
        </Field>
      </div>
      <Field label="Headers" help={help("headers")}><KeyValues value={v.headers} onChange={(x) => set("headers", x)} /></Field>
      <Field label="Query parameters" help={help("params")}><KeyValues value={v.params} onChange={(x) => set("params", x)} /></Field>
      <Field label="Body" help={help("json")}>
        <Choices value={bodyKind} onChange={(k) => {
          if (!k || k === bodyKind) return;
          const { json: _j, body: _b, ...rest } = v;
          onChange(k === "json" ? { ...rest, json: {} } : k === "body" ? { ...rest, body: "" } : rest);
        }} choices={[{ value: "none", label: "none" }, { value: "json", label: "JSON" }, { value: "body", label: "raw text" }]} />
      </Field>
      {bodyKind === "json" && <Field label="JSON body" help={help("json")} wide><JsonField value={v.json} onChange={(x) => onChange({ ...v, json: x ?? {} })} placeholder='{"mode": "run"}' /></Field>}
      {bodyKind === "body" && <Field label="Raw body" help={help("body")} wide><Text keys mono multiline value={v.body} onChange={(x) => onChange({ ...v, body: x })} /></Field>}
      <Section title="Expected answer" help={help("expect")}>
        <div className="inline-fields">
          <Field label="Status" help={help("expect")}>
            <Tags value={statuses} onChange={(l) => setExpect("status", l.length === 1 ? Number(l[0]) : l.map(Number).filter((n) => !Number.isNaN(n)))} placeholder="below 400" />
          </Field>
          <Field label="Body matches" help="A regular expression the body must match."><Text mono value={expect.regex} onChange={(x) => setExpect("regex", x)} /></Field>
        </div>
        <Field label="JSON contains" help="Values the JSON answer must contain; other fields are ignored." wide>
          <JsonField value={expect.json} onChange={(x) => setExpect("json", x)} placeholder='{"state": "started"}' />
        </Field>
      </Section>
      <div className="inline-fields">
        <Field label="Request timeout" help={help("timeout")}><Text keys value={v.timeout} onChange={(x) => set("timeout", x)} placeholder="30s" /></Field>
        <Field label="TLS" help={help("verify")}><Toggle value={v.verify === false} onChange={(x) => set("verify", x ? false : undefined)} label="accept a self-signed certificate" /></Field>
        {withSave && <Field label="Save the answer as" help={help("save")}><Text mono value={v.save} onChange={(x) => set("save", x)} placeholder="answer.json" /></Field>}
      </div>
    </>
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

export function WaitForEditor({ value, onChange, withSlot, slots }: {
  value: any; onChange: (v: any) => void; withSlot?: boolean; slots?: Choice[];
}) {
  const { ctx, schemas } = useSession();
  const v = value || {};
  const kinds = [...WAIT_KINDS, ...(ctx.ready_checks || []).map((r: string) => ({ value: r, description: "plugin ready check" }))];
  const kind = Object.keys(v).find((k) => !["timeout", "interval", "component"].includes(k)) || "";
  const waitHelp = (k: string) => helpText(schemas.library, [...CMD_PATH, "wait_for", k]);
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
      <Field label="Wait for" help={helpText(schemas.library, [...CMD_PATH, "wait_for"])}>
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
          <Field label="Log file" help="Path of the log file on the component (templates allowed)."><Text keys mono value={val?.file} onChange={(x) => set("log", { ...val, file: x })} /></Field>
          <Field label="Line matching" help="Regular expression (grep -E) a line must match."><Text keys mono value={val?.regex} onChange={(x) => set("log", { ...val, regex: x })} /></Field>
        </>
      )}
      {kind === "http" && (
        <HttpEditor value={val} onChange={(x) => set("http", x)} help={(f) => helpText(schemas.library, [...CMD_PATH, "wait_for", "http", ...(f ? [f] : [])]) || waitHelp("http")} />
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
        <Field label="Timeout" help="Then the step is `error` (default 60 s)."><Text keys value={v.timeout} onChange={(x) => set("timeout", x)} placeholder="60s" /></Field>
        <Field label="Interval" help="Time between checks (default 0.5 s)."><Text value={v.interval} onChange={(x) => set("interval", x)} placeholder="0.5s" /></Field>
      </div>
      {withSlot && (
        <Field label="Slot checked" help="The slot the check is about, when the step has no slot.">
          <Combo value={v.slot} onChange={(x) => set("slot", x)} choices={slots || []} />
        </Field>
      )}
    </div>
  );
}

const KINDS: Choice[] = [
  { value: "run", label: "shell command", description: "Run a command on the component (or start it, detached)." },
  { value: "python", label: "Python script", description: "A script or inline code, with the run's keys; an exception is an error." },
  { value: "http", label: "REST API", description: "An HTTP request: URL, method, headers, body, the expected answer." },
  { value: "wait_for", label: "wait for", description: "Wait until a check passes: state, log line, port, URL…" },
  { value: "signal", label: "signal", description: "Signal the processes the run started (TERM, then KILL)." },
  { value: "builtin", label: "built-in", description: "deploy, collect or kill, as linux_system does them." },
];

export function CommandEditor({ name, spec, onChange, noVia }: { name: string; spec: any; onChange: (v: any) => void; noVia?: boolean }) {
  const help = useCommandHelp();
  const { ctx, schemas } = useSession();
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
      ...(k === "run" ? { run: "" } : k === "python" ? { python: { script: "{{ component.input_dir }}/check.py" } } : k === "wait_for" ? { wait_for: { log: { file: "{{ component.output_dir }}/app.log", regex: "READY" }, timeout: "30s" } }
        : k === "http" ? { http: { url: "http://{{ component.host }}:8080/api/status" } }
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
            <Text keys mono multiline value={s.run} onChange={(x) => set("run", x)} placeholder="sudo systemctl start my-app" />
          </Field>
          <div className="inline-fields">
            <Field label="Detach" help={help("detach")}><Toggle value={s.detach} onChange={(x) => set("detach", x)} label="start and go on" /></Field>
            <Field label="Timeout" help={help("timeout")}><Text keys value={s.timeout} onChange={(x) => set("timeout", x)} placeholder="none" /></Field>
          </div>
          {!noVia && (
            <Field label="Run it through" help={help("via")}
              hint={s.via ? <>runs on <code>{s.via}</code>; use <code>{"{{ component.host }}"}</code> for this component, <code>{"{{ via.host }}"}</code> for <code>{s.via}</code></> : "empty: on the component itself"}>
              <Combo value={s.via} onChange={(x) => set("via", x)} choices={viaChoices(ctx)} placeholder="stage_server" mono />
            </Field>
          )}
          <ExpectEditor value={s.expect} onChange={(x) => set("expect", x)} />
        </>
      )}
      {kind === "python" && (
        <>
          <PythonEditor value={s.python} onChange={(x) => set("python", x)} uri={`inmemory:///command/${name}.py`}
            help={(f) => helpText(schemas.library, [...CMD_PATH, "python", ...(f ? [f] : [])])} />
          <div className="inline-fields">
            <Field label="Detach" help={help("detach")}><Toggle value={s.detach} onChange={(x) => set("detach", x)} label="start and go on" /></Field>
            <Field label="Timeout" help={help("timeout")}><Text keys value={s.timeout} onChange={(x) => set("timeout", x)} placeholder="none" /></Field>
          </div>
          <ExpectEditor value={s.expect} onChange={(x) => set("expect", x)} />
        </>
      )}
      {kind === "wait_for" && <WaitForEditor value={s.wait_for} onChange={(x) => set("wait_for", x)} />}
      {kind === "http" && (
        <>
          <HttpEditor value={s.http} onChange={(x) => set("http", x)} withSave help={(f) => helpText(schemas.library, [...CMD_PATH, "http", ...(f ? [f] : [])])} />
          {!noVia && (
            <Field label="Send it from" help={help("via")} hint={s.via ? <>sent from <code>{s.via}</code> with curl</> : "empty: from the sequencer host"}>
              <Combo value={s.via} onChange={(x) => set("via", x)} choices={viaChoices(ctx)} placeholder="stage_server" mono />
            </Field>
          )}
        </>
      )}
      {kind === "signal" && (
        <div className="inline-fields">
          <Field label="Signal" help={help("signal")}><Combo value={s.signal} onChange={(x) => set("signal", x)} choices={["TERM", "INT", "HUP", "USR1", "KILL"].map((v) => ({ value: v }))} /></Field>
          <Field label="Then KILL after" help="Grace period before KILL (default 10 s)."><Text keys value={s.timeout} onChange={(x) => set("timeout", x)} placeholder="10s" /></Field>
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
      {!["run", "python", "http", "wait_for", "signal", "builtin"].includes(kind) && (
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

/** Commands by name: inherited ones (read-only, can be overridden) and own ones (editable). With required (the
 * commands a role lists, with what each does), those implemented nowhere are shown as missing. */
export function CommandsEditor({ value, onChange, inherited, required, title, help }: {
  value: Record<string, any> | null | undefined; onChange: (v: Record<string, any>) => void;
  inherited?: Record<string, { spec: any; from: string }>; required?: Record<string, { description?: string; from?: string }>;
  title?: string; help?: string;
}) {
  const { schemas } = useSession();
  const own = value || {};
  const [openName, setOpenName] = useState<string | null>(null);
  const [adding, setAdding] = useState("");
  const names = [...new Set([...Object.keys(required || {}), ...Object.keys(inherited || {}), ...Object.keys(own)])];
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
            const role = required?.[name];
            if (!mine && !from)
              return (
                <tr key={name} className="missing" title={role?.description}>
                  <td><code>{name}</code></td>
                  <td className="error-text small">not implemented{role?.description ? `: ${role.description}` : ""}</td>
                  <td className="muted small">{role?.from ? `listed by role ${role.from}` : ""}</td>
                  <td><button className="small" onClick={() => add(name)}>Implement</button></td>
                </tr>
              );
            return [
              <tr key={name} className={classes("clickable", !mine && "inherited", openName === name && "selected")} onClick={() => setOpenName(openName === name ? null : name)}>
                <td><code>{name}</code></td>
                <td className="mono small">{summary(mine ? own[name] : from?.spec)}</td>
                <td className="muted small">{mine ? (from ? `overrides ${from.from}` : "defined here") : `from ${from?.from}`}{required && !role && <span className="warning-text"> · not in the role</span>}</td>
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
