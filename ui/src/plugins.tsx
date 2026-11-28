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

// The testsuit plugins of the server (GET /plugins, testsuit.plugins.plugins_report()): the ones found, whether
// they loaded, and what each one registered. In the Configuration tab of tsqd and of `mxp webui`.
import { useResource } from "./api";

export interface PluginInfo {
  name: string; source: string; target: string; package: string | null; version: string | null;
  status: "loaded" | "failed" | "not loaded"; error?: string; includes: Record<string, string[]>;
}

const STATUS_BADGE: Record<string, string> = { loaded: "ok", failed: "bad", "not loaded": "idle" };

export function PluginsSection({ server }: { server: string }) {
  const plugins = useResource<PluginInfo[]>("plugins");
  const list = plugins.data || [];
  return (
    <section className="config-section">
      <div className="page-head"><h3>Plugins</h3>
        <span className="muted">the testsuit plugins {server} found (entry points of the group <code>testsuit.plugins</code>, and
          the <code>TESTSUIT_PLUGINS</code> environment variable), and what each one adds</span></div>
      {plugins.error && <div className="error-box">{plugins.error}</div>}
      {plugins.data && !list.length && <p className="muted">No plugin: only the built-in formats, criteria, importers… of testsuit.</p>}
      {list.length > 0 && (
        <table className="grid compact plugins-table">
          <thead><tr><th>Plugin</th><th>Status</th><th>From</th><th>Includes</th></tr></thead>
          <tbody>
            {list.map((p) => {
              const kinds = Object.entries(p.includes || {});
              return (
                <tr key={`${p.source}:${p.name}`}>
                  <td><strong>{p.name}</strong>
                    {p.package && <div className="muted small">{p.package}{p.version ? ` ${p.version}` : ""}</div>}</td>
                  <td><span className={`badge ${STATUS_BADGE[p.status] || ""}`}>{p.status}</span></td>
                  <td className="small"><div>{p.source}</div><code className="muted">{p.target}</code></td>
                  <td>
                    {p.error && <div className="plugin-error mono small">{p.error}</div>}
                    {p.status === "loaded" && !kinds.length && <span className="muted small">nothing registered while it loaded</span>}
                    {p.status === "not loaded" && <span className="muted small">found, not loaded by this server (installed or declared since it started?)</span>}
                    {kinds.map(([kind, names]) => (
                      <div key={kind} className="plugin-kind">
                        <span className="muted small">{kind} ({names.length})</span>
                        <span className="plugin-names">{names.map((n) => <code key={n}>{n}</code>)}</span>
                      </div>
                    ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
