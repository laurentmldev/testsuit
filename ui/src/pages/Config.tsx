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

// The Configuration tab of the testsuit app: the app itself (testsuit.toml), then a section per mode on: the
// exploit mode's folder, the sequencer's tsqd (its state, its log) and its own configuration (tsq.yml, the
// settings in use with where each value comes from, as tsq config prints them); the plugins of each process.
import { useState } from "react";
import { get, post, useResource } from "../api";
import { NavIcon } from "../navicons";
import { PluginsSection } from "../plugins";
import { StopApp } from "../criteria/FolderPicker";
import { ago, notify } from "../util";
import type { AppInfo } from "../appinfo";

const STATE_BADGE: Record<string, string> = { running: "ok", starting: "run", restarting: "run", failed: "bad", unreachable: "bad", stopped: "idle" };

function Rows({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <table className="grid compact config-rows">
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}><td className="muted">{label}</td><td className="mono">{value ?? ""}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

function SettingsTable({ path, hide = [] }: { path: string; hide?: string[] }) {
  const config = useResource<any[]>(path);
  return (
    <>
      {config.error && <div className="error-box">{config.error}</div>}
      <table className="grid compact">
        <thead><tr><th>Setting</th><th>Value</th><th>From</th></tr></thead>
        <tbody>
          {(config.data || []).filter((c) => !hide.some((h) => c.key.startsWith(`${h}.`))).map((c) => (
            <tr key={c.key}><td><code>{c.key}</code></td><td className="mono">{typeof c.value === "object" && c.value !== null ? JSON.stringify(c.value) : String(c.value)}</td><td className="muted">{c.source}</td></tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function SequencerSection({ info, editor, refresh }: { info: AppInfo; editor: boolean; refresh: () => void }) {
  const s = info.sequencer;
  const [log, setLog] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const showLog = () => get<{ text: string }>("app/sequencer/log").then((l) => setLog(l.text || "(empty)")).catch((e) => notify(String(e.message || e), "error"));
  const restart = async () => {
    setBusy(true);
    try {
      await post("app/sequencer/restart");
      notify("tsqd restarted");
    } catch (e: any) {
      notify(String(e.message || e), "error");
    }
    setBusy(false);
    refresh();
  };
  const running = s?.state === "running";
  return (
    <section className="config-section">
      <div className="page-head"><h3><NavIcon name="runs" /> Sequencer mode</h3>
        <span className="muted">{s?.managed ? "tsqd, started and watched by testsuit (restarted if it ends)" : "a tsqd testsuit does not start (sequencer.url, or one already running on its port)"}</span>
        <span className="spacer" />
        {editor && <a href="#/edit" title="Every editing session, and the repositories they can open">Editing sessions</a>}
        {s?.log && <button className="small" onClick={log === null ? showLog : () => setLog(null)}>{log === null ? "tsqd log" : "Hide the log"}</button>}
        {s?.managed && <button className="small" disabled={busy} onClick={restart} title="Stop tsqd and start it again (its active runs are killed, their results packed)">Restart tsqd</button>}
      </div>
      {s && <Rows rows={[
        ["state", <><span className={`badge ${STATE_BADGE[s.state] || ""}`}>{s.state}</span>{s.detail && <span className="muted"> {s.detail}</span>}</>],
        ["tsqd", s.url],
        ...(s.managed ? [["started", s.started_at ? `${ago(new Date(s.started_at * 1000).toISOString())}${s.restarts ? `, ${s.restarts} restart(s)` : ""}${s.last_exit !== null ? `, last exit code ${s.last_exit}` : ""}` : ""]] as [string, string][] : []),
        ...(s.config ? [["its configuration", s.config]] as [string, string][] : []),
        ...(s.log ? [["its log", s.log]] as [string, string][] : []),
      ]} />}
      {log !== null && <pre className="config-log">{log}</pre>}
      {running && <>
        <h4>tsqd settings <span className="muted small">its configuration file, environment variables and defaults, as tsqd uses them</span></h4>
        <SettingsTable path="seq/config" />
        <PluginsSection server="tsqd" path="seq/plugins" />
      </>}
    </section>
  );
}

function ExploitSection() {
  const config = useResource<Record<string, any>>("x/config");
  const d = config.data;
  return (
    <section className="config-section">
      <div className="page-head"><h3><NavIcon name="criteria" /> Exploit mode</h3>
        <span className="muted">the criteria editor, exploit_runner, Plot and Extract, on a folder of this machine</span></div>
      {config.error && <div className="error-box">{config.error}</div>}
      {d && <Rows rows={[
        ["folder open", d.folder || "none yet"],
        ["exploit_runner runs kept in", d.results],
        ...((d.data || []).length ? [["data offered too", (d.data || []).join(", ")]] as [string, string][] : []),
        ["folders opened, remembered in", d.recent],
      ]} />}
    </section>
  );
}

export function ConfigPage({ info, editor, refresh, onStopped }: {
  info: AppInfo; editor: boolean; refresh: () => void; onStopped: () => void;
}) {
  const about = useResource<Record<string, string | null>>("app/about");
  const on = (m: string) => info.available.includes(m);
  return (
    <div className="page">
      <div className="page-head"><h2>Configuration</h2><span className="muted">what this testsuit app runs</span>
        <span className="spacer" />
        {info.stoppable && <StopApp onStopped={onStopped} />}
      </div>
      <section className="config-section">
        <div className="page-head"><h3><NavIcon name="config" /> testsuit</h3>
          <span className="muted">the app: one web UI, the modes of <code>testsuit.toml</code> behind it</span></div>
        <Rows rows={[
          ["modes on", info.modes.join(", ") + (info.modes.length !== info.available.length ? ` (here: ${info.available.join(", ") || "none"}; the exploit mode only from the machine testsuit runs on)` : "")],
          ["address", `${info.url}/ui/`],
          ["configuration file", info.config || "none: the defaults (the exploit mode alone)"],
          ["testsuit version", about.data?.testsuit],
          ["Python", about.data?.python],
          ["launch log", about.data?.log],
        ]} />
        <h4>testsuit.toml settings</h4>
        {/* the sections of the modes off are left out */}
        <SettingsTable path="app/config" hide={["exploit", "sequencer"].filter((m) => !info.modes.includes(m))} />
        <PluginsSection server="testsuit" path="app/plugins" />
      </section>
      {on("exploit") && <ExploitSection />}
      {on("sequencer") && <SequencerSection info={info} editor={editor} refresh={refresh} />}
    </div>
  );
}
