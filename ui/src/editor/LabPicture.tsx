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

// How a component shows on the Lab diagram, in the inventory form of a host: its picture (tsq_icon: a built-in
// drawing or an image uploaded to the lab repository) and the wiring its configuration does not show (tsq_links).
import { useRef, useState } from "react";
import { post, url } from "../api";
import { Combo, Text, type Choice } from "../fields";
import { BuiltinIcon, ICONS } from "../labicons";
import { attempt, classes } from "../util";
import { useSession } from "./state";

const IMAGE = /\.(png|jpe?g|gif|svg|webp)$/i;

/** The folder of the inventory in the session ("" at its root): tsq_icon files are relative to it. */
function inventoryDir(inventory?: string | null): string {
  const i = (inventory || "").lastIndexOf("/");
  return i < 0 ? "" : inventory!.slice(0, i);
}

export function IconPicker({ host, value, inherited, onChange }: {
  host: string; value?: string | null; inherited?: string | null; onChange: (v: string | undefined) => void;
}) {
  const { sid, ctx, files, refreshFiles } = useSession();
  const input = useRef<HTMLInputElement>(null);
  const [version, setVersion] = useState(0);
  const [uploading, setUploading] = useState(false);
  const dir = inventoryDir(ctx.inventory);
  const shown = value ?? inherited ?? "";
  const builtin = ICONS.some((i) => i.name === shown);
  const fileOf = (icon: string) => (dir ? `${dir}/${icon}` : icon);
  const exists = !shown || builtin || files.some((f) => f.path === fileOf(shown).replace(/^\.\//, ""));
  const images: Choice[] = files.filter((f) => IMAGE.test(f.path) && (!dir || f.path.startsWith(dir + "/")))
    .map((f) => ({ value: dir ? f.path.slice(dir.length + 1) : f.path, description: "image of the lab repository" }));
  const upload = async (file: File) => {
    const ext = (file.name.match(/\.[A-Za-z0-9]+$/)?.[0] || "").toLowerCase();
    if (!IMAGE.test(ext)) return alert("Pick a PNG, JPEG, GIF, SVG or WebP image.");
    const icon = `icons/${host}${ext}`;
    const form = new FormData();
    form.append("file", file);
    setUploading(true);
    const done = await attempt(() => post(`edit/sessions/${sid}/upload/${fileOf(icon)}`, form), `${icon} uploaded`);
    setUploading(false);
    if (!done) return;
    await refreshFiles();
    setVersion((n) => n + 1);
    onChange(icon);
  };
  return (
    <div className="icon-picker">
      <div className="icon-grid">
        <button type="button" className={classes("icon-choice", !shown && "selected")} onClick={() => onChange(undefined)} title="No picture: a plain box">
          <span className="icon-none">—</span><span>none</span>
        </button>
        {ICONS.map((i) => (
          <button type="button" key={i.name} className={classes("icon-choice", shown === i.name && "selected", value == null && shown === i.name && "inherited")}
            onClick={() => onChange(i.name)} title={`${i.label} (tsq_icon: ${i.name})`}>
            <BuiltinIcon name={i.name} size={26} /><span>{i.name}</span>
          </button>
        ))}
        {shown && !builtin && (
          <span className={classes("icon-choice selected", value == null && "inherited")} title={fileOf(shown)}>
            {exists ? <img src={url(`edit/sessions/${sid}/raw/${fileOf(shown)}?v=${version}`)} width={26} height={26} alt="" style={{ objectFit: "contain" }} />
              : <span className="icon-none error-text">?</span>}
            <span>image</span>
          </span>
        )}
        <button type="button" className="icon-choice" disabled={uploading} onClick={() => input.current?.click()}
          title={`Upload an image: saved as icons/${host}.<ext> next to the inventory, in the lab repository`}>
          <span className="icon-none">⤒</span><span>{uploading ? "uploading…" : "upload…"}</span>
        </button>
        <input ref={input} type="file" accept=".png,.jpg,.jpeg,.gif,.svg,.webp,image/*" hidden
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
      </div>
      <div className="icon-path">
        <Combo mono value={value ?? ""} onChange={(v) => onChange(v || undefined)} choices={[...ICONS.map((i) => ({ value: i.name, description: i.label })), ...images]}
          placeholder={inherited ? `${inherited} (inherited)` : "a built-in picture, or an image file relative to the inventory"} />
      </div>
      {shown && !builtin && !exists && <div className="error-text">{fileOf(shown)} is not a file of the lab repository: the Lab view draws a plain box.</div>}
    </div>
  );
}

/** tsq_links as rows: a host, or a mapping host: label (a list of hosts when no row has a label). */
function rowsOf(value: any): { to: string; label: string }[] {
  if (!value) return [];
  if (typeof value === "string") return [{ to: value, label: "" }];
  if (Array.isArray(value)) return value.map((v) => (typeof v === "object" && v ? { to: String(v.to ?? ""), label: String(v.label ?? "") } : { to: String(v), label: "" }));
  if (typeof value === "object") return Object.entries(value).map(([to, label]) => ({ to, label: label == null ? "" : String(label) }));
  return [];
}

function valueOf(rows: { to: string; label: string }[]): any {
  if (!rows.length) return undefined;
  if (rows.every((r) => !r.label)) return rows.map((r) => r.to);
  return Object.fromEntries(rows.map((r) => [r.to, r.label]));
}

export function LinksEditor({ value, onChange, hosts }: { value: any; onChange: (v: any) => void; hosts: Choice[] }) {
  const rows = rowsOf(value);
  const set = (next: { to: string; label: string }[]) => onChange(valueOf(next));
  return (
    <div className="links-editor">
      {rows.map((r, i) => (
        <div key={i} className="links-row">
          <Combo value={r.to} onChange={(v) => set(rows.map((x, j) => (j === i ? { ...x, to: v } : x)))} choices={hosts} placeholder="component" strict />
          <Text value={r.label} onChange={(v) => set(rows.map((x, j) => (j === i ? { ...x, label: v ?? "" } : x)))} placeholder="label (optional): eth0, CAN bus, GPIO 17…" />
          <button type="button" className="icon" title="Remove this link" onClick={() => set(rows.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button type="button" className="small" onClick={() => set([...rows, { to: "", label: "" }])}>+ link</button>
    </div>
  );
}
