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
  queue: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  runs: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM10 8.5l5.5 3.5l-5.5 3.5z",
  datapacks: "M3 7.5l9-4.5l9 4.5l-9 4.5zM3 7.5v9l9 4.5l9-4.5v-9M12 12v9",
  lab: "M9 3h6M10 3v6.5l-5.2 8.6A2 2 0 0 0 6.5 21h11a2 2 0 0 0 1.7-2.9L14 9.5V3M7.6 15h8.8",
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
