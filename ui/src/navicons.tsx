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

// The icons of the top-level tabs: line drawings on a 24 px grid, in the text color.

const PATHS: Record<string, string> = {
  runs: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM10 8.5l5.5 3.5l-5.5 3.5z",
  datapacks: "M3 7.5l9-4.5l9 4.5l-9 4.5zM3 7.5v9l9 4.5l9-4.5v-9M12 12v9",
  lab: "M5 2h14a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM4 8h16M4 14h16M7.5 5h.01M7.5 11h.01M7.5 17h.01M11 5h5.5M11 11h5.5M11 17h5.5M7 20v2M17 20v2",   // a rack of computers
  exploit: "M4 20V11M10 20V5M16 20v-6M2.5 20h19",
  config: "M4 6h9M17 6h3M15 4v4M4 12h3M11 12h9M9 10v4M4 18h11M19 18h1M17 16v4",
};

export function NavIcon({ name }: { name: string }) {
  const d = PATHS[name];
  if (!d) return null;
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.9"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
  );
}
