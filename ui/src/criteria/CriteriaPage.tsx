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

// The two hosts of the criteria editor: the sequencer mode on an editing session (#/criteria/<session>),
// and the exploit mode of the testsuit app on its folder (#/criteria).
import { useCallback, useEffect, useMemo, useState } from "react";
import { get } from "../api";
import { navigate, setQuery, useRoute, type Route } from "../util";
import { restAdapter, type Overview } from "./adapter";
import { CriteriaEditor } from "./CriteriaEditor";
import { useTabTitle } from "../title";
import type { ExploitMode } from "./FolderPicker";

function GitTag({ ov }: { ov: Overview | null }) {
  if (!ov?.git) return null;
  return (
    <span className="pill" title="Commit and push with your own git tools">
      branch <strong>{ov.git.branch}</strong>{ov.git.changed ? `, ${ov.git.changed} file(s) changed` : ", no change"}
    </span>
  );
}

export function SessionCriteriaPage({ sid, route }: { sid: string; route: Route }) {
  const adapter = useMemo(() => restAdapter(`edit/sessions/${sid}/criteria`, sid, sid), [sid]);
  const file = route.query.get("file");
  const scope = route.query.get("scope");
  const [session, setSession] = useState<any>(null);
  useEffect(() => { get(`edit/sessions/${sid}`).then(setSession).catch(() => setSession(null)); }, [sid]);
  const onFile = useCallback((p: string | null) => setQuery("file", p), []);
  useTabTitle(`Criteria${scope ? ` · ${scope.split("/").pop()}` : ""}`);
  return (
    <CriteriaEditor adapter={adapter} file={file} onFile={onFile} scope={scope} bar={(ov) => (
      <div className="session-bar">
        <strong>Criteria</strong>
        {scope && <span className="muted">of <code>{scope}</code></span>}
        {session && <span className="pill">{session.repo}{session.branch ? ` · ${session.branch}` : ""}</span>}
        {!session?.local && <GitTag ov={ov} />}
        <span style={{ flex: 1 }} />
        {scope && <button className="small" onClick={() => setQuery("scope", null)} title="Every scenario of the repository">All scenarios</button>}
        <button className="small" onClick={() => navigate(`edit/${sid}`, { file: file || undefined })} title="The other files of the repository, review and push">Open in the Editor</button>
      </div>
    )} />
  );
}

/** The criteria editor of the exploit mode (#/criteria), on its folder: the folder in its bar, which can be
 *  changed from there. */
export function ExploitCriteriaPage({ mode, onFolder }: { mode: ExploitMode; onFolder: () => void }) {
  const adapter = useMemo(() => restAdapter("criteria", "local"), []);
  const route = useRoute();
  const file = route.query.get("file");
  const onFile = useCallback((p: string | null) => setQuery("file", p), []);
  const folderName = mode.folder?.split(/[\\/]/).filter(Boolean).pop() || mode.folder || "";
  useTabTitle(`Criteria${folderName ? ` · ${folderName}` : ""}`);
  return (
    <CriteriaEditor adapter={adapter} file={file} onFile={onFile} bar={(ov) => (
      <div className="session-bar">
        <strong>Criteria</strong>
        <code className="folder-path-pill" title={`The folder of the exploit mode: ${mode.folder}`}><bdi>{mode.folder}</bdi></code>
        <GitTag ov={ov} />
        <span style={{ flex: 1 }} />
        <button className="small" onClick={onFolder} title="Work on another folder">Change folder…</button>
      </div>
    )} />
  );
}
