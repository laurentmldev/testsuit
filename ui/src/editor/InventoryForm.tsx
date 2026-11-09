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

// Lab inventory form (sequencer README, sections 6 and 13.2): stages, their components, ad hoc components
// and their variables, with the roles, components and stage types of the lab and the tests at hand.
import { useMemo, useState, useEffect } from "react";
import { post } from "../api";
import { Choices, Combo, Dialog, Field, helpText, KeyValues, Section, Tags, Text, useHelp, type Choice } from "../fields";
import { attempt, classes, freeName, labId, labIdInput, labIdProblem, Markdown } from "../util";
import { CommandsEditor, summary } from "./commands";
import { clone, componentChoices, labTagChoices, roleChoices, stageTypeChoices, transportChoices, useSession, type Doc } from "./state";
import { SelftestEditor, StatusEditor } from "./LibraryForm";
import { ItemLink, ItemLinks, extendsKind, useItemRequest } from "./links";

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
  useItemRequest(doc.path, (kind, name) => {
    if (kind === "host" && allHosts(inv).includes(name)) setSel({ kind: "host", name });
  });
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
                <span title={h}>{hostLabel(inv, h)}</span> <span className="muted small">{hostRole(ctx, inv, h) || "no role"}</span>
              </div>
            ))}
            <div className="nav-item sub add" onClick={() => setWizard({ kind: "member", stage: s.name })}>+ component</div>
          </div>
        ))}
        <div className="nav-title">Ad hoc components <button className="small" onClick={() => setWizard({ kind: "adhoc" })}>+ Component</button></div>
        {adhoc.map((h) => (
          <div key={h} className={classes("nav-item", sel.kind === "host" && sel.name === h && "selected")} onClick={() => setSel({ kind: "host", name: h })}>
            <span title={h}>{hostLabel(inv, h)}</span> <span className="muted small">{hostRole(ctx, inv, h) || "no role"}</span>
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

/** The role of a host: its tsq_role, else the one the lab resolved (the role of its component). */
function hostRole(ctx: any, inv: Inv, host: string): string {
  return hostVars(inv, host).tsq_role || ctx.lab.components?.[host]?.role || "";
}

/** What lists show for a host: its label (tsq_label) when it has one, else its id. */
function hostLabel(inv: Inv, host: string): string {
  return hostVars(inv, host).tsq_label || host;
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
  const roleIn = (stage: GroupInfo) => stage.hosts.map((h) => hostRole(ctx, inv, h));
  const staged = new Set(gs.filter((g) => g.stage).flatMap((s) => s.hosts));
  const adhocRoles = Object.keys(inv?.all?.hosts || {}).filter((h) => !staged.has(h)).map((h) => hostRole(ctx, inv, h));
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

const VAR_KEYS = ["ansible_host", "ansible_user", "ansible_port", "ansible_ssh_private_key_file", "tsq_role", "tsq_lock",
  "tsq_label", "tsq_description", "tsq_tags", "tsq_transport", "tsq_remote_root", "tsq_shell", "tsq_ssh_host_key", "tsq_extends", "tsq_commands", "tsq_status", "tsq_selftest", "tsq_collect", "tsq_stage_type",
  "tsq_library"];

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
      <Field label="Lab description" help={help(["all", "vars", "tsq_description"])}>
        <Text multiline value={vars.tsq_description} onChange={(v) => set("tsq_description", v)} placeholder="What this lab is (its own: stages and components have theirs)" />
      </Field>
      <Field label="Transport" help={help(["all", "vars", "tsq_transport"])}>
        <Choices value={vars.tsq_transport} onChange={(v) => set("tsq_transport", v ?? undefined)} choices={transportChoices(ctx)} allowNone="default (ssh)" />
      </Field>
      <Field label="Remote root" help={help(["all", "vars", "tsq_remote_root"])}><Text mono value={vars.tsq_remote_root} onChange={(v) => set("tsq_remote_root", v)} placeholder="/opt/testrun" /></Field>
      <Field label="Shell" help={help(["all", "vars", "tsq_shell"])}><Text mono value={vars.tsq_shell} onChange={(v) => set("tsq_shell", v)} placeholder="default: sh, bash, bash -l…" /></Field>
      <Field label="SSH user" help={help(["all", "vars", "ansible_user"])}><Text value={vars.ansible_user} onChange={(v) => set("ansible_user", v)} /></Field>
      <Field label="SSH key" help={help(["all", "vars", "ansible_ssh_private_key_file"])}><Text mono value={vars.ansible_ssh_private_key_file} onChange={(v) => set("ansible_ssh_private_key_file", v)} /></Field>
      <Field label="Library folders" help={help(["all", "vars", "tsq_library"])}>
        <Text mono value={(Array.isArray(vars.tsq_library) ? vars.tsq_library : vars.tsq_library ? [vars.tsq_library] : []).join(", ")}
          onChange={(v) => set("tsq_library", v ? v.split(/\s*,\s*/).filter(Boolean) : undefined)} placeholder="roles/, components/" />
      </Field>
      <Field label="Other variables" help="Any other variable is kept, and reachable in templates as `{{ component.vars.<name> }}`." wide>
        <KeyValues value={vars} onChange={(v) => update((d) => setAt(d, ["all", "vars"], v))} known={knownVars(schemas, ["all", "vars"])}
          exclude={["tsq_transport", "tsq_remote_root", "tsq_shell", "ansible_user", "ansible_ssh_private_key_file", "tsq_library", "tsq_description"]} />
      </Field>
    </div>
  );
}

/** A lab role: the commands it offers, with what each does and the role listing it. */
function RoleCard({ name }: { name?: string | null }) {
  const { ctx } = useSession();
  const r = name ? ctx.lab.roles?.[name] : null;
  if (!r) return null;
  return (
    <div className="type-card">
      <div><strong>role {name}</strong> <span className="muted small">{r.chain.join(" → ")}</span></div>
      {r.description && <Markdown text={r.description} />}
      <table className="grid compact">
        <tbody>
          {Object.entries<any>(r.commands).map(([c, info]) => (
            <tr key={c}><td><code>{c}</code></td><td className="small">{info.description}</td><td className="muted small">{info.from}</td></tr>
          ))}
        </tbody>
      </table>
      {!Object.keys(r.commands).length && <div className="small muted">no command: only monitored</div>}
    </div>
  );
}

/** A lab component of the library (or a host): what it implements, its status probe and self-test. */
function ComponentCard({ name }: { name?: string | null }) {
  const { ctx } = useSession();
  const c = name ? ctx.lab.library?.[name] : null;
  if (!c) return null;
  return (
    <div className="type-card">
      <div><strong>component {name}</strong> <span className="muted small">{c.chain.join(" → ")}</span></div>
      {c.description && <Markdown text={c.description} />}
      <table className="grid compact">
        <tbody>
          {Object.entries<any>(c.commands).map(([cmd, info]) => (
            <tr key={cmd}><td><code>{cmd}</code></td><td className="mono small">{summary(info.spec)}</td><td className="muted small">{info.from}</td></tr>
          ))}
        </tbody>
      </table>
      <div className="small">state: {c.status ? `${c.status.spec.builtin ? `built-in ${c.status.spec.builtin}` : `probe ${summary(c.status.spec.probe || {})}${c.status.spec.via ? ` via ${c.status.spec.via}` : ""}`} (from ${c.status.from})` : "unknown (no status probe)"}</div>
      <div className="small">self-test: {c.selftest.map((x: any) => x.spec.id).join(", ") || "none"}</div>
    </div>
  );
}

/** A host as the lab resolves it: each command of its role with the implementation used and where it comes from,
 * the commands of its role it does not implement, and those it implements that its role does not list. */
function HostCard({ name }: { name: string }) {
  const { ctx } = useSession();
  const h = ctx.lab.components?.[name];
  if (!h) return <p className="muted small">Saved hosts are resolved here.</p>;
  if (h.error) return <div className="error-text">{h.error}</div>;
  const role = ctx.lab.roles?.[h.role]?.commands || {};
  return (
    <div className="type-card">
      <div><span className="muted small">{h.chain.join(" → ")}</span></div>
      {h.missing?.length > 0 && <div className="error-text">Does not implement {h.missing.map((c: string) => <code key={c}>{c}</code>)}, listed by its role {h.role}: the lab does not load.</div>}
      {h.extra?.length > 0 && <div className="warning-text">Implements {h.extra.map((c: string) => <code key={c}>{c}</code>)}, which its role does not list: datapacks cannot use them.</div>}
      <table className="grid compact">
        <tbody>
          {Object.entries<any>(h.commands).map(([c, info]) => (
            <tr key={c} title={role[c]?.description}><td><code>{c}</code></td><td className="mono small">{summary(info.spec)}</td><td className="muted small">{info.from}</td></tr>
          ))}
        </tbody>
      </table>
      <div className="small">state: {h.status ? (h.status.builtin ? `built-in ${h.status.builtin}` : `probe ${summary(h.status.probe || {})}${h.status.via ? ` via ${h.status.via}` : ""}`) : "unknown (no status probe)"}</div>
      <div className="small">self-test: {h.selftest?.join(", ") || "none"}</div>
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
          <Combo value={group.vars.tsq_stage_type} onChange={(v) => set("tsq_stage_type", v ? labIdInput(v) : v)} choices={stageTypeChoices(ctx)} />
        </Field>
      )}
      <Field label="Description" help={help(["all", "children", "_", "vars", "tsq_description"])}>
        <Text multiline value={getAt(inv, varsPath)?.tsq_description} onChange={(v) => set("tsq_description", v)} placeholder={`What this ${group.stage ? "stage" : "group"} is (its own: its components have theirs)`} />
      </Field>
      {group.stage && (
        <Field label="Tags" help={help(["all", "children", "_", "vars", "tsq_tags"])} hint={<>its id <code>{group.name}</code> is always a tag</>}>
          <Tags value={getAt(inv, varsPath)?.tsq_tags} onChange={(v) => set("tsq_tags", v.length ? v : undefined)} choices={labTagChoices(ctx, "stages")} placeholder="+ tag" />
        </Field>
      )}
      <Section title={`Components (${group.hosts.length})`} help={helpText(schemas.inventory, ["all", "children", "_", "hosts"])}
        actions={<button className="small" onClick={onAdd}>+ New component</button>}>
        <table className="grid compact">
          <thead><tr><th>Component</th><th>Role</th><th>Extends</th><th>Address</th><th>Lock</th><th /></tr></thead>
          <tbody>
            {group.hosts.map((h) => {
              const v = hostVars(inv, h);
              const shared = hostPlaces(inv, h).length > 1;
              return (
                <tr key={h} className="clickable" onClick={() => onSelect({ kind: "host", name: h })}>
                  <td><strong title={h}>{v.tsq_label || h}</strong>{v.tsq_label && <span className="muted small"> {h}</span>}{shared && <span className="badge info" title="listed in several groups: a shared component">shared</span>}</td>
                  <td>{(v.tsq_role || ctx.lab.components?.[h]?.role) ? <ItemLink kind="role" name={v.tsq_role || ctx.lab.components[h].role} /> : <span className="error-text">no role</span>}{v.tsq_role && !ctx.lab.roles?.[v.tsq_role] && <span className="error-text small"> not a lab role</span>}{ctx.lab.components?.[h]?.missing?.length > 0 && <span className="error-text small"> missing {ctx.lab.components[h].missing.join(", ")}</span>}</td>
                  <td><ExtendsLink name={v.tsq_extends || ctx.lab.components?.[h]?.extends || "linux_system"} /></td>
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
              choices={otherHosts.map((h) => ({ value: h, description: [hostVars(inv, h).tsq_label, hostRole(ctx, inv, h)].filter(Boolean).join(" · ") }))}
              onPick={(h) => update((d) => setAt(d, [...hostsPath, h], {}))} />
          </div>
        )}
      </Section>
      <CommandsEditor title="Command overrides for the whole stage (tsq_commands)" help={help(["all", "vars", "tsq_commands"])}
        value={group.vars.tsq_commands} onChange={(v) => set("tsq_commands", Object.keys(v).length ? v : undefined)} />
      <Section title="Stage variables" help="Variables of every component of the group (host variables win).">
        <KeyValues value={group.vars} onChange={(v) => update((d) => setAt(d, varsPath, v))} known={knownVars(schemas, ["all", "children", "_", "vars"], ["tsq_stage_type", "tsq_commands"])}
          exclude={["tsq_stage_type", "tsq_commands", "tsq_description", "tsq_tags"]} />
      </Section>
    </div>
  );
}

function InlineName({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  // an empty or taken name shows red and is not applied
  const done = () => (labIdProblem(v) ? setV(value) : onChange(v));
  return <input className={classes("inline-name", labIdProblem(v) && "invalid")} value={v} onChange={(e) => setV(labIdInput(e.target.value))} onBlur={done} onKeyDown={(e) => e.key === "Enter" && done()} key={value} />;
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
  const ext = effective("tsq_extends") || "";
  const host = ctx.lab.components?.[name];
  const supported: string[] = host?.role_choices ?? (ext ? ctx.lab.library?.[ext]?.roles : null) ?? [];
  const role = effective("tsq_role") || (supported.length === 1 ? supported[0] : "") || host?.role || "";
  // what the component it extends gives (a library component, or another host)
  const base = ext ? (ctx.lab.library?.[ext] || ctx.lab.components?.[ext]) : ctx.lab.library?.linux_system;
  const baseName = ext || "linux_system";
  const extendedBy = Object.keys(ctx.lab.components || {}).filter((h) => ctx.lab.components[h]?.extends === name);
  const inherited = Object.fromEntries(Object.entries<any>(base?.commands || {}).map(([k, v]) => [k, { spec: v.spec, from: v.from }]));
  const required = Object.fromEntries(Object.entries<any>(ctx.lab.roles?.[role]?.commands || {}).map(([k, v]) => [k, { description: v.description, from: v.from }]));
  return (
    <div>
      <div className="form-title">
        <h3>Component <InlineName value={name} onChange={rename} /></h3>
        <span className="muted">{stagesOf.length ? `in stage ${stagesOf.map((s) => s.name).join(", ")}` : "ad hoc"}</span>
        <button className="small danger" onClick={remove}>Delete</button>
      </div>
      <Field label="Label" help={help(["all", "hosts", "_", "tsq_label"])}><Text value={vars.tsq_label} onChange={(v) => set("tsq_label", v)} placeholder={name} /></Field>
      <Field label="Description" help={help(["all", "hosts", "_", "tsq_description"])}><Text multiline value={vars.tsq_description} onChange={(v) => set("tsq_description", v)} placeholder="What this component is (its own: the stage's and the lab's are not inherited)" /></Field>
      <Field label="Tags" help={help(["all", "hosts", "_", "tsq_tags"])} hint={<>its id <code>{name}</code> is always a tag</>}>
        <Tags value={vars.tsq_tags} onChange={(v) => set("tsq_tags", v.length ? v : undefined)} choices={labTagChoices(ctx, "components")} placeholder="+ tag" />
      </Field>
      <Field label="Address" help={help(["all", "hosts", "_", "ansible_host"])} hint={reach && <span className={reach.ok ? "ok-text" : reach.ok === false ? "error-text" : "muted"}>{reach.detail}</span>}>
        <div className="with-button">
          <Text mono value={vars.ansible_host} onChange={(v) => set("ansible_host", v)} placeholder={effective("ansible_host") || name} />
          <button className="small" onClick={check} title="Whether the host answers on its SSH port (or is local)">Check reachability</button>
        </div>
      </Field>
      <Field label="Extends" help={help(["all", "hosts", "_", "tsq_extends"])} hint={<>edit <ExtendsLink name={baseName} />{extendedBy.length > 0 && <>{" · "}extended by <ItemLinks kind="host" names={extendedBy} /></>}</>}>
        <Combo value={vars.tsq_extends} onChange={(v) => set("tsq_extends", v)} choices={componentChoices(ctx, { hosts: true, filter: (n) => n !== name })} strict
          placeholder={ext ? `${ext} (inherited)` : "linux_system (default)"}
          onPick={(v) => setHelp({ title: v, text: ctx.lab.library?.[v]?.description, extra: <ComponentCard name={v} /> })} />
      </Field>
      <Field label="Role" help={help(["all", "hosts", "_", "tsq_role"])}
        hint={<>{role && <>edit <ItemLink kind="role" name={role} />{" · "}</>}{supported.length === 1 ? `the role of its component: no tsq_role needed` : supported.length > 1 ? `its component supports ${supported.join(", ")}: pick one` : "its component lists no role: give one"}</>}>
        <Combo value={vars.tsq_role} onChange={(v) => set("tsq_role", v)} choices={supported.length ? roleChoices(ctx).filter((r) => supported.includes(r.value)) : roleChoices(ctx)} strict
          placeholder={role ? `${role} (${supported.length === 1 ? "from its component" : "inherited"})` : undefined}
          onPick={(v) => setHelp({ title: v, text: ctx.lab.roles?.[v]?.description, extra: <RoleCard name={v} /> })} />
      </Field>
      <Field label="Lock" help={help(["all", "hosts", "_", "tsq_lock"])}>
        <Choices value={vars.tsq_lock} onChange={(v) => set("tsq_lock", v ?? undefined)} allowNone="exclusive (default)" choices={[
          { value: "shared", description: "several datapacks at once: a passive switch" },
          { value: "none", description: "never locked: the sequencer host" },
        ]} />
      </Field>
      <Field label="Transport" help={help(["all", "hosts", "_", "tsq_transport"])}>
        <Choices value={vars.tsq_transport} onChange={(v) => set("tsq_transport", v ?? undefined)} choices={transportChoices(ctx)} allowNone={`inherited (${effective("tsq_transport") || base?.transport || "ssh"})`} />
      </Field>
      <Field label="Remote root" help={help(["all", "hosts", "_", "tsq_remote_root"])}>
        <Text mono value={vars.tsq_remote_root} onChange={(v) => set("tsq_remote_root", v)} placeholder={effective("tsq_remote_root") || "home folder"} />
      </Field>
      <Field label="Shell" help={help(["all", "hosts", "_", "tsq_shell"])}>
        <Text mono value={vars.tsq_shell} onChange={(v) => set("tsq_shell", v)} placeholder={effective("tsq_shell") ? `${effective("tsq_shell")} (inherited)` : "default: sh, bash, bash -l…"} />
      </Field>
      {(effective("tsq_transport") || base?.transport || "ssh") === "ssh" && (
        <Field label="SSH host key" help={help(["all", "hosts", "_", "tsq_ssh_host_key"])}
          hint={<>one per line; on the server: <code>cat /etc/ssh/ssh_host_ed25519_key.pub</code>, or <code>ssh-keygen -F {effective("ansible_host") || name}</code> where it is known</>}>
          <HostKeys value={vars.tsq_ssh_host_key} onChange={(v) => set("tsq_ssh_host_key", v)}
            inherited={vars.tsq_ssh_host_key == null ? effective("tsq_ssh_host_key") : undefined} />
        </Field>
      )}
      <Section title="Its commands, as the lab resolves them" collapsed={false}>
        <HostCard name={name} />
      </Section>
      <CommandsEditor title="Its own command implementations (tsq_commands)" help={help(["all", "vars", "tsq_commands"])}
        value={vars.tsq_commands} onChange={(v) => set("tsq_commands", Object.keys(v).length ? v : undefined)}
        inherited={inherited} required={role ? required : undefined} />
      <StatusEditor value={vars.tsq_status} onChange={(v) => set("tsq_status", v)} inherited={base?.status ? { spec: base.status.spec ?? base.status, from: base.status.from ?? baseName } : undefined} />
      <SelftestEditor value={vars.tsq_selftest} onChange={(v) => set("tsq_selftest", v.length ? v : undefined)} inherited={(base?.selftest || []).map((c: any) => (typeof c === "string" ? { spec: { id: c }, from: baseName } : c))} />
      <Section title="Other variables" help="Any variable: `ansible_user`, `ansible_port`, or your own, reachable as `{{ component.vars.<name> }}` in commands.">
        <KeyValues value={vars} onChange={(v) => update((d) => setAt(d, path, v))} known={knownVars(schemas, ["all", "hosts", "_"])}
          exclude={["tsq_label", "tsq_description", "tsq_tags", "ansible_host", "tsq_role", "tsq_extends", "tsq_lock", "tsq_transport", "tsq_remote_root", "tsq_shell", "tsq_ssh_host_key", "tsq_commands", "tsq_status", "tsq_selftest"]} />
      </Section>
    </div>
  );
}

/** Why an entry of tsq_ssh_host_key is not a public key or a SHA256 fingerprint ("" when it is), as the lab
 * loader checks it. */
export function hostKeyProblem(entry: string): string {
  const e = entry.trim();
  if (e.startsWith("SHA256:")) return /^SHA256:[A-Za-z0-9+/]{43}=?$/.test(e) ? "" : "not a SHA256 fingerprint, as ssh-keygen -lf prints it";
  const parts = e.split(/\s+/);
  const keyType = /^(ssh-|ecdsa-|sk-)[a-z0-9@.-]+$/;
  if (parts.length >= 3 && keyType.test(parts[1])) return `starts with a host name, as in known_hosts: keep from ${parts[1]} on`;
  if (parts.length < 2 || !keyType.test(parts[0])) return "neither a public key (ssh-ed25519 AAAA…) nor a SHA256: fingerprint";
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(parts[1])) return "the key data is not base64";
  return "";
}

/** The host keys (tsq_ssh_host_key) of a component: one per line, a string for one, a list for several. Saved
 * when the field loses focus, so new lines can be typed. */
function HostKeys({ value, onChange, inherited }: { value: any; onChange: (v: string | string[] | undefined) => void; inherited?: any }) {
  const text = (v: any) => (Array.isArray(v) ? v.join("\n") : v ?? "");
  const [draft, setDraft] = useState(text(value));
  useEffect(() => setDraft(text(value)), [JSON.stringify(value)]);
  const lines = draft.split("\n").map((l: string) => l.trim()).filter(Boolean);
  const problems = lines.map(hostKeyProblem).map((p: string, i: number) => (p ? `line ${i + 1}: ${p}` : "")).filter(Boolean);
  const commit = () => onChange(lines.length === 0 ? undefined : lines.length === 1 ? lines[0] : lines);
  return (
    <>
      <Text mono multiline value={draft} onChange={setDraft} onBlur={commit}
        placeholder={inherited ? `${text(inherited)} (inherited)` : "not pinned: the sequencer's known_hosts is checked"} />
      {problems.map((p: string) => <div key={p} className="error-text">{p}</div>)}
    </>
  );
}

function NewStage({ inv, onClose, onCreate }: { inv: Inv; onClose: () => void; onCreate: (draft: Inv, name: string) => void }) {
  const { ctx } = useSession();
  const stages = groups(inv).filter((g) => g.stage);
  const taken = groups(inv).map((g) => g.name);
  const [copyFrom, setCopyFrom] = useState<string>(stages[0]?.name || "");
  const source = stages.find((s) => s.name === copyFrom);
  const [name, setName] = useState(source ? nextName(source.name, taken) : freeName("stage_1", taken));
  const [type, setType] = useState(source?.vars.tsq_stage_type || stageTypeChoices(ctx)[0]?.value || "");
  const hosts = allHosts(inv);
  const plan = (source?.hosts || []).map((h) => {
    const v = hostVars(inv, h);
    const shared = v.tsq_lock === "shared" || hostPlaces(inv, h).length > 1;
    return { from: h, to: shared ? h : nextName(h, hosts), shared, role: v.tsq_role, extends: v.tsq_extends };
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
      <Field label="Copy the components of" help="A new stage is usually a second copy of an existing one: same roles and components, other hosts. Shared components (lock `shared`, or already in several stages) are shared again, the others are copied under a new name, without their address.">
        <Choices value={copyFrom} onChange={(v) => {
          setCopyFrom(v || "");
          const s = stages.find((x) => x.name === v);
          if (s) {
            setName(nextName(s.name, taken));
            setType(s.vars.tsq_stage_type);
          }
        }} allowNone="nothing (empty stage)" choices={stages.map((s) => ({ value: s.name, description: `${s.vars.tsq_stage_type}: ${s.hosts.join(", ")}` }))} />
      </Field>
      <Field label="Name" error={labIdProblem(name, taken)}><Text value={name} onChange={(v) => setName(labIdInput(v))} /></Field>
      <Field label="Stage type" help="What datapacks ask for (`stages: {train: {type: ...}}`)."><Combo value={type} onChange={(v) => setType(labIdInput(v))} choices={stageTypeChoices(ctx)} /></Field>
      {plan.length > 0 && (
        <table className="grid compact">
          <thead><tr><th>From</th><th>Role</th><th>Extends</th><th>New component</th></tr></thead>
          <tbody>
            {plan.map((p) => (
              <tr key={p.from}><td>{p.from}</td><td>{p.role}</td><td>{p.extends}</td>
                <td>{p.shared ? <span>{p.to} <span className="badge info">shared</span></span> : <input value={names[p.from] ?? p.to} className={classes(labIdProblem(names[p.from] ?? p.to) && "invalid")} onChange={(e) => setNames({ ...names, [p.from]: labIdInput(e.target.value) })} />}</td></tr>
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
  const present = (group?.hosts || []).map((h) => hostRole(ctx, inv, h));
  // the roles the stages of this type (or the ad hoc components) already have first, then every other lab role
  const usual = roleChoices(ctx, stageType ? { stageType } : { adhoc: true }).filter((r) => ctx.lab.roles?.[r.value]);
  const roles = [...usual, ...roleChoices(ctx).filter((r) => !usual.some((u) => u.value === r.value))].filter((r) => !present.includes(r.value));
  const [role, setRoleState] = useState(usual.find((r) => !present.includes(r.value))?.value || "");
  // the component the other hosts with that role extend, as a start
  const usualExtends = (r: string) => Object.values<any>(ctx.lab.components || {}).find((c) => c.role === r)?.extends
    || ctx.lab.roles?.[r]?.implemented_by?.[0] || "";
  const [ext, setExt] = useState(usualExtends(role));
  const setRole = (r: string) => {
    setRoleState(r);
    setExt(usualExtends(r));
  };
  const [name, setName] = useState("");               // the user names the component (its inventory host name)
  const [host, setHost] = useState("");
  const [transport, setTransport] = useState<string | null>(null);
  const taken = allHosts(inv);
  const create = () => {
    const draft = clone(inv);
    const vars: any = {};
    if (host) vars.ansible_host = host;
    if (ext && ext !== "linux_system") vars.tsq_extends = ext;
    const supported: string[] = ctx.lab.library?.[ext]?.roles ?? ctx.lab.components?.[ext]?.role_choices ?? [];
    if (role && !(supported.length === 1 && supported[0] === role)) vars.tsq_role = role;   // else its component's
    if (transport) vars.tsq_transport = transport;
    if (group) setAt(draft, [...group.path, "hosts", name], vars);
    else setAt(draft, ["all", "hosts", name], vars);
    onCreate(draft, name);
  };
  return (
    <Dialog title={stage ? `New component in stage ${stage}` : "New ad hoc component"} onClose={onClose}
      footer={<button className="primary" disabled={!name || taken.includes(name)} onClick={create}>Create the component</button>}>
      <Field label="Role" help={(stage ? `A lab role: the roles the ${stageType} stages already have come first. Unique in the stage.` : "A lab role. Ad hoc components are bound by role alone: `sequencer` for the analysis on the sequencer host, a simulation…") + " It gives the commands the component must implement."}>
        <Combo value={role} onChange={setRole} choices={roles} strict />
        <RoleCard name={role} />
      </Field>
      <Field label="Extends" help="The lab component it is: it implements the commands of the role, and gives the status probe and the self-test. When it supports only this role, the host needs no tsq_role.">
        <Combo value={ext} onChange={setExt} choices={componentChoices(ctx, { hosts: true })} strict placeholder="linux_system (default)" />
        <ComponentCard name={ext} />
      </Field>
      <Field label="Id" help="The component's id in the lab (its inventory host name): what runs, logs and `tsq components` show. Letters, digits, `_`, `-` and `.`."
        error={name ? labIdProblem(name, taken) : null}>
        <Text value={name} onChange={(v) => setName(labIdInput(v))} placeholder={`for example ${freeName(labId(role || "component"), taken)}`} />
      </Field>
      <Field label="Address" help="IP address or host name (leave empty for localhost with the local transport)."><Text mono value={host} onChange={setHost} /></Field>
      <Field label="Transport"><Choices value={transport} onChange={setTransport} choices={transportChoices(ctx)} allowNone="inherited" /></Field>
    </Dialog>
  );
}

export { VAR_KEYS };

/** What an inventory component extends (a library component, or another inventory component), as a link. */
function ExtendsLink({ name }: { name: string }) {
  const session = useSession();
  return <ItemLink kind={extendsKind(session, name)} name={name} />;
}
