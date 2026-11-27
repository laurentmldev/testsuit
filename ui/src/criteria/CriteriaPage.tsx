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
// and the whole app when `mxp webui` serves it (#/criteria, criteria mode), with its exploit_runner page (#/exploit)
// and its Plot page (#/plot).
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { get } from "../api";
import { ExploitPage } from "../exploit/ExploitPage";
import { ExtractPage } from "../extract/ExtractPage";
import { PlotPage } from "../plot/PlotPage";
import { classes, navigate, setQuery, Toasts, type Route } from "../util";
import { restAdapter, type Overview } from "./adapter";
import { CriteriaEditor } from "./CriteriaEditor";
import { useTabTitle } from "../title";
import { FolderPickerPage, WebuiFolderActions, WebuiStopped, type WebuiMode } from "./FolderPicker";

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

/** The pages of `mxp webui`, in its bar. */
type WebuiPage = "criteria" | "exploit" | "plot" | "extract";
const PAGE_NAMES: Record<WebuiPage, string> = { criteria: "Criteria", exploit: "Exploit", plot: "Plot", extract: "Extract" };

function WebuiNav({ page }: { page: WebuiPage }) {
  return (
    <nav className="webui-nav">
      <a href="#/criteria" className={classes(page === "criteria" && "active")}>Criteria</a>
      <a href="#/exploit" className={classes(page === "exploit" && "active")}>Exploit</a>
      <a href="#/plot" className={classes(page === "plot" && "active")} title="Plot the parameters of data files by hand">Plot</a>
      <a href="#/extract" className={classes(page === "extract" && "active")} title="Extract parameters of data files to an HDF5 file">Extract</a>
    </nav>
  );
}

export function StandaloneCriteriaApp({ mode }: { mode: WebuiMode }) {
  const adapter = useMemo(() => restAdapter("criteria", "local"), []);
  const read = () => ({ page: (window.location.hash.startsWith("#/exploit") ? "exploit" : window.location.hash.startsWith("#/plot") ? "plot" : window.location.hash.startsWith("#/extract") ? "extract" : "criteria") as WebuiPage,
    file: new URLSearchParams(window.location.hash.split("?")[1] || "").get("file") });
  const [where, setWhere] = useState(read);
  const [stopped, setStopped] = useState(false);
  useEffect(() => {
    const on = () => setWhere(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  // `mxp webui` has no app name of its own: its tabs are named after the page and the folder
  useEffect(() => {
    document.title = mode.folder
      ? `${PAGE_NAMES[where.page]} · ${mode.folder.split(/[\\/]/).filter(Boolean).pop() || mode.folder}`
      : "mexploit";
  }, [mode.folder, where.page]);
  const onFile = useCallback((p: string | null) => {
    if (!window.location.hash.startsWith("#/criteria")) window.location.hash = "#/criteria";
    setQuery("file", p);
  }, []);
  const head = (extra?: ReactNode) => (
    <div className="standalone-bar">
      <span className="brand">mexploit</span>
      <WebuiNav page={where.page} />
      <code title="The folder of this web UI">{mode.folder}</code>
      {extra}
      <span style={{ flex: 1 }} />
      <WebuiFolderActions mode={mode} onStopped={() => setStopped(true)} />
      <span className="muted small">testsuit {mode.testsuit}</span>
    </div>
  );
  if (stopped) return <div className="app standalone"><main><WebuiStopped /></main></div>;
  // no folder open yet (the first launch from Anaconda Navigator): the folder picker
  if (!mode.folder) {
    return (
      <div className="app standalone">
        <div className="standalone-bar">
          <span className="brand">mexploit</span>
          <span style={{ flex: 1 }} />
          <WebuiFolderActions mode={mode} changeable={false} onStopped={() => setStopped(true)} />
          <span className="muted small">testsuit {mode.testsuit}</span>
        </div>
        <main><FolderPickerPage mode={mode} /></main>
        <Toasts />
      </div>
    );
  }
  if (where.page === "extract") {
    return (
      <div className="app full standalone">
        {head()}
        <ExtractPage />
        <Toasts />
      </div>
    );
  }
  if (where.page === "plot") {
    return (
      <div className="app full standalone">
        {head()}
        <PlotPage />
        <Toasts />
      </div>
    );
  }
  if (where.page === "exploit") {
    return (
      <div className="app standalone">
        {head()}
        <main><ExploitPage /></main>
        <Toasts />
      </div>
    );
  }
  return (
    <div className="app full standalone">
      <CriteriaEditor adapter={adapter} file={where.file} onFile={onFile} bar={(ov) => head(<GitTag ov={ov} />)} />
      <Toasts />
    </div>
  );
}
