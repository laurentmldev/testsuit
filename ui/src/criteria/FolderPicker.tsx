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

// The folder of `mxp webui`: picked when none is open (the first launch from Anaconda Navigator), changed
// later from its bar; and the Stop button of the server, which Navigator cannot stop.

import { useEffect, useState } from "react";
import { get, post } from "../api";
import { Dialog } from "../fields";
import { notify } from "../util";

export type WebuiMode = { folder: string | null; testsuit: string; recent?: string[]; stoppable?: boolean };

type Listing = { path: string; parent: string | null; folders: string[] };

const sep = (path: string) => (path.includes("\\") && !path.includes("/") ? "\\" : "/");
const join = (path: string, name: string) => (path.endsWith(sep(path)) ? path + name : path + sep(path) + name);

/** Browse the folders of the machine and open one (the page reloads on it). */
export function FolderPicker({ mode }: { mode: WebuiMode }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const browse = (path?: string) =>
    get<Listing>(path ? `folders?path=${encodeURIComponent(path)}` : "folders")
      .then((l) => { setListing(l); setTyped(l.path); })
      .catch((e) => notify(String(e.message || e), "error"));
  useEffect(() => { browse(mode.folder || mode.recent?.[0] || undefined); }, []);
  const open = async (path: string) => {
    setBusy(true);
    try {
      await post("folder", { path });
      window.location.hash = "#/criteria";
      window.location.reload();
    } catch (e: any) {
      notify(String(e.message || e), "error");
      setBusy(false);
    }
  };
  const recent = (mode.recent || []).filter((f) => f !== mode.folder);
  return (
    <div className="folder-picker">
      {recent.length > 0 && (
        <section>
          <h4>Opened before</h4>
          <ul className="folder-list">
            {recent.map((f) => (
              <li key={f}><button className="link mono" disabled={busy} onClick={() => open(f)} title="Open this folder">{f}</button></li>
            ))}
          </ul>
        </section>
      )}
      <section>
        <h4>Browse</h4>
        <form className="folder-path" onSubmit={(e) => { e.preventDefault(); browse(typed); }}>
          <input className="mono" value={typed} onChange={(e) => setTyped(e.target.value)} spellCheck={false}
                 aria-label="Folder path" title="Type a path and press Enter to go there" />
          <button type="submit" className="small">Go</button>
        </form>
        <ul className="folder-list tree">
          {listing?.parent && <li><button className="link" onClick={() => browse(listing.parent!)}>..</button></li>}
          {listing?.folders.map((name) => (
            <li key={name}><button className="link" onClick={() => browse(join(listing.path, name))}>{name}</button></li>
          ))}
          {listing && listing.folders.length === 0 && <li className="muted">no subfolder</li>}
        </ul>
        <div className="folder-go">
          <button className="primary" disabled={!listing || busy} onClick={() => listing && open(listing.path)}>
            Open this folder
          </button>
          {listing && <code className="muted">{listing.path}</code>}
        </div>
      </section>
    </div>
  );
}

/** The whole page when no folder is open yet. */
export function FolderPickerPage({ mode }: { mode: WebuiMode }) {
  return (
    <div className="page folder-picker-page">
      <h2>Choose the folder to work on</h2>
      <p className="muted">A tests repository, a test definition, a folder of scenarios… It is opened again at the next
        launch, and you can change it later from the bar.</p>
      <FolderPicker mode={mode} />
    </div>
  );
}

/** "Change folder…" and "Stop" in the bar of `mxp webui`. */
export function WebuiFolderActions({ mode, onStopped, changeable = true }: {
  mode: WebuiMode; onStopped: () => void; changeable?: boolean;
}) {
  const [picking, setPicking] = useState(false);
  const [stopping, setStopping] = useState(false);
  const stop = async () => {
    try {
      await post("stop");
      onStopped();
    } catch (e: any) {
      notify(String(e.message || e), "error");
    }
    setStopping(false);
  };
  return (
    <>
      {changeable && <button className="small" onClick={() => setPicking(true)} title="Work on another folder">Change folder…</button>}
      {mode.stoppable && (
        <button className="small" onClick={() => setStopping(true)} title="Stop the mxp web UI server">Stop</button>
      )}
      {picking && (
        <Dialog title="Change folder" onClose={() => setPicking(false)}>
          <FolderPicker mode={mode} />
        </Dialog>
      )}
      {stopping && (
        <Dialog title="Stop the web UI?" onClose={() => setStopping(false)} footer={<>
          <button onClick={() => setStopping(false)}>Cancel</button>
          <button className="primary" onClick={stop}>Stop</button>
        </>}>
          <p>The server of this page stops, and the runs going on with it. Your files are saved as you edit them.
            Launch it again from Anaconda Navigator or with <code>mxp webui</code>.</p>
        </Dialog>
      )}
    </>
  );
}

/** What is left once the server is stopped. */
export function WebuiStopped() {
  return (
    <div className="page folder-picker-page">
      <h2>The mxp web UI is stopped</h2>
      <p className="muted">You can close this tab.</p>
    </div>
  );
}
