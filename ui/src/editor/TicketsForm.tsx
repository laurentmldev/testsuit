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

// Tickets form (sequencer README, section 10.6): tickets.yml, where the issues of the tests repository go. Its sources
// (a backend, its options and credentials, labels), the default one, the routes that pick the source of a new issue,
// and how an issue names the requirements it is about.
import { useState } from "react";
import { Combo, Field, IdText, KeyValues, Section, Tags, Text, Toggle, type Choice } from "../fields";
import { send } from "../api";
import { classes } from "../util";
import { Credentials } from "./RequirementsForm";
import { clone, useSession, type Doc } from "./state";

type Data = Record<string, any>;
const NAME = /^[A-Za-z_][A-Za-z0-9_-]*$/;
const ROUTE_KEYS: Choice[] = [
  { value: "testdef", description: "the test definition's name" },
  { value: "step", description: "the step, as <stage>:<step> (firmware:*, exploit:criteria)" },
  { value: "criteria", description: "the criteria file of a check (criteria/firmware/*)" },
  { value: "check", description: "the name of a check (speed_*)" },
];
const REQ_DEFAULTS: Data = { labels: "req:{id}", text: true, jira_links: true, add: true };

function pruned(obj: Data): Data {
  const out: Data = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)
      || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length)) continue;
    out[k] = v;
  }
  return out;
}

export function TicketsForm({ doc, api }: { doc: Doc; api: { setData: (d: any) => void } }) {
  const { ctx } = useSession();
  const data: Data = doc.data || {};
  const backends: any[] = ctx.ticket_backends || [];
  const sources: Data = data.sources || {};
  const names = Object.keys(sources);
  const defaultName = data.default || names[0];
  const setTop = (k: string, v: any) => {
    const next = clone(data);
    if (v === undefined || v === "" || (typeof v === "object" && v !== null && !Array.isArray(v) && !Object.keys(v).length)) delete next[k];
    else next[k] = v;
    api.setData(next);
  };
  const setSource = (n: string, spec: Data | undefined) => {
    const next = clone(sources);
    if (spec === undefined) delete next[n];
    else next[n] = pruned(spec);
    setTop("sources", next);
  };
  const [name, setName] = useState("");
  const [added, setAdded] = useState<string | null>(null);
  const bad = name && !NAME.test(name) ? "letters, digits, _ and - (the prefix of its references: name#42)" : name in sources ? "already a source" : null;
  const create = () => {
    if (!name || bad) return;
    setSource(name, { backend: backends[0]?.name || "gitlab" });
    setAdded(name);
    setName("");
  };
  return (
    <div className="form pad tickets-form">
      <Section title="Sources" help="Each source is one project of a tickets tool (or a GitLab group, a GitHub organization): where issues are read, and created. References name it: name#42 (#42 alone: the default source).">
        <div className="req-new">
          <strong>New source</strong>
          <input className="mono" placeholder="its name: the prefix of its references (bench, firmware, jira…)" value={name}
            onChange={(e) => setName(e.target.value.trim())} onKeyDown={(e) => e.key === "Enter" && create()} />
          <button className="primary" disabled={!name || !!bad} onClick={create}>+ Create source</button>
          {bad && <span className="bad-text small">{bad}</span>}
        </div>
        {names.length === 0 && <p className="muted">No source yet: failures cannot be reported nor bound to issues.</p>}
        {names.map((n) => (
          <TicketSource key={n} name={n} spec={sources[n] || {}} data={data} backends={backends} taken={names}
            isDefault={n === defaultName} startOpen={added === n || names.length === 1}
            onChange={(spec) => setSource(n, spec)}
            onRename={(to) => {
              const next = clone(data);
              next.sources = Object.fromEntries(Object.entries(sources).map(([k, v]) => [k === n ? to : k, v]));
              if (next.default === n) next.default = to;
              next.routes = (next.routes || []).map((r: Data) => (r?.source === n ? { ...r, source: to } : r));
              api.setData(next);
            }}
            onDefault={() => setTop("default", n === names[0] ? undefined : n)}
            onRemove={() => {
              const next = clone(data);
              delete next.sources[n];
              if (next.default === n) delete next.default;
              next.routes = (next.routes || []).filter((r: Data) => r?.source !== n);
              if (!next.routes.length) delete next.routes;
              api.setData(next);
            }} />
        ))}
        <p className="muted small">Backends available here: {backends.map((b) => b.name).join(", ") || "none"} (a plugin adds one
          with <code>register_ticket_backend</code>). Credentials are read on the sequencer host, from an environment variable or a file.</p>
      </Section>
      <Section title="Routes" help="Where a new issue goes: the first route whose patterns all match the failure (globs), else the default source.">
        <Routes value={data.routes || []} names={names} onChange={(v) => setTop("routes", v.length ? v : undefined)} />
      </Section>
      <Section title="Requirements" help="How an issue names the requirements it is about (besides those its failure verifies).">
        <ReqRules value={data.requirements} onChange={(v) => setTop("requirements", v)} />
      </Section>
    </div>
  );
}

function TicketSource({ name, spec, data, backends, taken, isDefault, startOpen, onChange, onRename, onDefault, onRemove }: {
  name: string; spec: Data; data: Data; backends: any[]; taken: string[]; isDefault: boolean; startOpen?: boolean;
  onChange: (spec: Data) => void; onRename: (to: string) => void; onDefault: () => void; onRemove: () => void;
}) {
  const { sid } = useSession();
  const [open, setOpen] = useState(!!startOpen);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const backend = backends.find((b) => b.name === String(spec.backend || "").toLowerCase());
  const options: Data = spec.options || {};
  const credentials: Data = spec.credentials || {};
  const set = (k: string, v: any) => onChange({ ...spec, [k]: v });
  const optionChoices: Choice[] = Object.entries<string>(backend?.options || {}).map(([k, d]) => ({ value: k, description: d }));
  const credRows = [...new Set([...Object.keys(credentials), ...Object.keys(backend?.credentials || {})])];
  const where = ["project", "repo", "group", "org"].map((k) => options[k]).find(Boolean);
  const test = async () => {
    setOpen(true);
    setTesting(true);
    setResult(null);
    try {
      setResult(await send("POST", `edit/sessions/${sid}/tickets/test`, { source: name, data }));
    } catch (e: any) {
      setResult({ error: String(e?.message || e) });
    } finally {
      setTesting(false);
    }
  };
  return (
    <div className={classes("req-source", isDefault && "default", !open && "folded")}>
      <div className="req-source-head">
        <button className="fold" onClick={() => setOpen(!open)} title={open ? "Fold it" : "Edit it"}>{open ? "▾" : "▸"}</button>
        <IdText value={name} taken={taken} onChange={onRename} />
        {!open && <span className="req-source-sum small muted" onClick={() => setOpen(true)}>
          <code>{spec.backend || "?"}</code>{where ? ` · ${where}` : ""}{spec.label ? ` · ${spec.label}` : ""}</span>}
        <label className="toggle" title="The source of references without a prefix (#42), and of new issues no route picks">
          <input type="radio" checked={isDefault} onChange={onDefault} /> default
        </label>
        <span className="grow" />
        <button className="small" disabled={testing} onClick={test}
          title="Read its open issues as in this form (saved or not): the credentials are read on the sequencer host">
          {testing ? "Testing…" : "Test connection"}</button>
        <button className="icon" title="Remove this source (and its routes)" onClick={onRemove}>×</button>
      </div>
      {open && <>
        {result && (
          <div className={classes("req-test", result.error ? "bad" : "ok")}>
            {result.error ? <div>{result.error}</div> : (
              <div>✓ {result.count} open issue(s) read in {result.seconds} s{result.issues?.length ? ":" : ""}
                {result.issues?.length > 0 && <ul className="small">{result.issues.map((i: any) => <li key={i.id}><code>{name}#{i.id}</code> {i.title}</li>)}</ul>}
                {result.projects?.length > 0 && <div className="small muted">Projects: {result.projects.join(", ")}</div>}
              </div>
            )}
            {result.calls?.length > 0 && (
              <details className="small"><summary>{result.calls.length} call(s) to the server</summary>
                {result.calls.map((x: any, i: number) => <div key={i} className="mono">{x.method} {x.url} → {x.status ?? x.error}</div>)}
              </details>
            )}
          </div>
        )}
        <Field label="Backend" help="The tickets tool: gitlab, github, jira, redmine, or a plugin's."
          error={spec.backend && !backend ? `no backend '${spec.backend}' here: a plugin registers it` : null}>
          <Combo mono value={spec.backend} onChange={(v) => onChange({ ...spec, backend: v })}
            choices={backends.map((b) => ({ value: b.name, description: [b.description, b.capabilities?.length ? `can: ${b.capabilities.join(", ")}` : ""].filter(Boolean).join(" · ") }))} />
        </Field>
        <Field label="Label" help="Its name in the UI and the reports (default: the source's name)."><Text value={spec.label} onChange={(v) => set("label", v)} placeholder={name} /></Field>
        <Field label="Options" wide help={"Where the project is, and how to reach it. " + Object.entries<string>(backend?.options || {}).map(([k, d]) => `${k}: ${d}`).join(" ")}
          hint={optionChoices.length > 0 && <span className="small muted">{optionChoices.filter((c) => !(c.value in options)).map((c) => c.value).join(", ")}{optionChoices.some((c) => !(c.value in options)) ? ": other options of this backend" : ""}</span>}>
          <KeyValues value={options} known={optionChoices} keyPlaceholder="new option (url, project…)" onChange={(v) => set("options", v)} />
        </Field>
        <Field label="Credentials" wide help={"Never written here: an environment variable (env:) or a file (file:) on the sequencer host. " + Object.entries<string>(backend?.credentials || {}).map(([k, d]) => `${k}: ${d}`).join(" ")}>
          <Credentials value={credentials} rows={credRows} help={backend?.credentials} onChange={(v) => set("credentials", v)} />
          {credRows.length > 1 && <div className="hint">Give the ones of one way to sign in; the others stay empty.</div>}
        </Field>
        <Field label="Labels" help={"The labels of every issue tsq creates in it (besides those of requirements)." + (backend?.name === "redmine" ? " Redmine has no labels: not sent." : "")}>
          <Tags value={spec.labels} onChange={(v) => set("labels", v.length ? v : undefined)} placeholder={spec.labels?.length ? "" : "tsq"} />
        </Field>
        <Field label="Requirements" help="How the issues of this source name requirements, if not as the file says (Requirements, below).">
          <ReqRules value={spec.requirements} onChange={(v) => set("requirements", v)} inherit />
        </Field>
      </>}
    </div>
  );
}

function Routes({ value, names, onChange }: { value: Data[]; names: string[]; onChange: (v: Data[]) => void }) {
  const rows = Array.isArray(value) ? value : [];
  const put = (i: number, r: Data) => onChange(rows.map((x, j) => (j === i ? pruned(r) : x)));
  const move = (i: number, d: number) => {
    const next = [...rows];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange(next);
  };
  const sourceChoices: Choice[] = names.map((n) => ({ value: n }));
  return (
    <div>
      {rows.length > 0 && (
        <table className="grid compact tickets-routes">
          <thead><tr><th>#</th><th>Source</th>{ROUTE_KEYS.map((k) => <th key={k.value} title={k.description}>{k.value}</th>)}<th /></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>
              <td className="muted">{i + 1}</td>
              <td><Combo mono value={r.source} choices={sourceChoices} onChange={(v) => put(i, { ...r, source: v })} /></td>
              {ROUTE_KEYS.map((k) => (
                <td key={k.value}><Text mono value={r[k.value]} placeholder="*" onChange={(v) => put(i, { ...r, [k.value]: v.trim() || undefined })} /></td>
              ))}
              <td className="nowrap">
                <button className="icon" disabled={i === 0} title="Before" onClick={() => move(i, -1)}>↑</button>
                <button className="icon" disabled={i === rows.length - 1} title="After" onClick={() => move(i, 1)}>↓</button>
                <button className="icon" title="Remove this route" onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
              </td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {rows.some((r) => r.source && !names.includes(r.source)) && <div className="bad-text small">A route names a source that is not in this file.</div>}
      {rows.some((r) => !ROUTE_KEYS.some((k) => r[k.value])) && <div className="bad-text small">A route needs at least one pattern.</div>}
      <button className="small" disabled={!names.length} onClick={() => onChange([...rows, { source: names[0], step: "" }])}>+ Add route</button>
      <div className="hint">Globs on the failure: <code>step: "firmware:*"</code> sends the failures of the firmware stage to its project. A route's patterns must all match.</div>
    </div>
  );
}

function ReqRules({ value, onChange, inherit }: { value?: Data | null; onChange: (v: Data | undefined) => void; inherit?: boolean }) {
  const given: Data = value && typeof value === "object" ? value : {};
  if (inherit && !value) {
    return <span className="small muted">as the file says <button className="link small" onClick={() => onChange({ ...REQ_DEFAULTS })}>set them for this source…</button></span>;
  }
  const v = { ...REQ_DEFAULTS, ...given };
  // written as given: the keys left at their default are left out
  const put = (k: string, x: any) => {
    const next: Data = { ...given, [k]: x };
    for (const [d, dv] of Object.entries(REQ_DEFAULTS)) if (next[d] === dv) delete next[d];
    onChange(Object.keys(next).length || inherit ? next : undefined);
  };
  const labelsOff = v.labels === false;
  const pattern = labelsOff ? "" : String(v.labels);
  return (
    <div className="tickets-req-rules">
      <div className="row">
        <Toggle value={!labelsOff} label="Labels:" onChange={(on) => put("labels", on ? REQ_DEFAULTS.labels : false)} />
        {!labelsOff && <Text mono value={pattern} invalid={!pattern.includes("{id}")} onChange={(x) => put("labels", x)} placeholder="req:{id}" />}
        {!labelsOff && !pattern.includes("{id}") && <span className="bad-text small">a pattern with {"{id}"}</span>}
      </div>
      {!labelsOff && <Toggle value={v.add} label="Give such labels to the issues tsq creates" onChange={(x) => put("add", x)} />}
      <Toggle value={v.text} label="A known requirement's id in an issue's title or text names it" onChange={(x) => put("text", x)} />
      <Toggle value={v.jira_links} label="Jira: an issue link to the requirement's own issue names it" onChange={(x) => put("jira_links", x)} />
      {inherit && <button className="link small" onClick={() => onChange(undefined)}>as the file says</button>}
    </div>
  );
}
