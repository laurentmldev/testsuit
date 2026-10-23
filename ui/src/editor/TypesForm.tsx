// Component type library form (sequencer README, section 7): each type with what it inherits, its own
// commands, status probe, self-test and transport.
import { useState } from "react";
import { Choices, Combo, Dialog, Field, helpText, Section, Tags, Text } from "../fields";
import { classes, freeName, Markdown, slug } from "../util";
import { CommandEditor, CommandsEditor, summary } from "./commands";
import { clone, transportChoices, typeChoices, useSession, type Doc } from "./state";

const TP = ["component_types", "_"];

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
  { id: "derived", label: "Variant of an existing type", description: "Extends a type of the library and redefines only what differs.", body: (base) => ({ extends: base, description: "" }) },
];

export function TypesForm({ doc, api }: { doc: Doc; api: { setData: (d: any) => void } }) {
  const { ctx } = useSession();
  const data = doc.data || {};
  const types: Record<string, any> = data.component_types || {};
  const [sel, setSel] = useState<string | null>(Object.keys(types)[0] || null);
  const [creating, setCreating] = useState(false);
  const setType = (name: string, value: any) => {
    const draft = clone(data);
    draft.component_types ||= {};
    if (value === undefined) delete draft.component_types[name];
    else draft.component_types[name] = value;
    api.setData(draft);
  };
  const rename = (from: string, to: string) => {
    if (!to || to === from || to in types) return;
    const draft = clone(data);
    const entries = Object.entries(draft.component_types);
    draft.component_types = Object.fromEntries(entries.map(([k, v]) => [k === from ? to : k, v]));
    api.setData(draft);
    setSel(to);
  };
  return (
    <div className="form-split">
      <div className="form-nav">
        <div className="nav-title">Types in this file <button className="small" onClick={() => setCreating(true)}>+ Type</button></div>
        {Object.entries(types).map(([name, t]) => (
          <div key={name} className={classes("nav-item", sel === name && "selected")} onClick={() => setSel(name)}>
            <strong>{name}</strong> <span className="muted small">{t?.extends}</span>
            {ctx.lab.types[name]?.used_by?.length ? <div className="muted small">used by {ctx.lab.types[name].used_by.join(", ")}</div> : null}
          </div>
        ))}
        <div className="nav-hint">
          <Markdown text={"Every type in the library: " + Object.keys(ctx.lab.types).map((t) => `\`${t}\``).join(", ")} />
        </div>
      </div>
      <div className="form-main">
        {sel && types[sel] ? <TypeForm name={sel} value={types[sel]} onChange={(v) => setType(sel, v)} onRename={(to) => rename(sel, to)}
          onDelete={() => {
            if (!window.confirm(`Delete type ${sel}?${ctx.lab.types[sel]?.used_by?.length ? ` It is used by ${ctx.lab.types[sel].used_by.join(", ")}.` : ""}`)) return;
            setType(sel, undefined);
            setSel(null);
          }} /> : <p className="muted">Pick a type, or add one.</p>}
      </div>
      {creating && <NewType taken={Object.keys(ctx.lab.types)} onClose={() => setCreating(false)} onCreate={(name, body) => {
        setType(name, body);
        setSel(name);
        setCreating(false);
      }} />}
    </div>
  );
}

function NewType({ taken, onClose, onCreate }: { taken: string[]; onClose: () => void; onCreate: (name: string, body: any) => void }) {
  const { ctx } = useSession();
  const [starter, setStarter] = useState("service");
  const [base, setBase] = useState("linux_system");
  const [name, setName] = useState(freeName("my_type", taken));
  const s = STARTERS.find((x) => x.id === starter)!;
  return (
    <Dialog title="New component type" onClose={onClose} footer={<button className="primary" disabled={!name || taken.includes(name)} onClick={() => onCreate(name, s.body(base))}>Create</button>}>
      <Field label="Start from"><Choices value={starter} onChange={(v) => v && setStarter(v)} choices={STARTERS.map((x) => ({ value: x.id, label: x.label, description: x.description }))} /></Field>
      {starter === "derived" && <Field label="Type to extend"><Combo value={base} onChange={setBase} choices={typeChoices(ctx)} strict /></Field>}
      <Field label="Name" help="The name components give in `tsq_type`: the kind of hardware or software (`rpi5`, `arduino_unoq`)." error={taken.includes(name) ? "already a type" : null}>
        <Text value={name} onChange={(v) => setName(slug(v))} />
      </Field>
    </Dialog>
  );
}

function TypeForm({ name, value, onChange, onRename, onDelete }: { name: string; value: any; onChange: (v: any) => void; onRename: (to: string) => void; onDelete: () => void }) {
  const { ctx, schemas } = useSession();
  const help = (path: string[]) => helpText(schemas.types, [...TP, ...path]);
  const t = value || {};
  const set = (k: string, v: any) => {
    const next = { ...t, [k]: v };
    if (v === undefined || v === "" || (typeof v === "object" && v && !Array.isArray(v) && !Object.keys(v).length) || (Array.isArray(v) && !v.length)) delete next[k];
    onChange(next);
  };
  // what the parent chain gives (resolved by the server for the library as it is)
  const parent = t.extends ? ctx.lab.types[t.extends] : null;
  const inherited: Record<string, any> = parent ? Object.fromEntries(Object.entries<any>(parent.commands).map(([c, i]) => [c, { spec: i.spec, from: i.from }])) : {};
  const [newName, setNewName] = useState(name);
  return (
    <div>
      <div className="form-title">
        <h3>Type <input className="inline-name" value={newName} onChange={(e) => setNewName(slug(e.target.value))} onBlur={() => onRename(newName)} key={name} /></h3>
        <button className="small danger" onClick={onDelete}>Delete</button>
      </div>
      <Field label="Extends" help={help(["extends"])}>
        <Combo value={t.extends} onChange={(v) => set("extends", v)} choices={typeChoices(ctx, (n) => n !== name)} strict />
      </Field>
      {parent && <div className="hint-box small">Inherits from <code>{parent.chain.join(" → ")}</code>: {Object.keys(parent.commands).map((c) => <code key={c}>{c}</code>)}{parent.status && <>, status from {parent.status.from}</>}{parent.selftest.length > 0 && <>, self-test {parent.selftest.map((c: any) => c.spec.id).join(", ")}</>}</div>}
      <Field label="Description" help={help(["description"])}><Text multiline value={t.description} onChange={(v) => set("description", v)} placeholder="What kind of component this is" /></Field>
      <Field label="Transport" help={help(["transport"])}>
        <Choices value={t.transport} onChange={(v) => set("transport", v ?? undefined)} choices={transportChoices(ctx)} allowNone={`inherited (${parent?.transport || "ssh"})`} />
      </Field>
      <CommandsEditor value={t.commands} onChange={(v) => set("commands", v)} inherited={inherited} help={help(["commands"])} />
      <StatusEditor value={t.status} onChange={(v) => set("status", v)} inherited={parent?.status} />
      <SelftestEditor value={t.selftest} onChange={(v) => set("selftest", v)} inherited={parent?.selftest || []} />
      <Section title="Collect filters" help={help(["collect"])} collapsed={!t.collect}>
        <Field label="Only" help="Glob patterns of the output files to keep (relative to the output folder)."><Tags value={t.collect?.include} onChange={(v) => set("collect", { ...t.collect, include: v.length ? v : undefined })} placeholder="*.csv" /></Field>
        <Field label="Except" help="Glob patterns of the output files to drop."><Tags value={t.collect?.exclude} onChange={(v) => set("collect", { ...t.collect, exclude: v.length ? v : undefined })} placeholder="*.tmp" /></Field>
      </Section>
    </div>
  );
}

const STATES = ["running", "idle", "configured", "broken", "offline", "unknown"];

function StatusEditor({ value, onChange, inherited }: { value: any; onChange: (v: any) => void; inherited?: any }) {
  const { schemas } = useSession();
  const help = (p: string[]) => helpText(schemas.types, [...TP, "status", ...p]);
  const s = value || null;
  const states: Record<string, any> = s?.states || {};
  const set = (k: string, v: any) => {
    const next = { ...(s || {}), [k]: v };
    if (v === undefined || v === "") delete next[k];
    onChange(next);
  };
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
          <Field label="Probe command" help={help(["probe"])}><Text mono value={s.probe?.run} onChange={(v) => set("probe", { ...s.probe, run: v })} placeholder="systemctl is-active my-service" /></Field>
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

function SelftestEditor({ value, onChange, inherited }: { value: any[] | undefined; onChange: (v: any[]) => void; inherited: any[] }) {
  const { schemas } = useSession();
  const list = value || [];
  const [open, setOpen] = useState<number | null>(null);
  const set = (i: number, v: any) => onChange(list.map((c, j) => (j === i ? v : c)));
  return (
    <Section title="Self-test checks" help={helpText(schemas.types, [...TP, "selftest"])}>
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
                <Field label="Id"><Text value={c.id} onChange={(v) => set(i, { ...c, id: slug(v) })} /></Field>
                <CommandEditor name={c.id} spec={c} onChange={(v) => set(i, { id: c.id, ...v })} />
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
