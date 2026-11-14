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

// The two hosts of the criteria editor: a page of tsqd on an editing session (#/criteria/<session>),
// and the whole app when `mxp edit` serves it (#/criteria, criteria mode).
import { useCallback, useEffect, useMemo, useState } from "react";
import { get } from "../api";
import { navigate, setQuery, Toasts, type Route } from "../util";
import { restAdapter, type Overview } from "./adapter";
import { CriteriaEditor } from "./CriteriaEditor";

function GitTag({ ov }: { ov: Overview | null }) {
  if (!ov?.git) return null;
  return (
    <span className="pill" title="Commit and push with your own git tools">
      branch <strong>{ov.git.branch}</strong>{ov.git.changed ? `, ${ov.git.changed} file(s) changed` : ", no change"}
    </span>
  );
}

export function SessionCriteriaPage({ sid, route }: { sid: string; route: Route }) {
  const adapter = useMemo(() => restAdapter(`edit/sessions/${sid}/criteria`, sid), [sid]);
  const file = route.query.get("file");
  const scope = route.query.get("scope");
  const [session, setSession] = useState<any>(null);
  useEffect(() => { get(`edit/sessions/${sid}`).then(setSession).catch(() => setSession(null)); }, [sid]);
  const onFile = useCallback((p: string | null) => setQuery("file", p), []);
  useEffect(() => { document.title = `Criteria${scope ? ` · ${scope.split("/").pop()}` : ""}`; }, [scope]);
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

export function StandaloneCriteriaApp({ mode }: { mode: { folder: string; testsuit: string } }) {
  const adapter = useMemo(() => restAdapter("criteria", "local"), []);
  const [file, setFile] = useState<string | null>(() => new URLSearchParams(window.location.hash.split("?")[1] || "").get("file"));
  useEffect(() => {
    const on = () => setFile(new URLSearchParams(window.location.hash.split("?")[1] || "").get("file"));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => { document.title = `Criteria · ${mode.folder.split("/").pop()}`; }, [mode.folder]);
  const onFile = useCallback((p: string | null) => {
    if (!window.location.hash.startsWith("#/criteria")) window.location.hash = "#/criteria";
    setQuery("file", p);
  }, []);
  return (
    <div className="app full standalone">
      <CriteriaEditor adapter={adapter} file={file} onFile={onFile} bar={(ov) => (
        <div className="standalone-bar">
          <span className="brand">Criteria editor</span>
          <code title="The folder edited, in place">{mode.folder}</code>
          <GitTag ov={ov} />
          <span style={{ flex: 1 }} />
          <span className="muted small">testsuit {mode.testsuit}</span>
        </div>
      )} />
      <Toasts />
    </div>
  );
}
