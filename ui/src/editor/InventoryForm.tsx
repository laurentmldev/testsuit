// Lab inventory form (sequencer README, sections 6 and 13.2): stages, their components, ad hoc components
// and their variables, with the component types, roles and stage types of the lab and the tests at hand.
import { useMemo, useState } from "react";
import { post } from "../api";
import { Choices, Combo, Dialog, Field, helpText, KeyValues, Section, Text, useHelp, type Choice } from "../fields";
import { attempt, classes, freeName, Markdown, slug } from "../util";
import { CommandsEditor, summary } from "./commands";
import { clone, roleChoices, stageTypeChoices, transportChoices, typeChoices, useSession, type Doc } from "./state";

type Inv = any;
type Path = (string | number)[];

interface GroupInfo {
  name: string;
  path: Path;            // path of the group in the document
  stage: boolean;
  vars: any;
  hosts: string[];
}

function groups(inv: Inv): GroupInfo[] {
  const out: GroupInfo[] = [];
  const visit = (name: string, g: any, path: Path) => {
    if (!g || typeof g !== "object") return;
    out.push({ name, path, stage: !!g.vars?.tsq_stage_type, vars: g.vars || {}, hosts: Object.keys(g.hosts || {}) });
    for (const [child, sub] of Object.entries(g.children || {})) visit(child, sub, [...path, "children", child]);
  };
  for (const [child, sub] of Object.entries(inv?.all?.children || {})) visit(child, sub, ["all", "children", child]);
  return out;
}

function getAt(data: any, path: Path): any {
  return path.reduce((d, k) => (d == null ? undefined : d[k]), data);
}

function setAt(data: any, path: Path, value: any) {
  let d = data;
  for (const k of path.slice(0, -1)) {
    if (d[k] == null || typeof d[k] !== "object") d[k] = {};
    d = d[k];
  }
  if (value === undefined) delete d[path[path.length - 1]];
  else d[path[path.length - 1]] = value;
}

/** Where a host is defined: every group listing it; its variables live at the first place that has some. */
function hostPlaces(inv: Inv, host: string): Path[] {
  const places: Path[] = [];
  if (inv?.all?.hosts && host in inv.all.hosts) places.push(["all", "hosts", host]);
  for (const g of groups(inv)) if (g.hosts.includes(host)) places.push([...g.path, "hosts", host]);
  return places;
}

function hostVarsPath(inv: Inv, host: string): Path | null {
  const places = hostPlaces(inv, host);
  return places.find((p) => {
    const v = getAt(inv, p);
    return v && typeof v === "object" && Object.keys(v).length;
  }) || places[0] || null;
}

function allHosts(inv: Inv): string[] {
  const set = new Set<string>(Object.keys(inv?.all?.hosts || {}));
  for (const g of groups(inv)) g.hosts.forEach((h) => set.add(h));
  return [...set];
}

function nextName(name: string, taken: string[]): string {
  const m = name.match(/^(.*?)(\d+)$/);
  if (m) {
    for (let n = Number(m[2]) + 1; ; n++) if (!taken.includes(`${m[1]}${n}`)) return `${m[1]}${n}`;
  }
  return freeName(`${name}_2`, taken);
}

type Selection = { kind: "defaults" } | { kind: "group"; name: string } | { kind: "host"; name: string };

export function InventoryForm({ doc, api }: { doc: Doc; api: { setData: (d: any) => void } }) {
  const { ctx } = useSession();
  const inv = doc.data || {};
  const [sel, setSel] = useState<Selection>({ kind: "defaults" });
  const [wizard, setWizard] = useState<null | { kind: "stage" } | { kind: "adhoc" } | { kind: "member"; stage: string }>(null);
  const gs = groups(inv);
  const stages = gs.filter((g) => g.stage);
  const plain = gs.filter((g) => !g.stage);
  const staged = new Set(stages.flatMap((s) => s.hosts));
  const adhoc = Object.keys(inv?.all?.hosts || {}).filter((h) => !staged.has(h));
  const update = (fn: (draft: Inv) => void) => {
    const draft = clone(inv);
    fn(draft);
    api.setData(draft);
  };
  const missing = useMemo(() => missingFromLab(ctx, inv), [ctx, inv]);
  return (
    <div className="form-split">
      <div className="form-nav">
        <div className={classes("nav-item", sel.kind === "defaults" && "selected")} onClick={() => setSel({ kind: "defaults" })}>Defaults (all.vars)</div>
        <div className="nav-title">Stages <button className="small" onClick={() => setWizard({ kind: "stage" })}>+ Stage</button></div>
        {stages.map((s) => (
          <div key={s.name}>
            <div className={classes("nav-item", sel.kind === "group" && sel.name === s.name && "selected")} onClick={() => setSel({ kind: "group", name: s.name })}>
              <strong>{s.name}</strong> <span className="muted small">{s.vars.tsq_stage_type}</span>
            </div>
            {s.hosts.map((h) => (
              <div key={h} className={classes("nav-item sub", sel.kind === "host" && sel.name === h && "selected")} onClick={() => setSel({ kind: "host", name: h })}>
                {h} <span className="muted small">{hostVars(inv, h).tsq_role || "no role"}</span>
              </div>
            ))}
            <div className="nav-item sub add" onClick={() => setWizard({ kind: "member", stage: s.name })}>+ component</div>
          </div>
        ))}
        <div className="nav-title">Ad hoc components <button className="small" onClick={() => setWizard({ kind: "adhoc" })}>+ Component</button></div>
        {adhoc.map((h) => (
          <div key={h} className={classes("nav-item", sel.kind === "host" && sel.name === h && "selected")} onClick={() => setSel({ kind: "host", name: h })}>
            {h} <span className="muted small">{hostVars(inv, h).tsq_role || "no role"}</span>
          </div>
        ))}
        {plain.length > 0 && <div className="nav-title">Other groups</div>}
        {plain.map((g) => (
          <div key={g.name} className={classes("nav-item", sel.kind === "group" && sel.name === g.name && "selected")} onClick={() => setSel({ kind: "group", name: g.name })}>{g.name}</div>
        ))}
        {missing.length > 0 && (
          <div className="nav-hint">
            <strong>The tests ask for</strong>
            <Markdown text={missing.map((m) => `- ${m}`).join("\n")} />
          </div>
        )}
      </div>
      <div className="form-main">
        {sel.kind === "defaults" && <DefaultsForm inv={inv} update={update} />}
        {sel.kind === "group" && gs.find((g) => g.name === sel.name) && (
          <GroupForm inv={inv} group={gs.find((g) => g.name === sel.name)!} update={update} onSelect={setSel}
            onAdd={() => setWizard({ kind: "member", stage: sel.name })} />
        )}
        {sel.kind === "host" && allHosts(inv).includes(sel.name) && <HostForm inv={inv} name={sel.name} update={update} onRenamed={(n) => setSel({ kind: "host", name: n })} onDeleted={() => setSel({ kind: "defaults" })} />}
      </div>
      {wizard?.kind === "stage" && <NewStage inv={inv} onClose={() => setWizard(null)} onCreate={(draft, name) => {
        api.setData(draft);
        setSel({ kind: "group", name });
        setWizard(null);
      }} />}
      {(wizard?.kind === "adhoc" || wizard?.kind === "member") && (
        <NewHost inv={inv} stage={wizard.kind === "member" ? wizard.stage : null} onClose={() => setWizard(null)} onCreate={(draft, name) => {
          api.setData(draft);
          setSel({ kind: "host", name });
          setWizard(null);
        }} />
      )}
    </div>
  );
}

function hostVars(inv: Inv, host: string): any {
  const p = hostVarsPath(inv, host);
  return (p && getAt(inv, p)) || {};
}

/** Stage types and roles the workspaces' setups ask for that this inventory does not have. */
function missingFromLab(ctx: any, inv: Inv): string[] {
  const gs = groups(inv);
  const stageTypes = new Map<string, string[]>();
  for (const s of gs.filter((g) => g.stage)) stageTypes.set(s.vars.tsq_stage_type, [...(stageTypes.get(s.vars.tsq_stage_type) || []), s.name]);
  const roleIn = (stage: GroupInfo) => stage.hosts.map((h) => hostVars(inv, h).tsq_role);
  const staged = new Set(gs.filter((g) => g.stage).flatMap((s) => s.hosts));
  const adhocRoles = Object.keys(inv?.all?.hosts || {}).filter((h) => !staged.has(h)).map((h) => hostVars(inv, h).tsq_role);
  const out = new Set<string>();
  for (const setup of ctx.workspace?.setups || [])
    for (const [cfg, c] of Object.entries<any>(setup.configurations || {})) {
      for (const [, type] of Object.entries<any>(c.stages || {})) if (type && !stageTypes.has(type)) out.add(`a stage of type \`${type}\` (setup ${setup.id}, ${cfg})`);
      for (const [id, comp] of Object.entries<any>(c.components || {})) {
        if (!comp.role) continue;
        if (comp.stage) {
          const type = c.stages?.[comp.stage];
          for (const name of stageTypes.get(type) || []) {
            const stage = gs.find((g) => g.name === name)!;
            if (!roleIn(stage).includes(comp.role)) out.add(`role \`${comp.role}\` in stage ${name} (component ${id} of ${setup.id}, ${cfg})`);
          }
        } else if (!adhocRoles.includes(comp.role)) out.add(`an ad hoc component with role \`${comp.role}\` (component ${id} of ${setup.id}, ${cfg})`);
      }
    }
  return [...out];
}

function useInvHelp() {
  const { schemas } = useSession();
  return (path: Path) => helpText(schemas.inventory, path);
}

const VAR_KEYS = ["ansible_host", "ansible_user", "ansible_port", "ansible_ssh_private_key_file", "tsq_type", "tsq_role", "tsq_lock",
  "tsq_label", "tsq_transport", "tsq_remote_root", "tsq_commands", "tsq_stage_type", "tsq_types"];

function knownVars(schemas: any, path: Path, exclude: string[] = []): Choice[] {
  const node = path.reduce((n: any, k) => n?.properties?.[k] ?? n?.additionalProperties, schemas.inventory);
  return Object.entries<any>(node?.properties || {}).filter(([k]) => !exclude.includes(k)).map(([k, v]) => ({ value: k, description: v.markdownDescription }));
}

function DefaultsForm({ inv, update }: { inv: Inv; update: (fn: (d: Inv) => void) => void }) {
  const { ctx, schemas } = useSession();
  const help = useInvHelp();
  const vars = inv?.all?.vars || {};
  const set = (k: string, v: any) => update((d) => setAt(d, ["all", "vars", k], v === "" ? undefined : v));
  return (
    <div>
      <h3>Defaults of every component</h3>
      <p className="muted">Variables under <code>all.vars</code>: a stage or a host can redefine each of them.</p>
      <Field label="Default type" help={help(["all", "vars", "tsq_type"])}>
        <Combo value={vars.tsq_type} onChange={(v) => set("tsq_type", v)} choices={typeChoices(ctx)} strict placeholder="linux_system" />
      </Field>
      <Field label="Transport" help={help(["all", "vars", "tsq_transport"])}>
        <Choices value={vars.tsq_transport} onChange={(v) => set("tsq_transport", v ?? undefined)} choices={transportChoices(ctx)} allowNone="default (ssh)" />
      </Field>
      <Field label="Remote root" help={help(["all", "vars", "tsq_remote_root"])}><Text mono value={vars.tsq_remote_root} onChange={(v) => set("tsq_remote_root", v)} placeholder="/opt/testrun" /></Field>
      <Field label="SSH user" help={help(["all", "vars", "ansible_user"])}><Text value={vars.ansible_user} onChange={(v) => set("ansible_user", v)} /></Field>
      <Field label="SSH key" help={help(["all", "vars", "ansible_ssh_private_key_file"])}><Text mono value={vars.ansible_ssh_private_key_file} onChange={(v) => set("ansible_ssh_private_key_file", v)} /></Field>
      <Field label="Type library folders" help={help(["all", "vars", "tsq_types"])}>
        <Text mono value={(Array.isArray(vars.tsq_types) ? vars.tsq_types : vars.tsq_types ? [vars.tsq_types] : []).join(", ")}
          onChange={(v) => set("tsq_types", v ? v.split(/\s*,\s*/).filter(Boolean) : undefined)} placeholder="types/" />
      </Field>
      <Field label="Other variables" help="Any other variable is kept, and reachable in templates as `{{ component.vars.<name> }}`." wide>
        <KeyValues value={vars} onChange={(v) => update((d) => setAt(d, ["all", "vars"], v))} known={knownVars(schemas, ["all", "vars"])}
          exclude={["tsq_type", "tsq_transport", "tsq_remote_root", "ansible_user", "ansible_ssh_private_key_file", "tsq_types"]} />
      </Field>
    </div>
  );
}

function TypeCard({ name }: { name?: string | null }) {
  const { ctx } = useSession();
  const t = name ? ctx.lab.types[name] : null;
  if (!t) return null;
  return (
    <div className="type-card">
      <div><strong>{name}</strong> <span className="muted small">{t.chain.join(" → ")}</span></div>
      {t.description && <Markdown text={t.description} />}
      <table className="grid compact">
        <tbody>
          {Object.entries<any>(t.commands).map(([c, info]) => (
            <tr key={c}><td><code>{c}</code></td><td className="mono small">{summary(info.spec)}</td><td className="muted small">{info.from}</td></tr>
          ))}
        </tbody>
      </table>
      <div className="small">state: {t.status ? `${t.status.spec.builtin ? `built-in ${t.status.spec.builtin}` : `probe ${summary(t.status.spec.probe || {})}`} (from ${t.status.from})` : "unknown (no status probe)"}</div>
      <div className="small">self-test: {t.selftest.map((c: any) => c.spec.id).join(", ") || "none"}</div>
    </div>
  );
}

function GroupForm({ inv, group, update, onSelect, onAdd }: {
  inv: Inv; group: GroupInfo; update: (fn: (d: Inv) => void) => void; onSelect: (s: Selection) => void; onAdd: () => void;
}) {
  const { ctx, schemas } = useSession();
  const help = useInvHelp();
  const varsPath = [...group.path, "vars"];
  const set = (k: string, v: any) => update((d) => setAt(d, [...varsPath, k], v === "" ? undefined : v));
  const otherHosts = allHosts(inv).filter((h) => !group.hosts.includes(h));
  const rename = (to: string) => {
    if (!to || to === group.name) return;
    update((d) => {
      const parent = getAt(d, group.path.slice(0, -1));
      const entries = Object.entries(parent);
      for (const k of Object.keys(parent)) delete parent[k];
      for (const [k, v] of entries) parent[k === group.name ? to : k] = v;
    });
    onSelect({ kind: "group", name: to });
  };
  const remove = () => {
    if (!window.confirm(`Delete ${group.stage ? "stage" : "group"} ${group.name}? Its components stay only if another group or all.hosts lists them.`)) return;
    update((d) => setAt(d, group.path, undefined));
    onSelect({ kind: "defaults" });
  };
  const hostsPath = [...group.path, "hosts"];
  return (
    <div>
      <div className="form-title">
        <h3>{group.stage ? "Stage" : "Group"} <InlineName value={group.name} onChange={rename} /></h3>
        <button className="small danger" onClick={remove}>Delete</button>
      </div>
      {group.stage && (
        <Field label="Stage type" help={help(["all", "children", "_", "vars", "tsq_stage_type"])}>
          <Combo value={group.vars.tsq_stage_type} onChange={(v) => set("tsq_stage_type", v)} choices={stageTypeChoices(ctx)} />
        </Field>
      )}
      <Section title={`Components (${group.hosts.length})`} help={helpText(schemas.inventory, ["all", "children", "_", "hosts"])}
        actions={<button className="small" onClick={onAdd}>+ New component</button>}>
        <table className="grid compact">
          <thead><tr><th>Component</th><th>Role</th><th>Type</th><th>Address</th><th>Lock</th><th /></tr></thead>
          <tbody>
            {group.hosts.map((h) => {
              const v = hostVars(inv, h);
              const shared = hostPlaces(inv, h).length > 1;
              return (
                <tr key={h} className="clickable" onClick={() => onSelect({ kind: "host", name: h })}>
                  <td><strong>{h}</strong>{shared && <span className="badge info" title="listed in several groups: a shared component">shared</span>}</td>
                  <td>{v.tsq_role || <span className="error-text">no role</span>}</td><td><code>{v.tsq_type || inv?.all?.vars?.tsq_type || "linux_system"}</code></td>
                  <td>{v.ansible_host || h}</td><td>{v.tsq_lock || "exclusive"}</td>
                  <td><button className="small" onClick={(e) => {
                    e.stopPropagation();
                    update((d) => setAt(d, [...hostsPath, h], undefined));
                  }} title="Take it out of this group (it stays where else it is listed)">Remove</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {otherHosts.length > 0 && (
          <div className="add-row">
            <Combo value="" onChange={() => {}} placeholder="add an existing component (shared with its other groups)…"
              choices={otherHosts.map((h) => ({ value: h, description: `${hostVars(inv, h).tsq_role || ""} ${hostVars(inv, h).tsq_type || ""}` }))}
              onPick={(h) => update((d) => setAt(d, [...hostsPath, h], {}))} />
          </div>
        )}
      </Section>
      <CommandsEditor title="Command overrides for the whole stage (tsq_commands)" help={help(["all", "vars", "tsq_commands"])}
        value={group.vars.tsq_commands} onChange={(v) => set("tsq_commands", Object.keys(v).length ? v : undefined)} />
      <Section title="Stage variables" help="Variables of every component of the group (host variables win).">
        <KeyValues value={group.vars} onChange={(v) => update((d) => setAt(d, varsPath, v))} known={knownVars(schemas, ["all", "children", "_", "vars"], ["tsq_stage_type", "tsq_commands"])}
          exclude={["tsq_stage_type", "tsq_commands"]} />
      </Section>
    </div>
  );
}

function InlineName({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [v, setV] = useState(value);
  return <input className="inline-name" value={v} onChange={(e) => setV(e.target.value.replace(/[^\w.-]/g, "_"))} onBlur={() => onChange(v)} onKeyDown={(e) => e.key === "Enter" && onChange(v)} key={value} />;
}

function HostForm({ inv, name, update, onRenamed, onDeleted }: {
  inv: Inv; name: string; update: (fn: (d: Inv) => void) => void; onRenamed: (n: string) => void; onDeleted: () => void;
}) {
  const { ctx, schemas } = useSession();
  const help = useInvHelp();
  const setHelp = useHelp();
  const path = hostVarsPath(inv, name)!;
  const vars = getAt(inv, path) || {};
  const stagesOf = groups(inv).filter((g) => g.stage && g.hosts.includes(name));
  const stageType = stagesOf[0]?.vars.tsq_stage_type;
  const effective = (k: string) => vars[k] ?? stagesOf[0]?.vars?.[k] ?? inv?.all?.vars?.[k];
  const [reach, setReach] = useState<any>(null);
  const set = (k: string, v: any) => update((d) => {
    const current = getAt(d, path);
    if (current == null || typeof current !== "object") setAt(d, path, {});
    setAt(d, [...path, k], v === "" ? undefined : v);
  });
  const rename = (to: string) => {
    if (!to || to === name) return;
    if (allHosts(inv).includes(to)) return alert(`${to} already exists`);
    update((d) => {
      for (const p of hostPlaces(d, name)) {
        const parent = getAt(d, p.slice(0, -1));
        const entries = Object.entries(parent);
        for (const k of Object.keys(parent)) delete parent[k];
        for (const [k, v] of entries) parent[k === name ? to : k] = v;
      }
    });
    onRenamed(to);
  };
  const remove = () => {
    if (!window.confirm(`Delete component ${name} from the inventory (from every group)?`)) return;
    update((d) => hostPlaces(d, name).forEach((p) => setAt(d, p, undefined)));
    onDeleted();
  };
  const check = async () => {
    setReach({ detail: "checking…" });
    const transport = effective("tsq_transport") || "ssh";
    const r = await attempt(() => post("edit/reachability", { host: effective("ansible_host") || name, port: effective("ansible_port") || 22, transport }));
    setReach(r || null);
  };
  const type = effective("tsq_type") || "linux_system";
  const lab = ctx.lab.types[type];
  return (
    <div>
      <div className="form-title">
        <h3>Component <InlineName value={name} onChange={rename} /></h3>
        <span className="muted">{stagesOf.length ? `in stage ${stagesOf.map((s) => s.name).join(", ")}` : "ad hoc"}</span>
        <button className="small danger" onClick={remove}>Delete</button>
      </div>
      <Field label="Label" help={help(["all", "hosts", "_", "tsq_label"])}><Text value={vars.tsq_label} onChange={(v) => set("tsq_label", v)} placeholder={name} /></Field>
      <Field label="Address" help={help(["all", "hosts", "_", "ansible_host"])} hint={reach && <span className={reach.ok ? "ok-text" : reach.ok === false ? "error-text" : "muted"}>{reach.detail}</span>}>
        <div className="with-button">
          <Text mono value={vars.ansible_host} onChange={(v) => set("ansible_host", v)} placeholder={effective("ansible_host") || name} />
          <button className="small" onClick={check} title="Whether the host answers on its SSH port (or is local)">Check reachability</button>
        </div>
      </Field>
      <Field label="Type" help={help(["all", "hosts", "_", "tsq_type"])}>
        <Combo value={vars.tsq_type} onChange={(v) => set("tsq_type", v)} choices={typeChoices(ctx)} strict placeholder={`${type} (inherited)`}
          onPick={(v) => setHelp({ title: v, text: ctx.lab.types[v]?.description, extra: <TypeCard name={v} /> })} />
      </Field>
      <Field label="Role" help={help(["all", "hosts", "_", "tsq_role"])}>
        <Combo value={vars.tsq_role} onChange={(v) => set("tsq_role", v)} choices={roleChoices(ctx, stageType ? { stageType } : { adhoc: true })} />
      </Field>
      <Field label="Lock" help={help(["all", "hosts", "_", "tsq_lock"])}>
        <Choices value={vars.tsq_lock} onChange={(v) => set("tsq_lock", v ?? undefined)} allowNone="exclusive (default)" choices={[
          { value: "shared", description: "several datapacks at once: a passive switch" },
          { value: "none", description: "never locked: the sequencer host" },
        ]} />
      </Field>
      <Field label="Transport" help={help(["all", "hosts", "_", "tsq_transport"])}>
        <Choices value={vars.tsq_transport} onChange={(v) => set("tsq_transport", v ?? undefined)} choices={transportChoices(ctx)} allowNone={`inherited (${effective("tsq_transport") || lab?.transport || "ssh"})`} />
      </Field>
      <Field label="Remote root" help={help(["all", "hosts", "_", "tsq_remote_root"])}>
        <Text mono value={vars.tsq_remote_root} onChange={(v) => set("tsq_remote_root", v)} placeholder={effective("tsq_remote_root") || "home folder"} />
      </Field>
      <Section title="What its type gives it" collapsed={false}>
        <TypeCard name={type} />
      </Section>
      <CommandsEditor title="Command overrides for this host (tsq_commands)" help={help(["all", "vars", "tsq_commands"])}
        value={vars.tsq_commands} onChange={(v) => set("tsq_commands", Object.keys(v).length ? v : undefined)}
        inherited={Object.fromEntries(Object.entries<any>(lab?.commands || {}).map(([k, v]) => [k, { spec: v.spec, from: `type ${v.from}` }]))} />
      <Section title="Other variables" help="Any variable: `ansible_user`, `ansible_port`, or your own, reachable as `{{ component.vars.<name> }}` in commands.">
        <KeyValues value={vars} onChange={(v) => update((d) => setAt(d, path, v))} known={knownVars(schemas, ["all", "hosts", "_"])}
          exclude={["tsq_label", "ansible_host", "tsq_type", "tsq_role", "tsq_lock", "tsq_transport", "tsq_remote_root", "tsq_commands"]} />
      </Section>
    </div>
  );
}

function NewStage({ inv, onClose, onCreate }: { inv: Inv; onClose: () => void; onCreate: (draft: Inv, name: string) => void }) {
  const { ctx } = useSession();
  const stages = groups(inv).filter((g) => g.stage);
  const taken = groups(inv).map((g) => g.name);
  const [copyFrom, setCopyFrom] = useState<string>(stages[0]?.name || "");
  const source = stages.find((s) => s.name === copyFrom);
  const [name, setName] = useState(source ? nextName(source.name, taken) : "stage_1");
  const [type, setType] = useState(source?.vars.tsq_stage_type || stageTypeChoices(ctx)[0]?.value || "");
  const hosts = allHosts(inv);
  const plan = (source?.hosts || []).map((h) => {
    const v = hostVars(inv, h);
    const shared = v.tsq_lock === "shared" || hostPlaces(inv, h).length > 1;
    return { from: h, to: shared ? h : nextName(h, hosts), shared, role: v.tsq_role };
  });
  const [names, setNames] = useState<Record<string, string>>({});
  const create = () => {
    const draft = clone(inv);
    draft.all ||= {};
    draft.all.children ||= {};
    const group: any = { vars: { ...(source ? clone(source.vars) : {}), tsq_stage_type: type }, hosts: {} };
    for (const p of plan) {
      const to = names[p.from] || p.to;
      if (p.shared) group.hosts[to] = {};
      else {
        const v = clone(hostVars(inv, p.from));
        delete v.ansible_host;
        group.hosts[to] = v;
      }
    }
    draft.all.children[name] = group;
    onCreate(draft, name);
  };
  return (
    <Dialog title="New stage" onClose={onClose} footer={<button className="primary" disabled={!name || taken.includes(name) || !type} onClick={create}>Create the stage</button>}>
      <Field label="Copy the components of" help="A new stage is usually a second copy of an existing one: same roles and types, other hosts. Shared components (lock `shared`, or already in several stages) are shared again, the others are copied under a new name, without their address.">
        <Choices value={copyFrom} onChange={(v) => {
          setCopyFrom(v || "");
          const s = stages.find((x) => x.name === v);
          if (s) {
            setName(nextName(s.name, taken));
            setType(s.vars.tsq_stage_type);
          }
        }} allowNone="nothing (empty stage)" choices={stages.map((s) => ({ value: s.name, description: `${s.vars.tsq_stage_type}: ${s.hosts.join(", ")}` }))} />
      </Field>
      <Field label="Name" error={taken.includes(name) ? "already used" : null}><Text value={name} onChange={(v) => setName(slug(v))} /></Field>
      <Field label="Stage type" help="What datapacks ask for (`stages: {train: {type: ...}}`)."><Combo value={type} onChange={setType} choices={stageTypeChoices(ctx)} /></Field>
      {plan.length > 0 && (
        <table className="grid compact">
          <thead><tr><th>From</th><th>Role</th><th>New component</th></tr></thead>
          <tbody>
            {plan.map((p) => (
              <tr key={p.from}><td>{p.from}</td><td>{p.role}</td>
                <td>{p.shared ? <span>{p.to} <span className="badge info">shared</span></span> : <input value={names[p.from] ?? p.to} onChange={(e) => setNames({ ...names, [p.from]: slug(e.target.value) })} />}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {plan.length > 0 && <p className="muted small">Give each new component its address afterwards (Address field), then check its reachability.</p>}
    </Dialog>
  );
}

function NewHost({ inv, stage, onClose, onCreate }: { inv: Inv; stage: string | null; onClose: () => void; onCreate: (draft: Inv, name: string) => void }) {
  const { ctx } = useSession();
  const group = stage ? groups(inv).find((g) => g.name === stage) : null;
  const stageType = group?.vars.tsq_stage_type;
  const present = (group?.hosts || []).map((h) => hostVars(inv, h).tsq_role);
  const roles = roleChoices(ctx, stageType ? { stageType } : { adhoc: true }).filter((r) => !present.includes(r.value));
  const [role, setRole] = useState(roles[0]?.value || "");
  // the type other components with this role have
  const typeFor = (r: string) => (stageType ? ctx.lab.stage_type_roles?.[stageType]?.[r] : ctx.lab.adhoc_roles?.[r]) || "";
  const [type, setType] = useState(typeFor(roles[0]?.value || ""));
  const [name, setName] = useState(freeName(slug(roles[0]?.value || "component"), allHosts(inv)));
  const [host, setHost] = useState("");
  const [transport, setTransport] = useState<string | null>(null);
  const taken = allHosts(inv);
  const create = () => {
    const draft = clone(inv);
    const vars: any = {};
    if (host) vars.ansible_host = host;
    if (type) vars.tsq_type = type;
    if (role) vars.tsq_role = role;
    if (transport) vars.tsq_transport = transport;
    if (group) setAt(draft, [...group.path, "hosts", name], vars);
    else setAt(draft, ["all", "hosts", name], vars);
    onCreate(draft, name);
  };
  return (
    <Dialog title={stage ? `New component in stage ${stage}` : "New ad hoc component"} onClose={onClose}
      footer={<button className="primary" disabled={!name || taken.includes(name)} onClick={create}>Create the component</button>}>
      <Field label="Role" help={stage ? `Roles the ${stageType} stages have, and those the tests ask for. Unique in the stage.` : "Ad hoc components are bound by role alone: `sequencer` for the analysis on the sequencer host, a simulation…"}>
        <Combo value={role} onChange={setRole} choices={roles} onPick={(r) => {
          setRole(r);
          if (typeFor(r)) setType(typeFor(r));
          setName(freeName(slug(r), taken));
        }} />
      </Field>
      <Field label="Type" help="Commands, status probe and self-test come from it."><Combo value={type} onChange={setType} choices={typeChoices(ctx)} strict /></Field>
      <Field label="Name" error={taken.includes(name) ? "already used" : null}><Text value={name} onChange={(v) => setName(slug(v))} /></Field>
      <Field label="Address" help="IP address or host name (leave empty for localhost with the local transport)."><Text mono value={host} onChange={setHost} /></Field>
      <Field label="Transport"><Choices value={transport} onChange={setTransport} choices={transportChoices(ctx)} allowNone="inherited" /></Field>
    </Dialog>
  );
}

export { VAR_KEYS };
