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

// Which tests repository (workspace) a page, an editing session or a generation works on: shown the same way
// everywhere, and picked among the others when there are several (sequencer README, section 13.2).
import { classes } from "../util";

export type Repository = { id: string; label?: string; url?: string | null; branch?: string | null; git?: any; added?: any };

const branchOf = (r: Repository) => r.branch ?? r.git?.branch ?? null;

/** The workspace worked on: its name, branch and git URL. */
export function WorkspaceTag({ repository, className }: { repository?: Repository | null; className?: string }) {
  if (!repository) return null;
  const branch = branchOf(repository);
  return (
    <span className={classes("workspace-tag", className)} title={`Tests repository (workspace) '${repository.id}'${repository.url ? `\n${repository.url}` : ""}${branch ? `\nbranch ${branch}` : ""}`}>
      <span className="workspace-kicker">Workspace</span>
      <strong>{repository.id}</strong>
      {branch && <code>{branch}</code>}
      {repository.url && <span className="muted small mono workspace-url">{repository.url}</span>}
    </span>
  );
}

/** The workspaces to pick from, one card each (name, branch, URL), the current one marked; a single one is only shown. */
export function WorkspacePicker({ repositories, value, onChange }: {
  repositories: Repository[]; value: string | null; onChange: (id: string) => void;
}) {
  if (repositories.length <= 1) return <WorkspaceTag repository={repositories[0]} />;
  return (
    <div className="workspace-picker" role="radiogroup" aria-label="Workspace">
      <span className="workspace-kicker">Workspace</span>
      {repositories.map((r) => {
        const branch = branchOf(r);
        return (
          <button key={r.id} type="button" role="radio" aria-checked={r.id === value}
                  className={classes("workspace-choice", r.id === value && "selected")} onClick={() => onChange(r.id)}
                  title={r.url || r.label || r.id}>
            <strong>{r.id}</strong>
            {branch && <code>{branch}</code>}
            {r.url && <span className="muted small mono workspace-url">{"\u200E" + r.url}</span>}
          </button>
        );
      })}
    </div>
  );
}
