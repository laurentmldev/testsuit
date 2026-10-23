import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "./styles.css";
import { get, post, useConnected, useResource } from "./api";
import { attempt, classes, notify, Toasts, useRoute } from "./util";
import { QueuePage } from "./pages/Queue";
import { RunsPage } from "./pages/Runs";
import { RunPage } from "./pages/Run";
import { LabPage } from "./pages/Lab";
import { ConfigPage } from "./pages/Config";
import { EditorHome } from "./editor/EditorHome";
import { SessionPage } from "./editor/Session";

function LabBanner() {
  const lab = useResource<any>("lab", ["lab"]);
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
        <div className="banner warn" key={w}>{w}</div>
      ))}
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
  const page = route.parts[0] || "queue";
  const nav = [
    ["queue", "Queue"], ["runs", "Runs"], ["lab", "Lab"], ...(editor ? [["edit", "Editor"]] : []), ["config", "Configuration"],
  ];
  let content;
  if (page === "runs" && route.parts[1]) content = <RunPage id={route.parts[1]} tab={route.query.get("tab") || "pipeline"} />;
  else if (page === "runs") content = <RunsPage />;
  else if (page === "lab") content = <LabPage />;
  else if (page === "config") content = <ConfigPage />;
  else if (page === "edit" && route.parts[1]) content = <SessionPage id={route.parts[1]} route={route} />;
  else if (page === "edit") content = <EditorHome />;
  else content = <QueuePage />;
  return (
    <div className={classes("app", page === "edit" && route.parts[1] && "full")}>
      <header className="topbar">
        <a className="brand" href="#/">Test sequencer</a>
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
          <span className={classes("dot", connected ? "on" : "off")} title={connected ? "live updates on" : "no live updates (reconnecting)"} />
        </div>
      </header>
      <LabBanner />
      <main>{content}</main>
      <Toasts />
    </div>
  );
}

window.addEventListener("unhandledrejection", (e) => notify(String(e.reason?.message || e.reason), "error"));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
