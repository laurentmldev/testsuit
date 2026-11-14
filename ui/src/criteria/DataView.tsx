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

// A data file of a unit-test case made by hand (CSV): its table and a plot of its columns, to see at a
// glance what the case feeds the checks with.
import { useMemo, useState } from "react";
import { classes } from "../util";

export interface Table {
  header: string[];
  rows: string[][];
  delimiter: string;
  comments: string[];
}

const MAX_PLOT = 20000;
const MAX_ROWS = 300;

export function parseCsv(text: string): Table | null {
  const lines = text.split(/\r?\n/);
  const comments: string[] = [];
  let i = 0;
  while (i < lines.length && (lines[i].startsWith("#") || !lines[i].trim())) comments.push(lines[i++]);
  if (i >= lines.length) return null;
  const head = lines[i];
  const delimiter = [";", ",", "\t"].map((d) => [d, head.split(d).length] as const).sort((a, b) => b[1] - a[1])[0][0];
  const header = head.split(delimiter).map((h) => h.trim());
  if (header.length < 2) return null;
  const rows = lines.slice(i + 1).filter((l) => l.trim() && !l.startsWith("#")).map((l) => l.split(delimiter).map((v) => v.trim()));
  return { header, rows, delimiter, comments };
}

function xValues(rows: string[][]): { xs: number[]; label: string } {
  const first = rows.map((r) => r[0]);
  if (first.every((v) => v !== "" && !isNaN(Number(v)))) return { xs: first.map(Number), label: "" };
  const dates = first.map((v) => Date.parse(v));
  if (dates.every((d) => !isNaN(d))) return { xs: dates.map((d) => (d - dates[0]) / 1000), label: " (s from the first row)" };
  return { xs: first.map((_, i) => i), label: " (row)" };
}

const COLORS = ["#2a6fdb", "#d9480f", "#2b8a3e", "#ae3ec9", "#e67700", "#1098ad", "#c92a2a", "#5c940d"];

export function DataView({ text }: { text: string }) {
  const table = useMemo(() => parseCsv(text), [text]);
  const numeric = useMemo(() => {
    if (!table) return [];
    return table.header.map((_, c) => c).filter((c) => c > 0 && table.rows.length > 0 &&
      table.rows.slice(0, 200).every((r) => r[c] === undefined || r[c] === "" || !isNaN(Number(r[c]))));
  }, [table]);
  const [shown, setShown] = useState<number[] | null>(null);
  const [view, setView] = useState<"plot" | "table">("plot");
  if (!table) return <div className="banner warn">Not a table this view can read (a header line, then values separated by ; , or tabs).</div>;
  const cols = shown ?? numeric.slice(0, 3);
  return (
    <div className="data-view">
      <div className="data-bar">
        <div className="segmented">
          <button className={view === "plot" ? "selected" : ""} onClick={() => setView("plot")}>Plot</button>
          <button className={view === "table" ? "selected" : ""} onClick={() => setView("table")}>Table</button>
        </div>
        <span className="muted small">{table.rows.length} rows, {table.header.length} columns, separated by “{table.delimiter === "\t" ? "tab" : table.delimiter}”</span>
      </div>
      {table.comments.filter((c) => c.trim()).length > 0 && <pre className="data-comments">{table.comments.join("\n").trim()}</pre>}
      {view === "plot" ? (
        <>
          <div className="data-columns">
            {numeric.map((c, i) => (
              <label key={c} className="data-column">
                <input type="checkbox" checked={cols.includes(c)} onChange={(e) => setShown(e.target.checked ? [...cols, c] : cols.filter((x) => x !== c))} />
                <span className="swatch" style={{ background: COLORS[i % COLORS.length] }} />{table.header[c]}
              </label>
            ))}
          </div>
          <Plot table={table} cols={cols} colorOf={(c) => COLORS[numeric.indexOf(c) % COLORS.length]} />
        </>
      ) : (
        <div className="data-table-wrap">
          <table className="data-table">
            <thead><tr>{table.header.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
            <tbody>{table.rows.slice(0, MAX_ROWS).map((r, i) => <tr key={i}>{table.header.map((_, c) => <td key={c}>{r[c]}</td>)}</tr>)}</tbody>
          </table>
          {table.rows.length > MAX_ROWS && <p className="muted small">First {MAX_ROWS} rows of {table.rows.length}: the text view shows them all.</p>}
        </div>
      )}
    </div>
  );
}

function Plot({ table, cols, colorOf }: { table: Table; cols: number[]; colorOf: (c: number) => string }) {
  const rows = table.rows.length > MAX_PLOT ? table.rows.filter((_, i) => i % Math.ceil(table.rows.length / MAX_PLOT) === 0) : table.rows;
  const { xs, label } = xValues(rows);
  const W = 800, H = 260, L = 50, R = 10, T = 10, B = 28;
  const series = cols.map((c) => ({ c, ys: rows.map((r) => (r[c] === undefined || r[c] === "" ? NaN : Number(r[c]))) }));
  const all = series.flatMap((s) => s.ys.filter((y) => !isNaN(y)));
  if (!xs.length || !all.length) return <p className="muted">Choose a column to plot.</p>;
  let [ymin, ymax] = [Math.min(...all), Math.max(...all)];
  if (ymin === ymax) [ymin, ymax] = [ymin - 1, ymax + 1];
  const [xmin, xmax] = [Math.min(...xs), Math.max(...xs) === Math.min(...xs) ? Math.min(...xs) + 1 : Math.max(...xs)];
  const sx = (x: number) => L + ((x - xmin) / (xmax - xmin)) * (W - L - R);
  const sy = (y: number) => T + (1 - (y - ymin) / (ymax - ymin)) * (H - T - B);
  const fmt = (v: number) => (Math.abs(v) >= 1000 || (Math.abs(v) < 0.01 && v !== 0) ? v.toExponential(1) : String(Math.round(v * 1000) / 1000));
  return (
    <svg className="data-plot" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="plot of the data">
      <line x1={L} y1={H - B} x2={W - R} y2={H - B} className="axis" />
      <line x1={L} y1={T} x2={L} y2={H - B} className="axis" />
      {[ymin, (ymin + ymax) / 2, ymax].map((v) => <text key={v} x={L - 4} y={sy(v) + 4} textAnchor="end" className="tick">{fmt(v)}</text>)}
      {[xmin, (xmin + xmax) / 2, xmax].map((v) => <text key={v} x={sx(v)} y={H - B + 14} textAnchor="middle" className="tick">{fmt(v)}</text>)}
      <text x={W - R} y={H - 2} textAnchor="end" className="tick">{table.header[0]}{label}</text>
      {series.map(({ c, ys }) => {
        let d = "";
        let pen = false;
        ys.forEach((y, i) => {
          if (isNaN(y)) { pen = false; return; }
          d += `${pen ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(y).toFixed(1)}`;
          pen = true;
        });
        return <path key={c} d={d} fill="none" stroke={colorOf(c)} strokeWidth={1.4} vectorEffect="non-scaling-stroke" className={classes("series")} />;
      })}
    </svg>
  );
}
