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

// Test definition form (sequencer README, section 8.3): what it is, the setup configuration it uses and its
// key files. The pipeline itself has its own view (PipelineEditor).
import { useState } from "react";
import { Combo, Field, helpText, Section, Tags, Text, type Choice } from "../fields";
import { joinPath, Markdown, relPath } from "../util";
import { DictionaryDialog } from "./DictionaryDialog";
import { clone, useSession, type Doc } from "./state";

export function TestdefForm({ doc, api }: { doc: Doc; api: { setData: (d: any) => void } }) {
  const { ctx, schemas, files, open } = useSession();
  const data = doc.data || {};
  const help = (f: string) => helpText(schemas.testdef, [f]);
  const set = (k: string, v: any) => {
    const next = clone(data);
    if (v === undefined || v === "" || (Array.isArray(v) && !v.length)) delete next[k];
    else next[k] = v;
    api.setData(next);
  };
  const setups: any[] = ctx.workspace?.setups || [];
  const setupPath = data.setup_file ? joinPath(doc.path, data.setup_file) : null;
  const setup = setups.find((s) => s.path === setupPath);
  const setupChoices: Choice[] = setups.map((s) => ({
    value: relPath(doc.path, s.path),
    label: relPath(doc.path, s.path),
    description: `${s.name || s.id || s.path} · configurations ${Object.keys(s.configurations).join(", ") || "none"}`,
  }));
  const configChoices: Choice[] = Object.entries<any>(setup?.configurations || {}).map(([name, c]) => ({
    value: name,
    description: `stage_slots ${Object.entries<any>(c.stage_slots || {}).map(([s, t]) => `${s} (${t})`).join(", ") || "none"} · slots ${Object.keys(c.slots || {}).join(", ") || "none"}`,
  }));
  const config = setup?.configurations?.[data.setup_config];
  const dictChoices: Choice[] = files.filter((f) => f.kind === "dictionary").map((f) => ({ value: relPath(doc.path, f.path), description: f.path }));
  const [dictionary, setDictionary] = useState<string | null>(null);
  const usedIds = (ctx.workspace?.testdefs || []).filter((t: any) => t.path !== doc.path).map((t: any) => t.id);
  return (
    <div className="form pad">
      {dictionary && <DictionaryDialog fromFile={doc.path} entry={dictionary} onClose={() => setDictionary(null)} />}
      <Section title="Test definition">
        <Field label="Id" help={help("id")} error={usedIds.includes(data.id) ? "another test definition has this id" : null}>
          <Text mono value={data.id} onChange={(v) => set("id", v)} />
        </Field>
        <Field label="Name" help={help("name")}><Text value={data.name} onChange={(v) => set("name", v)} /></Field>
        <Field label="Description" help={help("description")} wide><Text multiline value={data.description} onChange={(v) => set("description", v)} /></Field>
      </Section>
      <Section title="Setup" help="The setup says which stage slots and slots the test needs; the configuration picks one of its sets.">
        <Field label="Setup file" help={help("setup_file")} error={data.setup_file && !setup ? "no setup at this path in the session" : null}>
          <Combo mono value={data.setup_file} onChange={(v) => set("setup_file", v)} choices={setupChoices} strict
            onPick={(v) => {
              const s = setups.find((x) => x.path === joinPath(doc.path, v));
              const configs = Object.keys(s?.configurations || {});
              const next = clone(data);
              next.setup_file = v;
              if (!configs.includes(next.setup_config) && configs.length) next.setup_config = configs[0];
              api.setData(next);
            }} />
          {setup && <button className="link small" onClick={() => open(setup.path)}>open {setup.path}</button>}
        </Field>
        <Field label="Configuration" help={help("setup_config")}
          error={setup && data.setup_config && !config ? `not a configuration of ${setup.id || setup.path}` : null}>
          <Combo value={data.setup_config} onChange={(v) => set("setup_config", v)} choices={configChoices} strict={!!setup} />
        </Field>
        {config && (
          <table className="grid compact">
            <thead><tr><th>Slot</th><th>Slot class</th><th>Configuration</th><th>Stage slot</th><th>Role</th><th>Needs</th></tr></thead>
            <tbody>
              {Object.entries<any>(config.slots || {}).map(([id, c]) => (
                <tr key={id}>
                  <td><code>{id}</code> <span className="muted small">{c.name}</span></td>
                  <td>{c.slot_class}</td><td>{c.configuration}</td>
                  <td>{c.stage_slot ? `${c.stage_slot} (${config.stage_slots?.[c.stage_slot] || "?"})` : <span className="muted">ad hoc</span>}</td>
                  <td>{c.role}</td><td className="small">{(c.needs || []).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      <Section title="Key files" collapsed={!data.dictionaries?.length}
        help="YAML files whose keys the templates and commands use, on top of those of the setup: `{{ section.key }}`. A later file overrides an earlier one.">
        <Field label="Dictionaries" help={help("dictionaries")}><Tags value={data.dictionaries} onChange={(v) => set("dictionaries", v)} choices={dictChoices}
          onOpen={setDictionary} openTitle="View or edit this key file" /></Field>
      </Section>
      <Section title="Requirements" collapsed={!(Array.isArray(data.reqs) ? data.reqs.length : data.reqs)}
        help="Requirements the test verifies, `[<source>:]<id>[@<version>]`, from the sources of `requirements.yml` at the root of the tests repository. A check of the criteria can list its own (`reqs`); a requirement listed here is verified by the run verdict. Unknown, deleted or rejected ones are problems, and fail the generation unless forced.">
        <Field label="Requirements" help={help("reqs")}>
          <Tags value={Array.isArray(data.reqs) ? data.reqs : data.reqs ? [String(data.reqs)] : []} onChange={(v) => set("reqs", v)} placeholder="REQ-12" />
        </Field>
      </Section>
      <Section title="Keys given at each generation" collapsed={!Object.keys(data.user_keys || {}).length}
        help="Keys the user gives each time a datapack is generated (`datapack --set name=value`, or the Generate form, where each has its field): free text, or one of a list of choices. No dictionary defines them; the generation refuses to start without them.">
        <UserKeys value={data.user_keys || {}} onChange={(v) => set("user_keys", Object.keys(v).length ? v : undefined)} />
      </Section>
      <Section title="Pipeline">
        <Markdown text={`${(data.pipeline?.phases || []).length} phase(s): ${(data.pipeline?.phases || []).map((p: any) => `\`${p.id}\``).join(", ") || "none yet"}. Open the **Pipeline** view above to edit it as a graph.`} />
      </Section>
    </div>
  );
}

function UserKeys({ value, onChange }: { value: Record<string, any>; onChange: (v: Record<string, any>) => void }) {
  const entries = Object.entries<any>(value);
  const [name, setName] = useState("");
  const update = (key: string, patch: any) => {
    const spec = { ...(value[key] || {}), ...patch };
    for (const k of Object.keys(spec)) if (spec[k] === undefined || spec[k] === "" || (Array.isArray(spec[k]) && !spec[k].length)) delete spec[k];
    onChange({ ...value, [key]: spec });
  };
  const bad = name && !/^[A-Za-z_]\w*(\.\w+)*$/.test(name) ? "not a key name (name, or name.sub)" : name in value ? "already declared" : null;
  return (
    <div className="user-keys">
      {entries.length > 0 && (
        <table className="grid compact">
          <thead><tr><th>Key</th><th>Description</th><th>Choices (none: free text)</th><th /></tr></thead>
          <tbody>
            {entries.map(([key, spec]) => (
              <tr key={key}>
                <td><code>{key}</code></td>
                <td><Text value={spec?.description} onChange={(v) => update(key, { description: v })} /></td>
                <td><Tags value={(spec?.choices || []).map(String)} onChange={(v) => update(key, { choices: v })} /></td>
                <td><button className="icon" title="Remove" onClick={() => onChange(Object.fromEntries(entries.filter(([k]) => k !== key)))}>×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        <input className="mono" placeholder="key (name.sub)" value={name} onChange={(e) => setName(e.target.value.trim())} />
        <button className="small" disabled={!name || !!bad} onClick={() => { onChange({ ...value, [name]: {} }); setName(""); }}>Declare</button>
        {bad && <span className="bad-text small">{bad}</span>}
      </div>
    </div>
  );
}
