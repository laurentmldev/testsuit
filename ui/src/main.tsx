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
import { get, post, url, useConnected, useResource } from "./api";
import { attempt, classes, notify, Toasts, useRoute } from "./util";
import { QueuePage } from "./pages/Queue";
import { RunsPage } from "./pages/Runs";
import { RunPage } from "./pages/Run";
import { LabPage } from "./pages/Lab";
import { ConfigPage } from "./pages/Config";
import { EditorHome } from "./editor/EditorHome";
import { DatapacksPage } from "./pages/Datapacks";
import { SessionPage } from "./editor/Session";
import { LabPushDialog } from "./editor/Publish";
import { SessionCriteriaPage, StandaloneCriteriaApp } from "./criteria/CriteriaPage";

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
          {editor && w.endsWith("has local changes") && d.git?.branch &&
            <button className="small" onClick={() => setPushing(true)} title="Review the changes of the lab folder, then commit and push them in your name, with your own git credentials (used for this push only, never stored)">Commit and push…</button>}
        </div>
      ))}
      {pushing && <LabPushDialog onClose={() => { setPushing(false); lab.refresh(); }} />}
    </>
  );
}

function App() {
  const route = useRoute();
  const connected = useConnected();
  const [editor, setEditor] = useState(false);
  const health = useResource<any>("health", ["lab"]);
  useEffect(() => {
    get("edit").then(() => setEditor(true)).catch(() => setEditor(false));
  }, []);
  const title = health.data?.title || "Test sequencer";
  const logo = health.data?.logo ? (/^https?:/.test(health.data.logo) ? health.data.logo : url(health.data.logo)) : null;
  const page = route.parts[0] || "queue";
  const editing = (page === "edit" || page === "criteria") && !!route.parts[1];   // an editor session names its own tab
  useEffect(() => {
    if (!editing) document.title = title;
  }, [title, editing]);
  const nav = [
    ["queue", "Queue"], ["runs", "Runs"], ...(editor ? [["datapacks", "Datapacks"]] : []), ["lab", "Lab"], ["config", "Configuration"],
  ];
  let content;
  if (page === "runs" && route.parts[1]) content = <RunPage id={route.parts[1]} tab={route.query.get("tab") || "pipeline"} />;
  else if (page === "runs") content = <RunsPage />;
  else if (page === "lab") content = <LabPage editor={editor} />;
  else if (page === "datapacks") content = <DatapacksPage route={route} />;
  else if (page === "config") content = <ConfigPage editor={editor} />;
  else if (page === "edit" && route.parts[1]) content = <SessionPage id={route.parts[1]} route={route} />;
  else if (page === "criteria" && route.parts[1]) content = <SessionCriteriaPage sid={route.parts[1]} route={route} />;
  else if (page === "edit") content = <EditorHome />;
  else content = <QueuePage />;
  return (
    <div className={classes("app", editing && "full")}>
      <header className="topbar">
        <a className="brand" href="#/">
          {logo && <img src={logo} alt="" />}
          {title}
        </a>
        <nav>
          {nav.map(([key, label]) => (
            <a key={key} href={`#/${key}`} className={classes(page === key && "active")}>{label}</a>
          ))}
        </nav>
        <div className="topbar-right">
          {health.data?.lab && (
            <span className="pill" title={`lab commit ${health.data.lab.commit || ""}`}>
              lab {health.data.lab.tag || health.data.lab.commit?.slice(0, 8) || "?"}
            </span>
          )}
          <a href="https://github.com/laurentmldev/testsuit" target="_blank" rel="noreferrer"
             title="testsuit is free software under the GNU AGPL, version 3 or later: get its source code">source</a>
          <span className={classes("dot", connected ? "on" : "off")} title={connected ? "live updates on" : "no live updates (reconnecting)"} />
        </div>
      </header>
      <LabBanner editor={editor} />
      <main>{content}</main>
      <Toasts />
    </div>
  );
}

// `mxp edit` serves this UI in its criteria mode: only the criteria editor (tsqd has no /mode)
function Root() {
  const [mode, setMode] = useState<any>(undefined);
  useEffect(() => {
    get("mode").then((m) => setMode(m?.mode === "criteria" ? m : null)).catch(() => setMode(null));
  }, []);
  if (mode === undefined) return null;
  return mode ? <StandaloneCriteriaApp mode={mode} /> : <App />;
}

window.addEventListener("unhandledrejection", (e) => notify(String(e.reason?.message || e.reason), "error"));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
