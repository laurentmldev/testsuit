// Editor mode, start page (sequencer README, section 13.2): the repositories it can edit, the editing sessions.
import { useState } from "react";
import { del, post, useResource } from "../api";
import { ago, attempt, Markdown, navigate } from "../util";

const INTRO = `The Editor changes the YAML files the sequencer and the datapack tool read, and nothing else: whatever it does can also be done with a text editor and git.

- Each **editing session** works in a scratch clone of a repository, on a branch: half-done changes never reach the running lab.
- Forms fill the files for you, with the choices the lab and the tests offer, and keep your comments and layout.
- **Review and push** commits your changes in your name and pushes them, with credentials used for that push only.
- A pushed lab change is applied only when you click **Reload** (\`tsq lab reload --pull\`). Test definitions are used the next time a datapack is generated.`;

export function EditorHome() {
  const home = useResource<any>("edit");
  const [branch, setBranch] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  if (home.error) return <div className="page"><div className="error-box">{home.error}</div></div>;
  const open = async (repo: string) => {
    setBusy(repo);
    const s = await attempt(() => post("edit/sessions", { repo, branch: branch[repo] || null }));
    setBusy(null);
    if (s) navigate(`edit/${s.id}`);
  };
  const close = async (s: any) => {
    if (s.changed && !window.confirm(`Session ${s.id} has ${s.changed} changed file(s) that were not pushed. Close it and drop them?`)) return;
    await attempt(() => del(`edit/sessions/${s.id}`), "session closed");
    home.refresh();
  };
  return (
    <div className="page editor-home">
      <div className="page-head"><h2>Editor</h2></div>
      <Markdown text={INTRO} className="intro" />
      <h3>Repositories</h3>
      <div className="cards">
        {(home.data?.repositories || []).map((r: any) => (
          <div key={r.id} className="card repo">
            <div className="card-head"><strong>{r.label}</strong></div>
            <div className="muted small mono">{r.folder}</div>
            <div className="small">
              {r.git ? <>commit <code>{r.git.commit?.slice(0, 10)}</code>{r.git.tag && <> · tag <code>{r.git.tag}</code></>}{r.git.branch && <> · branch {r.git.branch}</>}{r.git.repo && <> · from <code>{r.git.repo}</code></>}</>
                : <span className="error-text">not a git work tree: the Editor needs one</span>}
            </div>
            <div className="card-actions">
              <input placeholder={r.git?.branch || "branch (default: the upstream default)"} value={branch[r.id] || ""}
                onChange={(e) => setBranch({ ...branch, [r.id]: e.target.value })} title="Branch the session works on and pushes to" />
              <button className="primary" disabled={!r.git || busy === r.id} onClick={() => open(r.id)}>
                {busy === r.id ? "Cloning…" : r.kind === "lab" ? "Edit the lab" : "Edit the tests"}
              </button>
            </div>
          </div>
        ))}
      </div>
      <h3>Editing sessions</h3>
      {(home.data?.sessions || []).length === 0 && <p className="muted">No session yet.</p>}
      <table className="grid">
        <tbody>
          {(home.data?.sessions || []).map((s: any) => (
            <tr key={s.id}>
              <td><a href={`#/edit/${s.id}`}><strong>{s.repo === "lab" ? "Lab" : s.folder}</strong></a> <span className="muted small">{s.id}</span></td>
              <td>branch {s.branch}</td>
              <td>{s.changed ? `${s.changed} changed file(s)` : "no change"}</td>
              <td>{s.pushed?.length ? `${s.pushed.length} push(es), last ${ago(s.pushed[s.pushed.length - 1].at)}` : ""}</td>
              <td className="muted">opened {ago(s.created_at)}</td>
              <td><button className="small" onClick={() => navigate(`edit/${s.id}`)}>Open</button> <button className="small" onClick={() => close(s)}>Close</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
