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

// Quick view and edit of a dictionary (key file) listed in a form, without leaving the form.
import { useCallback, useEffect, useState } from "react";
import { get, put } from "../api";
import { Dialog } from "../fields";
import { CodeEditor } from "../monaco";
import { joinPath, notify, Spinner } from "../util";
import { useSession } from "./state";

/** The files of the session a dictionary entry names: itself, or every match of a templated name
 * (`platforms/{{ testdef.setup_config }}.yml` gives each platform file). */
export function dictionaryFiles(fromFile: string, entry: string, files: { path: string }[]): string[] {
  const target = joinPath(fromFile, entry);
  if (!entry.includes("{{")) return [target];
  const escape = (s: string) => s.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
  const pattern = new RegExp("^" + target.split(/\{\{[^}]*\}\}/).map(escape).join("[^/]+") + "$");
  return files.map((f) => f.path).filter((p) => pattern.test(p));
}

export function DictionaryDialog({ fromFile, entry, onClose }: { fromFile: string; entry: string; onClose: () => void }) {
  const { sid, files, refreshFiles, open } = useSession();
  const candidates = dictionaryFiles(fromFile, entry, files);
  const [path, setPath] = useState<string | null>(candidates[0] || null);
  const [saved, setSaved] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const dirty = saved !== null && text !== saved;

  useEffect(() => {
    setSaved(null);
    setError(null);
    setProblems([]);
    if (!path) return;
    get(`edit/sessions/${sid}/files/${path}`)
      .then((f: any) => {
        setSaved(f.content);
        setText(f.content);
      })
      .catch((e) => setError(e.message));
  }, [sid, path]);

  const close = useCallback(() => {
    if (dirty && !window.confirm("Close without saving the changes to this file?")) return;
    onClose();
  }, [dirty, onClose]);
  const save = async () => {
    if (!path) return;
    setBusy(true);
    try {
      const result: any = await put(`edit/sessions/${sid}/files/${path}`, { content: text });
      setSaved(text);
      setProblems((result.problems || []).filter((p: any) => p.severity === "error"));
      notify(`${path} saved`);
      refreshFiles();
    } catch (e: any) {
      notify(e.message, "error");
    } finally {
      setBusy(false);
    }
  };
  const footer = (
    <>
      {path && <button onClick={() => { if (!dirty || window.confirm("Leave the changes unsaved and open the file in the editor?")) { onClose(); open(path); } }}>Open in the editor</button>}
      <span className="spacer" />
      <button onClick={close}>{dirty ? "Cancel" : "Close"}</button>
      <button className="primary" disabled={!dirty || busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
    </>
  );
  return (
    <Dialog title={`Key file ${entry}`} onClose={close} footer={footer} wide>
      {candidates.length > 1 && (
        <p className="small">
          One file per value of the template:{" "}
          <select value={path || ""} onChange={(e) => (!dirty || window.confirm("Leave the changes unsaved?")) && setPath(e.target.value)}>
            {candidates.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </p>
      )}
      {candidates.length === 0 && <p className="muted">No file of this repository matches <code>{entry}</code> (seen from <code>{fromFile}</code>).</p>}
      {candidates.length === 1 && path && <p className="muted small mono">{path}</p>}
      {error && <div className="error-box">{error}</div>}
      {path && saved === null && !error && <Spinner className="block" text="Reading the file…" />}
      {path && saved !== null && (
        <div className="code-box">
          <CodeEditor uri={`file:///session/${sid}/dialog/${path}`} value={text} onChange={setText} onSave={save} language="yaml" height="55vh" />
        </div>
      )}
      {problems.map((p, i) => <div key={i} className="error-box small">{p.line ? `line ${p.line}: ` : ""}{p.message}</div>)}
    </Dialog>
  );
}
