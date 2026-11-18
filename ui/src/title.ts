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

import { useEffect, useState } from "react";

// The browser tab is named after what it shows, then the name of the app: "Lab · Test sequencer".
// The app name comes from the sequencer's /health (its configured title); App sets it once it is known,
// and every page that names its tab follows it.

let appName = "Test sequencer";
const listeners = new Set<() => void>();

export function setAppName(name: string) {
  if (name === appName) return;
  appName = name;
  listeners.forEach((l) => l());
}

/** Names the browser tab `label · app name` (the app name alone for a null label); undefined leaves the tab
 *  to a page below that names it itself. */
export function useTabTitle(label: string | null | undefined) {
  const [app, setApp] = useState(appName);
  useEffect(() => {
    const l = () => setApp(appName);
    listeners.add(l);
    l();
    return () => { listeners.delete(l); };
  }, []);
  useEffect(() => {
    if (label === undefined) return;
    document.title = label ? `${label} · ${app}` : app;
  }, [label, app]);
}
