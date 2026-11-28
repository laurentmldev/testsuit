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

import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "./styles.css";
import { get, post, setApiScope, setSequencerOn, url, useConnected, useResource } from "./api";
import type { AppInfo } from "./appinfo";
import { attempt, classes, notify, Toasts, useRoute } from "./util";
import { NavIcon } from "./navicons";
import { KindLegend } from "./kinds";
import { setAppName, useTabTitle } from "./title";
import { ACTIVE, QueuePage } from "./pages/Queue";
import { RunsPage } from "./pages/Runs";
import { RunPage } from "./pages/Run";
import { CampaignRunPage } from "./pages/Campaigns";
import { LabPage } from "./pages/Lab";
import { ConfigPage } from "./pages/Config";
import { EditorHome } from "./editor/EditorHome";
import { DatapacksPage } from "./pages/Datapacks";
import { ExploitPage } from "./exploit/ExploitPage";
import { ExtractPage } from "./extract/ExtractPage";
import { PlotPage } from "./plot/PlotPage";
import { SessionPage } from "./editor/Session";
import { LabPushDialog } from "./editor/Publish";
import { ExploitCriteriaPage, SessionCriteriaPage } from "./criteria/CriteriaPage";
import { AppStopped, FolderButton, FolderPickerPage, type ExploitMode } from "./criteria/FolderPicker";
import { ExportsBadge } from "./pages/Review";

function LabBanner({ editor }: { editor: boolean }) {
  const lab = useResource<any>("lab", ["lab"]);
  const [pushing, setPushing] = useState(false);
  const d = lab.data;
  if (!d) return null;
  const reload = async () => {
    const revision = d.published?.tag || d.published?.commit;
    const done = await attempt(() => post("lab/reload", { pull: true, revision }), `lab reloaded on ${revision?.slice(0, 12)}`);
    if (done) lab.refresh();
  };
  return (
    <>
      {d.published?.newer && (
        <div className="banner info">
          A newer lab configuration was pushed from the Editor: <strong>{d.published.tag || d.published.commit.slice(0, 10)}</strong>{" "}
          by {d.published.author}, “{d.published.message}”. The lab in use is{" "}
          <code>{d.git?.tag || d.git?.commit?.slice(0, 10) || "not in git"}</code>.
          <button className="primary small" onClick={reload}>Reload the lab on it</button>
          <span className="muted"> Running datapacks keep their components; the new lab applies to the next activations.</span>
        </div>
      )}
      {(d.warnings || []).map((w: string) => (
        <div className="banner warn" key={w}>
          {w}
          {/* local changes of the lab folder: committed and pushed in the user's name, with their own credentials */}
          {/* a lab on a tag (detached HEAD) too: its changes are pushed to the upstream default branch */}
          {w.endsWith("has local changes") && d.git?.commit && !editor &&
            <span className="muted"> To commit and push them from here, turn the Editor on (<code>server.editor: true</code>).</span>}
          {editor && w.endsWith("has local changes") && d.git?.commit &&
            <button className="small" onClick={() => setPushing(true)} title="Review the changes of the lab folder, then commit and push them in your name, with your own git credentials (used for this push only, never stored)">Commit and push…</button>}
        </div>
      ))}
      {pushing && <LabPushDialog onClose={() => { setPushing(false); lab.refresh(); }} />}
    </>
  );
}

// The subtabs of the Runs tab: what is queued and running now, and the history of every run.
const RUNS_SUBTABS: [string, string, string][] = [
  ["queue", "Queue", "Runs waiting and running now: post a datapack, change priorities, cancel or kill"],
  ["runs", "All runs", "Every run, past and present, filtered by state"],
];

function RunsSubtabs({ page }: { page: string }) {
  return (
    <div className="page subtabs-row"><div className="tabs subtabs">
      {RUNS_SUBTABS.map(([key, label, title]) => (
        <a key={key} href={`#/${key}`} title={title} className={classes(page === key && "selected")}>{label}</a>
      ))}
    </div></div>
  );
}

// The subtabs of the Exploit tab: check the criteria (run exploit_runner), plot and extract the data of the runs by hand.
const EXPLOIT_SUBTABS: [string, string, string][] = [
  ["exploit", "Criteria", "Check criteria: run exploit_runner on scenarios and the data of runs, open its reports"],
  ["exploit/plot", "Plot", "Plot the parameters of the data of runs (or of the workspaces) by hand"],
  ["exploit/extract", "Extract", "Extract parameters of the data of runs (or of the workspaces) to an HDF5 file"],
];

function ExploitSubtabs({ sub }: { sub: string }) {
  return (
    <div className="page subtabs-row"><div className="tabs subtabs">
      {EXPLOIT_SUBTABS.map(([key, label, title]) => (
        <a key={key} href={`#/${key}`} title={title} className={classes((sub ? `exploit/${sub}` : "exploit") === key && "selected")}>{label}</a>
      ))}
    </div></div>
  );
}

// The Runs tab itself (#/, the home page): opens the Queue subtab while some run waits or runs, and the
// All runs subtab when the queue is empty. #/queue and #/runs still open their subtab whatever the queue.
function RunsHome() {
  const pending = useResource<any[]>("runs?limit=1&state=" + ["queued", "waiting_resources", ...ACTIVE].join(","));
  useEffect(() => {
    if (pending.data) window.location.replace(pending.data.length ? "#/queue" : "#/runs");
    else if (pending.error) window.location.replace("#/queue");
  }, [pending.data, pending.error]);
  return null;
}

// The tabs of the exploit mode, on its folder
const EXPLOIT_TABS = [
  ["criteria", "Criteria", "Edit the scenarios of the folder (completion and checks) and their unit tests"],
  ["exploit", "Exploit", "Run exploit_runner on scenarios and data, open its reports"],
  ["plot", "Plot", "Plot the parameters of data files by hand"],
  ["extract", "Extract", "Extract parameters of data files to an HDF5 file"],
];

/** app/info, again every few seconds while the sequencer's tsqd is not running (it may be starting). */
function useAppInfo(initial: AppInfo): [AppInfo, () => void] {
  const [info, setInfo] = useState(initial);
  const refresh = () => { get<AppInfo>("app/info").then(setInfo).catch(() => {}); };
  const seqState = info.sequencer?.state;
  useEffect(() => {
    if (!info.available.includes("sequencer")) return;
    const t = window.setInterval(refresh, seqState === "running" ? 30000 : 3000);
    return () => window.clearInterval(t);
  }, [seqState]);
  return [info, refresh];
}

function SequencerBanner({ info }: { info: AppInfo }) {
  const s = info.sequencer;
  if (!s || s.state === "running") return null;
  const starting = s.state === "starting" || s.state === "restarting";
  return (
    <div className={classes("banner", starting ? "info" : "warn")}>
      {starting ? <>The sequencer is starting (tsqd{s.detail ? `, ${s.detail}` : ""}): its tabs fill in once it answers.</>
        : <>The sequencer (tsqd at <code>{s.url}</code>) is <strong>{s.state}</strong>{s.detail ? `: ${s.detail}` : ""}.</>}
      {" "}<a href="#/config">Configuration</a>
    </div>
  );
}

function App({ initial }: { initial: AppInfo }) {
  const [info, refreshInfo] = useAppInfo(initial);
  const route = useRoute();
  const seqOn = info.available.includes("sequencer");
  const xOn = info.available.includes("exploit");
  const page = route.parts[0] || "";
  // the pages of the exploit mode, on its folder; #/exploit/plot and #/exploit/extract stay the sequencer's
  // (the data of its runs: the Files tab of a run opens them). With the sequencer on, the criteria are edited
  // on the Datapacks tab (#/criteria/<session>): the exploit mode has no Criteria tab then
  const xCriteria = xOn && !seqOn;
  const xPage = xOn && ((((page === "criteria" && xCriteria) || page === "exploit") && !route.parts[1]) || page === "plot" || page === "extract");
  setApiScope(xPage ? "x" : "seq");
  const connected = useConnected();
  const [stopped, setStopped] = useState(false);
  const [picking, setPicking] = useState(false);
  const [editor, setEditor] = useState(false);
  const [xMode, setXMode] = useState<ExploitMode | null>(null);
  const seqUp = seqOn && info.sequencer?.state === "running";
  const health = useResource<any>(seqUp ? "seq/health" : null, ["lab"]);
  useEffect(() => {
    if (seqUp) get("seq/edit").then(() => setEditor(true)).catch(() => setEditor(false));
  }, [seqUp]);
  useEffect(() => {
    if (xOn) get<ExploitMode>("x/mode").then(setXMode).catch(() => setXMode(null));
  }, [xOn]);
  const title = info.title;
  const logo = info.logo ? (/^https?:/.test(info.logo) ? info.logo : url(info.logo)) : null;
  // without the sequencer, the home page is the criteria editor
  useEffect(() => { if (page === "" && !seqOn) window.location.replace("#/criteria"); }, [page, seqOn]);
  // with both modes on, #/criteria alone (no editing session) opens the exploit mode's Exploit tab
  useEffect(() => { if (page === "criteria" && !route.parts[1] && xOn && seqOn) window.location.replace("#/exploit"); }, [page, route.parts[1], xOn, seqOn]);
  // an editor session and the criteria editor name their own browser tab and fill the window
  const editing = (page === "edit" && !!route.parts[1]) || (page === "criteria" && (!!route.parts[1] || xPage));
  const plotting = page === "plot" || page === "extract" || (page === "exploit" && (route.parts[1] === "plot" || route.parts[1] === "extract"));
  useEffect(() => { setAppName(title); }, [title]);
  const seqNav: string[][] = seqOn ? [
    ["runs", "Runs", "Runs waiting, running and done"], ...(editor ? [["datapacks", "Datapacks", "Test definitions, campaigns, requirements and issues: edit, generate, run"]] : []),
    ["lab", "Infra", "The lab: its components, their state, the resources"],
    // the sequencer's Exploit tab (on the data of its runs) when the exploit mode is off; with it on, its tabs serve
    ...(editor && !xOn ? [["exploit", "Exploit", "Check criteria, plot and extract the data of the runs"]] : []),
  ] : [];
  const xNav = xOn ? EXPLOIT_TABS.filter(([key]) => key !== "criteria" || xCriteria) : [];
  const nav = [...seqNav, ...xNav, ["config", "Configuration", "What this testsuit app runs: its modes, their settings, the plugins"]];
  // the Runs tab groups the queue and the list of all runs as its subtabs
  const tabOf = (key: string) => (key === "queue" || key === "" || key === "campaigns" ? "runs"
    : xOn && key === "exploit" && route.parts[1] ? route.parts[1] : key);
  const tabLabel = page === "edit" ? "Editor" : nav.find(([key]) => key === tabOf(page))?.[1] ?? "Runs";
  useTabTitle(editing ? undefined : tabLabel);
  if (stopped) return <div className="app"><main><AppStopped /></main></div>;
  let content;
  const needsFolder = xPage && (page === "criteria" || page === "exploit") && xMode && !xMode.folder;
  if (needsFolder) content = <FolderPickerPage mode={xMode!} />;
  else if (xPage && page === "criteria") content = xMode ? <ExploitCriteriaPage mode={xMode} onFolder={() => setPicking(true)} /> : null;
  else if (xPage && page === "exploit") content = <ExploitPage />;
  else if (xPage && page === "plot") content = <PlotPage />;
  else if (xPage && page === "extract") content = <ExtractPage />;
  else if (page === "config") content = <ConfigPage info={info} editor={editor} refresh={refreshInfo} onStopped={() => setStopped(true)} />;
  else if (!seqOn) content = null;
  else if (!seqUp) content = <div className="page"><p className="muted">Waiting for the sequencer…</p></div>;
  else if (page === "runs" && route.parts[1]) content = <RunPage id={route.parts[1]} tab={route.query.get("tab") || "pipeline"} editor={editor}
    file={route.query.get("file")} line={Number(route.query.get("line")) || null} />;
  else if (page === "runs") content = <RunsPage />;
  else if (page === "campaigns" && route.parts[1]) content = <CampaignRunPage id={route.parts[1]} />;
  else if (page === "lab") content = <LabPage editor={editor} />;
  else if (page === "datapacks") content = <DatapacksPage route={route} />;
  else if (plotting) content = route.parts[1] === "extract" ? <ExtractPage /> : <PlotPage />;
  else if (page === "exploit") content = <ExploitPage />;
  else if (page === "edit" && route.parts[1]) content = <SessionPage id={route.parts[1]} route={route} />;
  else if (page === "criteria" && route.parts[1]) content = <SessionCriteriaPage sid={route.parts[1]} route={route} />;
  else if (page === "edit") content = <EditorHome />;
  else if (page === "queue") content = <QueuePage />;
  else content = <RunsHome />;
  return (
    <div className={classes("app", (editing || plotting) && !needsFolder && "full")}>
      <header className="topbar">
        <a className="brand" href="#/" title={`testsuit ${info.testsuit}`}>
          {logo && <img src={logo} alt="" />}
          {title}
        </a>
        <nav>
          {nav.map(([key, label, hint], i) => (
            <a key={key} href={key === "runs" ? "#/" : `#/${key}`} title={hint}
               className={classes(tabOf(page) === key && "active", seqNav.length > 0 && xNav.length > 0 && (i === seqNav.length || i === seqNav.length + xNav.length) && "group-start")}>
              <NavIcon name={key} />{label}</a>
          ))}
        </nav>
        <div className="topbar-right">
          {xOn && xMode && <FolderButton mode={xMode} open={picking} setOpen={setPicking} />}
          {seqOn && <ExportsBadge />}
          {seqOn && health.data?.lab && (
            <span className="pill" title={`lab commit ${health.data.lab.commit || ""}`}>
              lab {health.data.lab.tag || health.data.lab.commit?.slice(0, 8) || "?"}
            </span>
          )}
          {seqOn && <KindLegend />}
          <a href="https://github.com/laurentmldev/testsuit" target="_blank" rel="noreferrer"
             title={`testsuit ${info.testsuit} is free software under the GNU AGPL, version 3 or later: get its source code`}>source</a>
          {seqOn && <span className={classes("dot", connected && seqUp ? "on" : "off")}
            title={!seqUp ? `the sequencer is ${info.sequencer?.state}` : connected ? "live updates on" : "no live updates (reconnecting)"} />}
        </div>
      </header>
      {seqOn && <SequencerBanner info={info} />}
      {seqUp && <LabBanner editor={editor} />}
      <main className={classes(plotting && "plot-host")}>
        {seqUp && (page === "queue" || (page === "runs" && !route.parts[1])) && <RunsSubtabs page={page} />}
        {seqUp && !xOn && page === "exploit" && <ExploitSubtabs sub={route.parts[1] || ""} />}
        {content}
      </main>
      <Toasts />
    </div>
  );
}

// The app's modes come first (app/info): the tabs, and the APIs the pages call, follow them.
function Root() {
  const [info, setInfo] = useState<AppInfo | null | undefined>(undefined);
  const [error, setError] = useState("");
  useEffect(() => {
    get<AppInfo>("app/info")
      .then((i) => { setSequencerOn(i.available.includes("sequencer")); setInfo(i); })
      .catch((e) => { setError(String(e.message || e)); setInfo(null); });
  }, []);
  if (info === undefined) return null;
  if (info === null) return <div className="app"><main><div className="page"><h2>testsuit does not answer</h2><p className="muted">{error}</p></div></main></div>;
  if (!info.available.length) return <div className="app"><main><div className="page"><h2>{info.title}</h2>
    <p className="muted">The modes of this testsuit app ({info.modes.join(", ")}) can be used only from the machine it runs on.</p></div></main></div>;
  return <App initial={info} />;
}

window.addEventListener("unhandledrejection", (e) => notify(String(e.reason?.message || e.reason), "error"));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
