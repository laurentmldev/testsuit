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

// The pictures of lab components on the Lab view (tsq_icon): built-in drawings, or an image of the lab repository.
// The names are those of ICONS in src/testsuit/sequencer/inventory.py.
import type { ReactNode } from "react";

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

const DRAWINGS: Record<string, { label: string; draw: ReactNode }> = {
  server: { label: "Server", draw: <g {...S}><rect x="6" y="4" width="20" height="7" rx="1.5" /><rect x="6" y="12.5" width="20" height="7" rx="1.5" /><rect x="6" y="21" width="20" height="7" rx="1.5" /><circle cx="10" cy="7.5" r=".8" /><circle cx="10" cy="16" r=".8" /><circle cx="10" cy="24.5" r=".8" /><path d="M15 7.5h7M15 16h7M15 24.5h7" /></g> },
  workstation: { label: "Workstation", draw: <g {...S}><rect x="4" y="5" width="24" height="16" rx="1.5" /><path d="M12 27h8M16 21v6" /></g> },
  switch: { label: "Network switch", draw: <g {...S}><rect x="3" y="10" width="26" height="12" rx="2" /><path d="M7 14.5h2.5v3H7zM12 14.5h2.5v3H12zM17 14.5h2.5v3H17zM22 14.5h2.5v3H22z" /><path d="M8 6l-3 2.5 3 2M24 6l3 2.5-3 2M5 8.5h22" /></g> },
  router: { label: "Router", draw: <g {...S}><circle cx="16" cy="16" r="11" /><path d="M16 8v6M13.5 10.5L16 8l2.5 2.5M16 24v-6M13.5 21.5L16 24l2.5-2.5M8 16h6M10.5 13.5L8 16l2.5 2.5M24 16h-6M21.5 13.5L24 16l-2.5 2.5" /></g> },
  tap: { label: "Network tap", draw: <g {...S}><path d="M3 12h26" /><rect x="11" y="8.5" width="10" height="7" rx="1.5" /><path d="M16 15.5V22" /><path d="M12.5 22h7l-3.5 5z" /></g> },
  embedded: { label: "Embedded calculator", draw: <g {...S}><rect x="8" y="8" width="16" height="16" rx="2" /><rect x="12" y="12" width="8" height="8" rx="1" /><path d="M12 4v4M16 4v4M20 4v4M12 24v4M16 24v4M20 24v4M4 12h4M4 16h4M4 20h4M24 12h4M24 16h4M24 20h4" /></g> },
  board: { label: "Single-board computer", draw: <g {...S}><rect x="3" y="7" width="26" height="18" rx="2" /><rect x="7" y="11" width="8" height="8" rx="1" /><path d="M19 11h6M19 14h6M19 17h4M6 22h1.5M9 22h1.5M12 22h1.5M15 22h1.5" /><circle cx="25" cy="21.5" r="1" /></g> },
  sensor: { label: "Sensor", draw: <g {...S}><circle cx="16" cy="16" r="3" /><path d="M10.5 10.5a8 8 0 0 0 0 11M21.5 10.5a8 8 0 0 1 0 11M6.5 6.5a13.5 13.5 0 0 0 0 19M25.5 6.5a13.5 13.5 0 0 1 0 19" /></g> },
  instrument: { label: "Measuring instrument", draw: <g {...S}><rect x="3" y="6" width="26" height="20" rx="2" /><rect x="6" y="9" width="14" height="11" rx="1" /><path d="M7 17l3-4 3 5 3-6 3 3" /><circle cx="24.5" cy="11.5" r="1.8" /><circle cx="24.5" cy="18" r="1.8" /><path d="M6 23h14" /></g> },
  power: { label: "Power supply", draw: <g {...S}><rect x="4" y="6" width="24" height="20" rx="2" /><path d="M17 9l-5 8h5l-2 6 6-9h-5z" /></g> },
  simulator: { label: "Simulator", draw: <g {...S}><rect x="4" y="5" width="24" height="17" rx="1.5" /><path d="M7 17c2-6 4-6 6-1s4 5 6 0 4-6 6-3" /><path d="M12 27h8M16 22v5" /></g> },
  storage: { label: "Storage", draw: <g {...S}><ellipse cx="16" cy="8" rx="10" ry="3.5" /><path d="M6 8v16c0 2 4.5 3.5 10 3.5s10-1.5 10-3.5V8M6 16c0 2 4.5 3.5 10 3.5s10-1.5 10-3.5" /></g> },
  network: { label: "Network", draw: <g {...S}><path d="M9 24h15a5 5 0 0 0 .5-10 7.5 7.5 0 0 0-14.5-2A6 6 0 0 0 9 24z" /></g> },
  camera: { label: "Camera", draw: <g {...S}><rect x="3" y="10" width="19" height="13" rx="2" /><path d="M22 14l7-3.5v12L22 19z" /><circle cx="9" cy="16.5" r="2.5" /></g> },
  generic: { label: "Component", draw: <g {...S}><path d="M16 3l11 6v14l-11 6-11-6V9z" /><path d="M5 9l11 6 11-6M16 15v14" /></g> },
};

/** The built-in pictures, in the order the editor offers them. */
export const ICONS: { name: string; label: string }[] = Object.entries(DRAWINGS).map(([name, d]) => ({ name, label: d.label }));

export function BuiltinIcon({ name, size = 32, title }: { name: string; size?: number; title?: string }) {
  const d = DRAWINGS[name] || DRAWINGS.generic;
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} role="img" aria-label={title || d.label}>
      {title && <title>{title}</title>}
      {d.draw}
    </svg>
  );
}

/** A component's picture: a built-in drawing, an image (src), or the generic one. */
export function ComponentIcon({ icon, src, size = 32, title }: { icon?: { builtin?: string; file?: string; error?: string } | null; src?: string | null; size?: number; title?: string }) {
  if (src) return <img src={src} width={size} height={size} alt={title || ""} title={title} style={{ objectFit: "contain" }} />;
  return <BuiltinIcon name={icon?.builtin || "generic"} size={size} title={title} />;
}
