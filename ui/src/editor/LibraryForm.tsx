// Lab library form (sequencer README, section 7): the roles of a file (abstract: the commands each offers, with
// what each does) and its components (concrete: how they implement commands, status probe, self-test, transport).
import { useState, useEffect } from "react";
import { Choices, Combo, Dialog, Field, helpText, IdText, Section, Tags, Text } from "../fields";
import { post } from "../api";
import { attempt, classes, freeName, labIdInput, labIdProblem, Markdown, notify } from "../util";
import { CommandEditor, CommandsEditor, summary, WELL_KNOWN } from "./commands";
import { clone, componentChoices, hostLabel, labRoleChoices, viaChoices, transportChoices, useSession, type Doc } from "./state";

const TP = ["components", "_"];
const RP = ["roles", "_"];
type Kind = "role" | "component";
const SECTION: Record<Kind, string> = { role: "roles", component: "components" };

const STARTERS: { id: string; label: string; description: string; body: (base: string) => any }[] = [
  {
    id: "service", label: "Linux computer with a service", description: "Reached over SSH; start and stop a systemd service; state from systemctl.",
    body: () => ({
      extends: "linux_system", description: "",
      commands: {
        start: { run: "sudo systemctl start my-service", timeout: "30s" }, stop: { run: "sudo systemctl stop my-service", timeout: "30s" },
        ready: { wait_for: { log: { file: "{{ component.output_dir }}/app.log", regex: "READY" }, timeout: "30s" } },
      },
      status: { probe: { run: "systemctl is-active my-service" }, states: { running: { regex: "^active" }, broken: { regex: "^failed" }, idle: { regex: "^inactive" } } },
    }),
  },
  {
    id: "process", label: "Process started by the sequencer", description: "The application runs from the deployed inputs, stopped by a signal: emulated components, simulations.",
    body: () => ({
      extends: "linux_system", description: "",
      commands: {
        start: { run: "cd {{ component.input_dir }} && exec ./app --out {{ component.output_dir }}", detach: true },
        stop: { signal: "TERM", timeout: "10s" },
        ready: { wait_for: { log: { file: "{{ component.output_dir }}/app.log", regex: "READY" }, timeout: "30s" } },
      },
    }),
  },
  {
    id: "monitored", label: "Monitored only", description: "Not a Linux system the sequencer drives: a switch, an instrument. Only its state is probed (ping).",
    body: () => ({
      extends: "component", transport: "local", description: "",
      status: { probe: { run: "ping -c 1 -W 1 {{ component.host }}" }, states: { running: { exit: 0 }, offline: { exit: "nonzero" } } },
    }),
  },
  { id: "derived", label: "Variant of an existing component", description: "Extends a component of the library and redefines only what differs.", body: (base) => ({ extends: base, description: "" }) },
];

export function LibraryForm({ doc, api }: { doc: Doc; api: { setData: (d: any) => void; flush?: () => Promise<void> } }) {
  const session = useSession();
  const { ctx } = session;
  const data = doc.data || {};
  const roles: Record<string, any> = data.roles || {};
  const components: Record<string, any> = data.components || {};
  const first: { kind: Kind; name: string } | null = Object.keys(roles).length ? { kind: "role", name: Object.keys(roles)[0] }
    : Object.keys(components).length ? { kind: "component", name: Object.keys(components)[0] } : null;
  const [sel, setSel] = useState<{ kind: Kind; name: string } | null>(first);
  const [creating, setCreating] = useState<Kind | null>(null);
  const known = (kind: Kind): Record<string, any> => (kind === "role" ? ctx.lab.roles : ctx.lab.library) || {};
  const setItem = (kind: Kind, name: string, value: any) => {
    const draft = clone(data);
    const section = SECTION[kind];
    draft[section] ||= {};
    if (value === undefined) delete draft[section][name];
    else draft[section][name] = value;
    if (!Object.keys(draft[section]).length && Object.keys(draft).length > 1) delete draft[section];
    api.setData(draft);
  };
  // a rename follows everywhere in the session: what extends it in this file, then the other library files
  // and the inventories reading the library (on the server); the helpers' lists are reloaded after
  const rename = async (kind: Kind, from: string, to: string) => {
    const section = SECTION[kind];
    if (!to || to === from || to in (data[section] || {})) return;
    const draft = clone(data);
    draft[section] = Object.fromEntries(Object.entries<any>(draft[section]).map(([k, v]) => [k === from ? to : k, v?.extends === from ? { ...v, extends: to } : v]));
    api.setData(draft);
    setSel({ kind, name: to });
    await api.flush?.();
    const result = await attempt(() => post(`edit/sessions/${session.sid}/rename_references`, { kind, from, to, skip: doc.path }));
    if (result?.changed?.length) notify(`'${from}' is now '${to}' in ${result.changed.join(", ")} too`, "info");
    await session.refreshFiles();
    await session.refreshContext();
  };
  const remove = (kind: Kind, name: string) => {
    const users = known(kind)[name]?.used_by || [];
    if (!window.confirm(`Delete ${kind} ${name}?${users.length ? ` ${kind === "role" ? "It is the role of" : "It is extended by"} ${users.join(", ")}.` : ""}`)) return;
    setItem(kind, name, undefined);
    setSel(null);
  };
  const item = sel ? (sel.kind === "role" ? roles : components)[sel.name] : undefined;
  return (
    <div className="form-split">
      <div className="form-nav">
        <div className="nav-title">Roles <button className="small" onClick={() => setCreating("role")}>+ Role</button></div>
        {Object.entries(roles).map(([name, r]) => (
          <div key={name} className={classes("nav-item", sel?.kind === "role" && sel.name === name && "selected")} onClick={() => setSel({ kind: "role", name })}>
            <strong>{name}</strong> <span className="muted small">{r?.extends}</span>
            {ctx.lab.roles?.[name]?.used_by?.length ? <div className="muted small">role of {ctx.lab.roles[name].used_by.map((h: string) => hostLabel(ctx, h)).join(", ")}</div> : null}
          </div>
        ))}
        <div className="nav-title">Components <button className="small" onClick={() => setCreating("component")}>+ Component</button></div>
        {Object.entries(components).map(([name, c]) => (
          <div key={name} className={classes("nav-item", sel?.kind === "component" && sel.name === name && "selected")} onClick={() => setSel({ kind: "component", name })}>
            <strong>{name}</strong> <span className="muted small">{c?.extends}</span>
            {ctx.lab.library?.[name]?.roles?.length ? <div className="muted small">roles {ctx.lab.library[name].roles.join(", ")}</div> : null}
            {ctx.lab.library?.[name]?.used_by?.length ? <div className="muted small">extended by {ctx.lab.library[name].used_by.map((h: string) => hostLabel(ctx, h)).join(", ")}</div> : null}
          </div>
        ))}
        <div className="nav-hint">
          <Markdown text={"Roles are abstract: the commands a component offers, with what each does. Components are concrete: the roles they support, and how they implement their commands. Inventory hosts extend a component (`tsq_extends`) and have its role, or pick one of its roles (`tsq_role`).\n\nEvery role: " + Object.keys(ctx.lab.roles || {}).map((t) => `\`${t}\``).join(", ") + "\n\nEvery component: " + Object.keys(ctx.lab.library || {}).map((t) => `\`${t}\``).join(", ")} />
        </div>
      </div>
      <div className="form-main">
        {sel && item !== undefined ? (sel.kind === "role"
          ? <RoleForm key={"r:" + sel.name} name={sel.name} value={item} onChange={(v) => setItem("role", sel.name, v)} onRename={(to) => rename("role", sel.name, to)} onDelete={() => remove("role", sel.name)} />
          : <ComponentForm key={"c:" + sel.name} name={sel.name} value={item} onChange={(v) => setItem("component", sel.name, v)} onRename={(to) => rename("component", sel.name, to)} onDelete={() => remove("component", sel.name)} />)
          : <p className="muted">Pick a role or a component, or add one.</p>}
      </div>
      {creating === "component" && <NewComponent taken={[...new Set([...Object.keys(known("component")), ...Object.keys(components)])]} onClose={() => setCreating(null)} onCreate={(name, body) => {
        setItem("component", name, body);
        setSel({ kind: "component", name });
        setCreating(null);
      }} />}
      {creating === "role" && <NewRole taken={[...new Set([...Object.keys(known("role")), ...Object.keys(roles)])]} onClose={() => setCreating(null)} onCreate={(name, body) => {
        setItem("role", name, body);
        setSel({ kind: "role", name });
        setCreating(null);
      }} />}
    </div>
  );
}

function NewRole({ taken, onClose, onCreate }: { taken: string[]; onClose: () => void; onCreate: (name: string, body: any) => void }) {
  const { ctx } = useSession();
  const [base, setBase] = useState("linux_system");
  const [name, setName] = useState(freeName("my_role", taken));
  return (
    <Dialog title="New lab role" onClose={onClose} footer={<button className="primary" disabled={!name || taken.includes(name)} onClick={() => onCreate(name, { extends: base, description: "", commands: {} })}>Create</button>}>
      <Field label="Extends" help="Its commands are offered too: `linux_system` (deploy, collect, kill) for anything reachable over SSH, `component` (no command) otherwise, or another role.">
        <Combo value={base} onChange={setBase} choices={labRoleChoices(ctx)} strict />
      </Field>
      <Field label="Name" help="What components with this role are for (`tsq_role: pilot`): datapacks bind their logical components by role." error={labIdProblem(name, taken)}>
        <Text value={name} onChange={(v) => setName(labIdInput(v))} />
      </Field>
    </Dialog>
  );
}

function NewComponent({ taken, onClose, onCreate }: { taken: string[]; onClose: () => void; onCreate: (name: string, body: any) => void }) {
  const { ctx } = useSession();
  const [starter, setStarter] = useState("service");
  const [base, setBase] = useState("linux_system");
  const [name, setName] = useState(freeName("my_component", taken));
  const s = STARTERS.find((x) => x.id === starter)!;
  return (
    <Dialog title="New lab component" onClose={onClose} footer={<button className="primary" disabled={!name || taken.includes(name)} onClick={() => onCreate(name, s.body(base))}>Create</button>}>
      <Field label="Start from"><Choices value={starter} onChange={(v) => v && setStarter(v)} choices={STARTERS.map((x) => ({ value: x.id, label: x.label, description: x.description }))} /></Field>
      {starter === "derived" && <Field label="Component to extend"><Combo value={base} onChange={setBase} choices={componentChoices(ctx)} strict /></Field>}
      <Field label="Name" help="The kind of hardware or software (`rpi5`, `arduino_unoq`): inventory hosts extend it (`tsq_extends`)." error={labIdProblem(name, taken)}>
        <Text value={name} onChange={(v) => setName(labIdInput(v))} />
      </Field>
    </Dialog>
  );
}

function useNameEdit(name: string, taken: string[], onRename: (to: string) => void) {
  const [newName, setNewName] = useState(name);
  useEffect(() => setNewName(name), [name]);         // another one picked, or created
  const invalid = newName !== name && !!labIdProblem(newName, taken);
  return (
    <input className={classes("inline-name", invalid && "invalid")} value={newName} onChange={(e) => setNewName(labIdInput(e.target.value))}
      onBlur={() => (newName === name || labIdProblem(newName, taken) ? setNewName(name) : onRename(newName))} key={name} />
  );
}

function RoleForm({ name, value, onChange, onRename, onDelete }: { name: string; value: any; onChange: (v: any) => void; onRename: (to: string) => void; onDelete: () => void }) {
  const { ctx, schemas } = useSession();
  const help = (path: string[]) => helpText(schemas.library, [...RP, ...path]);
  const r = value || {};
  const set = (k: string, v: any) => {
    const next = { ...r, [k]: v };
    if (v === undefined || v === "") delete next[k];
    onChange(next);
  };
  const own: Record<string, string | null> = Array.isArray(r.commands) ? Object.fromEntries(r.commands.map((c: string) => [c, ""])) : r.commands || {};
  const setCommands = (c: Record<string, string | null>) => set("commands", Object.keys(c).length ? c : undefined);
  const parent = r.extends ? ctx.lab.roles?.[r.extends] : null;
  const inherited: Record<string, any> = parent?.commands || {};
  const [adding, setAdding] = useState("");
  const add = (n: string) => {
    const c = n.trim();
    if (!c || c in own) return;
    setCommands({ ...own, [c]: "" });
    setAdding("");
  };
  const described = ctx.lab.roles?.[name];
  const nameInput = useNameEdit(name, Object.keys(ctx.lab.roles || {}), onRename);
  return (
    <div>
      <div className="form-title">
        <h3>Role {nameInput}</h3>
        <button className="small danger" onClick={onDelete}>Delete</button>
      </div>
      <Field label="Extends" help={help(["extends"])}>
        <Combo value={r.extends} onChange={(v) => set("extends", v)} choices={labRoleChoices(ctx, (n) => n !== name)} strict />
      </Field>
      <Field label="Description" help={help(["description"])}><Text multiline value={r.description} onChange={(v) => set("description", v)} placeholder="What components with this role are for" /></Field>
      <Section title="Commands offered" help={help(["commands"])}>
        <table className="grid compact commands">
          <tbody>
            {Object.entries<any>(inherited).filter(([c]) => !(c in own)).map(([c, info]) => (
              <tr key={c} className="inherited"><td><code>{c}</code></td><td className="small">{info.description}</td><td className="muted small">from {info.from}</td><td /></tr>
            ))}
            {Object.entries(own).map(([c, text]) => (
              <tr key={c}>
                <td><code>{c}</code></td>
                <td><input value={text || ""} placeholder="what it does" onChange={(e) => setCommands({ ...own, [c]: e.target.value })} /></td>
                <td className="muted small">{c in inherited ? `also in ${inherited[c].from}` : "listed here"}</td>
                <td><button className="icon" onClick={() => {
                  const next = { ...own };
                  delete next[c];
                  setCommands(next);
                }}>×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="add-row">
          <Combo value={adding} onChange={setAdding} choices={WELL_KNOWN.filter((n) => !(n in own) && !(n in inherited)).map((n) => ({ value: n }))} placeholder="add a command: start, stop, ready…" onPick={add} />
          {adding && <button className="small" onClick={() => add(adding)}>Add</button>}
        </div>
        <p className="muted small">A role lists commands only: the lab components implement them, and every host with this role must implement them all.</p>
      </Section>
      {described?.implemented_by?.length > 0 && <div className="hint-box small">Supported by the components {described.implemented_by.map((c: string) => <code key={c}>{c}</code>)}</div>}
      {described?.used_by?.length > 0 && <div className="hint-box small">Role of {described.used_by.map((h: string) => <code key={h} title={h}>{hostLabel(ctx, h)}</code>)}</div>}
    </div>
  );
}

function ComponentForm({ name, value, onChange, onRename, onDelete }: { name: string; value: any; onChange: (v: any) => void; onRename: (to: string) => void; onDelete: () => void }) {
  const { ctx, schemas } = useSession();
  const help = (path: string[]) => helpText(schemas.library, [...TP, ...path]);
  const t = value || {};
  const set = (k: string, v: any) => {
    const next = { ...t, [k]: v };
    if (v === undefined || v === "" || (typeof v === "object" && v && !Array.isArray(v) && !Object.keys(v).length) || (Array.isArray(v) && !v.length)) delete next[k];
    onChange(next);
  };
  // what the parent chain gives (resolved by the server for the library as it is)
  const parent = t.extends ? ctx.lab.library?.[t.extends] : null;
  const inherited: Record<string, any> = parent ? Object.fromEntries(Object.entries<any>(parent.commands).map(([c, i]) => [c, { spec: i.spec, from: i.from }])) : {};
  const described = ctx.lab.library?.[name];
  const nameInput = useNameEdit(name, Object.keys(ctx.lab.library || {}), onRename);
  // the roles it supports (its own list, else its parent's): their commands are required
  const roles: string[] = t.roles ?? parent?.roles ?? [];
  const required: Record<string, { description?: string; from?: string }> = {};
  for (const r of roles) for (const [c, i] of Object.entries<any>(ctx.lab.roles?.[r]?.commands || {})) required[c] = { description: i.description, from: r };
  return (
    <div>
      <div className="form-title">
        <h3>Component {nameInput}</h3>
        <button className="small danger" onClick={onDelete}>Delete</button>
      </div>
      <Field label="Extends" help={help(["extends"])}>
        <Combo value={t.extends} onChange={(v) => set("extends", v)} choices={componentChoices(ctx, { filter: (n) => n !== name })} strict />
      </Field>
      <Field label="Roles it supports" help={help(["roles"])}
        hint={t.roles === undefined && parent?.roles?.length ? `inherited from ${t.extends}: ${parent.roles.join(", ")}` : !roles.length ? "none: a base, whose hosts give their role (tsq_role)" : roles.length > 1 ? "its hosts pick one (tsq_role)" : null}>
        <Tags value={t.roles} onChange={(v) => set("roles", v.length ? v : undefined)} choices={labRoleChoices(ctx)} placeholder="add a role it supports" />
      </Field>
      {Object.entries<any>(described?.role_missing || {}).filter(([, m]) => m.length).map(([r, m]) => (
        <div key={r} className="error-text">Does not implement {m.map((c: string) => <code key={c}>{c}</code>)}, listed by its role {r}: the lab does not load.</div>
      ))}
      {parent && <div className="hint-box small">Inherits from <code>{parent.chain.join(" → ")}</code>: {Object.keys(parent.commands).map((c) => <code key={c}>{c}</code>)}{parent.status && <>, status from {parent.status.from}</>}{parent.selftest.length > 0 && <>, self-test {parent.selftest.map((c: any) => c.spec.id).join(", ")}</>}</div>}
      <Field label="Description" help={help(["description"])}><Text multiline value={t.description} onChange={(v) => set("description", v)} placeholder="What kind of component this is" /></Field>
      <Field label="Transport" help={help(["transport"])}>
        <Choices value={t.transport} onChange={(v) => set("transport", v ?? undefined)} choices={transportChoices(ctx)} allowNone={`inherited (${parent?.transport || "ssh"})`} />
      </Field>
      <CommandsEditor title="Command implementations" value={t.commands} onChange={(v) => set("commands", v)} inherited={inherited} required={roles.length ? required : undefined} help={help(["commands"])} />
      <StatusEditor value={t.status} onChange={(v) => set("status", v)} inherited={parent?.status} />
      <SelftestEditor value={t.selftest} onChange={(v) => set("selftest", v)} inherited={parent?.selftest || []} />
      <Section title="Collect filters" help={help(["collect"])} collapsed={!t.collect}>
        <Field label="Only" help="Glob patterns of the output files to keep (relative to the output folder)."><Tags value={t.collect?.include} onChange={(v) => set("collect", { ...t.collect, include: v.length ? v : undefined })} placeholder="*.csv" /></Field>
        <Field label="Except" help="Glob patterns of the output files to drop."><Tags value={t.collect?.exclude} onChange={(v) => set("collect", { ...t.collect, exclude: v.length ? v : undefined })} placeholder="*.tmp" /></Field>
      </Section>
      {described?.used_by?.length > 0 && <div className="hint-box small">Extended by {described.used_by.map((h: string) => <code key={h} title={h}>{hostLabel(ctx, h)}</code>)}: the inventory shows, for each host, the commands of its role it does not implement.</div>}
    </div>
  );
}

const STATES = ["running", "idle", "configured", "broken", "offline", "unknown"];

export function StatusEditor({ value, onChange, inherited }: { value: any; onChange: (v: any) => void; inherited?: any }) {
  const { schemas, ctx } = useSession();
  const help = (p: string[]) => helpText(schemas.library, [...TP, "status", ...p]);
  const s = value || null;
  const states: Record<string, any> = s?.states || {};
  const set = (k: string, v: any) => {
    const next = { ...(s || {}), [k]: v };
    if (v === undefined || v === "") delete next[k];
    onChange(next);
  };
  // one command, or several checks (run: [...]) that all run
  const runs: string[] = Array.isArray(s?.probe?.run) ? s.probe.run : [s?.probe?.run ?? ""];
  const setRuns = (list: string[]) => set("probe", { ...s.probe, run: list.length > 1 ? list : list[0] ?? "" });
  return (
    <Section title="State monitoring (status)" help={help([])} collapsed={!s}>
      {!s && (
        <p>
          {inherited ? <>Inherited from <code>{inherited.from}</code>: {inherited.spec.builtin ? `built-in ${inherited.spec.builtin}` : summary(inherited.spec.probe)}. </> : "No probe: the state stays unknown. "}
          <button className="small" onClick={() => onChange({ probe: { run: "" }, states: { running: { regex: "^active" }, idle: { regex: "^inactive" } } })}>Define a probe</button>
        </p>
      )}
      {s && (
        <>
          <Field label={runs.length > 1 ? "Probe commands" : "Probe command"} help={help(["probe"])}
            hint={runs.length > 1 ? "each one runs and is OK when it exits with 0; the rules match their outputs, one after the other" : null}>
            {runs.map((r, i) => (
              <div key={i} className="inline-fields">
                <Text keys mono value={r} onChange={(v) => setRuns(runs.map((x, j) => (j === i ? v : x)))} placeholder={i ? "ping -c 1 -W 1 {{ component.host }}" : "systemctl is-active my-service"} />
                {runs.length > 1 && <button className="icon" title="Remove this check" onClick={() => setRuns(runs.filter((_, j) => j !== i))}>×</button>}
              </div>
            ))}
            <button className="small" onClick={() => setRuns([...runs, ""])}>+ check</button>
          </Field>
          <Field label="Run it through" help={help(["via"])}
            hint={s.via ? <>runs on <code>{s.via}</code>; use <code>{"{{ component.host }}"}</code> for the component probed, <code>{"{{ via.host }}"}</code> for <code>{s.via}</code></> : "empty: on the component itself"}>
            <Combo value={s.via} onChange={(v) => set("via", v)} choices={viaChoices(ctx)} placeholder="stage_server" mono />
          </Field>
          <Field label="Every" help={help(["interval"])}><Text value={s.interval} onChange={(v) => set("interval", v)} placeholder="30s" /></Field>
          <Field label="States" help={help(["states"])} wide>
            <table className="grid compact">
              <thead><tr><th>State</th><th>when the output matches</th><th>or the exit status is</th><th /></tr></thead>
              <tbody>
                {Object.entries(states).map(([state, rule]) => (
                  <tr key={state}>
                    <td><code>{state}</code></td>
                    <td><input className="mono" value={rule?.regex ?? ""} onChange={(e) => set("states", { ...states, [state]: { ...rule, regex: e.target.value || undefined } })} /></td>
                    <td><input value={rule?.exit ?? ""} placeholder="0 or nonzero" onChange={(e) => set("states", { ...states, [state]: { ...rule, exit: e.target.value === "" ? undefined : /^\d+$/.test(e.target.value) ? Number(e.target.value) : e.target.value } })} /></td>
                    <td><button className="icon" onClick={() => {
                      const next = { ...states };
                      delete next[state];
                      set("states", next);
                    }}>×</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="add-row">
              {STATES.filter((x) => !(x in states)).map((x) => <button key={x} className="small" onClick={() => set("states", { ...states, [x]: { regex: "" } })}>+ {x}</button>)}
            </div>
            <p className="muted small">The first matching rule wins, in this order.</p>
          </Field>
          <button className="small" onClick={() => onChange(undefined)}>Remove the probe{inherited ? " (use the inherited one)" : ""}</button>
        </>
      )}
    </Section>
  );
}

export function SelftestEditor({ value, onChange, inherited }: { value: any[] | undefined; onChange: (v: any[]) => void; inherited: any[] }) {
  const { schemas } = useSession();
  const list = value || [];
  const [open, setOpen] = useState<number | null>(null);
  const set = (i: number, v: any) => onChange(list.map((c, j) => (j === i ? v : c)));
  return (
    <Section title="Self-test checks" help={helpText(schemas.library, [...TP, "selftest"])}>
      {inherited.length > 0 && <p className="small muted">Inherited: {inherited.map((c) => <code key={c.spec.id} title={summary(c.spec)}>{c.spec.id}</code>)} (a check with the same id replaces one).</p>}
      <table className="grid compact">
        <tbody>
          {list.map((c, i) => [
            <tr key={i} className="clickable" onClick={() => setOpen(open === i ? null : i)}>
              <td><code>{c.id}</code></td><td className="mono small">{c.builtin ? `built-in ${c.builtin}` : c.run}</td><td>{c.timeout}</td>
              <td><button className="icon" onClick={(e) => {
                e.stopPropagation();
                onChange(list.filter((_, j) => j !== i));
              }}>×</button></td>
            </tr>,
            open === i && (
              <tr key={i + "e"} className="editing"><td colSpan={4}>
                <Field label="Id"><IdText value={c.id} taken={list.map((x) => x.id)} onChange={(v) => set(i, { ...c, id: v })} /></Field>
                <CommandEditor name={c.id} spec={c} noVia onChange={(v) => set(i, { id: c.id, ...v })} />
              </td></tr>
            ),
          ])}
        </tbody>
      </table>
      <button className="small" onClick={() => {
        onChange([...list, { id: freeName("check", list.map((c) => c.id)), run: "", timeout: "20s" }]);
        setOpen(list.length);
      }}>+ Check</button>
    </Section>
  );
}
