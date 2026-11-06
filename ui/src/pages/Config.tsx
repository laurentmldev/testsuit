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

// The configuration in use, with where each value comes from (tsq config).
import { useResource } from "../api";

export function ConfigPage({ editor }: { editor: boolean }) {
  const config = useResource<any[]>("config");
  return (
    <div className="page">
      <div className="page-head"><h2>Configuration</h2><span className="muted">the main configuration file, environment variables and defaults, as tsqd uses them</span>
        <span className="spacer" />
        {editor && <a href="#/edit" title="Every editing session, and the repositories they can open">Editing sessions</a>}
      </div>
      {config.error && <div className="error-box">{config.error}</div>}
      <table className="grid compact">
        <thead><tr><th>Setting</th><th>Value</th><th>From</th></tr></thead>
        <tbody>
          {(config.data || []).map((c) => (
            <tr key={c.key}><td><code>{c.key}</code></td><td className="mono">{typeof c.value === "object" ? JSON.stringify(c.value) : String(c.value)}</td><td className="muted">{c.source}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
