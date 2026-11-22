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

// History of runs, filtered by state; the runs of a campaign grouped under it.
import { Fragment, useState } from "react";
import { useResource } from "../api";
import { ago, Badge, classes, Elapsed, time } from "../util";
import { CampaignBadge, CampaignProgress } from "./Campaigns";
import { PausedFlag } from "./Operator";
import { ManualFlag } from "./Manual";
import { RunStateBadge } from "./Queue";

const FILTERS: [string, string][] = [
  ["", "all"], ["queued,waiting_resources", "waiting"], ["reserved,selftest,running,cleaning", "running"],
  ["done", "done"], ["cancelled", "cancelled"],
];

type Item = { run: any } | { campaign: any; runs: any[] };

/** The runs, newest first; those of a campaign run under it, where its newest run is. */
function grouped(runs: any[], campaigns: any[]): Item[] {
  const views = new Map(campaigns.map((c) => [c.id, c]));
  const groups = new Map<string, { campaign: any; runs: any[] }>();
  const items: Item[] = [];
  for (const r of runs) {
    const view = r.campaign_run ? views.get(r.campaign_run) : undefined;
    if (!view) {
      items.push({ run: r });
      continue;
    }
    let group = groups.get(view.id);
    if (!group) {
      group = { campaign: view, runs: [] };
      groups.set(view.id, group);
      items.push(group);
    }
    group.runs.push(r);
  }
  return items;
}

function RunRow({ r, nested, replaced }: { r: any; nested?: boolean; replaced?: string }) {
  return (
    <tr className={classes(nested && "campaign-child", replaced && "replaced")}>
      <td><a href={`#/runs/${r.id}`}>{r.id}</a></td>
      <td title={r.name}>{r.testdef}
        {r.rerun_of && <span className="chip rerun-tag" title={`Runs the datapack of run ${r.rerun_of} again, unchanged`}>re-run</span>}
        {replaced && <span className="chip muted" title={`An earlier attempt: run ${replaced} ran its datapack again, and counts for the campaign`}>earlier attempt</span>}</td>
      <td><RunStateBadge run={r} /> <ManualFlag run={r} /> <PausedFlag run={r} /></td>
      <td><Badge value={r.verdict} /></td>
      <td title={time(r.submitted_at)}>{ago(r.submitted_at)}</td>
      <td>{r.activated_at ? <Elapsed from={r.activated_at} to={r.ended_at} /> : ""}</td>
      <td><Badge value={r.conform ? "conform" : "non-conform"} title={(r.nonconform || []).join("\n")} /></td>
      <td className="reason">{r.reason}</td>
    </tr>
  );
}

export function RunsPage() {
  const [state, setState] = useState("");
  const [text, setText] = useState("");
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const runs = useResource<any[]>(`runs?limit=500${state ? `&state=${state}` : ""}`, ["run", "phase", "manual_operation", "pause"]);
  const campaigns = useResource<any[]>("campaigns?limit=200", ["run", "campaign", "manual_operation", "pause"]);
  // the runs a re-run replaced in their campaign: their re-run, by id
  const replacedBy = new Map<string, string>((runs.data || []).filter((r) => r.rerun_of).map((r) => [r.rerun_of, r.id]));
  const needle = text.toLowerCase();
  const matches = (r: any) => !text || `${r.id} ${r.name} ${r.testdef}`.toLowerCase().includes(needle);
  const items = grouped(runs.data || [], campaigns.data || [])
    .map((item): Item | null => {
      if ("run" in item) return matches(item.run) ? item : null;
      const c = item.campaign;
      const own = !text || `${c.id} ${c.campaign} ${c.title}`.toLowerCase().includes(needle);
      const children = own ? item.runs : item.runs.filter(matches);
      return children.length ? { ...item, runs: children } : null;
    })
    .filter((x): x is Item => x !== null);
  // a campaign run is open while it runs, folded once done; a click changes that
  const isFolded = (c: any) => folded[c.id] ?? c.state === "done";
  return (
    <div className="page">
      <div className="page-head">
        <div className="segmented">
          {FILTERS.map(([value, label]) => (
            <button key={label} className={state === value ? "selected" : ""} onClick={() => setState(value)}>{label}</button>
          ))}
        </div>
        <input className="search" placeholder="filter by test, campaign or id…" value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      {runs.error && <div className="error-box">{runs.error}</div>}
      <table className="grid runs-tree">
        <thead>
          <tr><th>Run</th><th>Test</th><th>State</th><th>Verdict</th><th>Submitted</th><th>Duration</th><th>Conform</th><th>Reason</th></tr>
        </thead>
        <tbody>
          {items.map((item) => {
            if ("run" in item) return <RunRow key={item.run.id} r={item.run} replaced={replacedBy.get(item.run.id)} />;
            const c = item.campaign;
            const closed = isFolded(c);
            return (
              <Fragment key={c.id}>
                <tr className="campaign-row">
                  <td>
                    <button className="fold" onClick={() => setFolded({ ...folded, [c.id]: !closed })}
                      title={closed ? "Show its runs" : "Hide its runs"}>{closed ? "▸" : "▾"}</button>{" "}
                    <a href={`#/campaigns/${c.id}`} title="The campaign run: its runs at a glance, and its report">{c.id}</a>
                  </td>
                  <td><span className="badge campaign">campaign</span> <strong title={c.file}>{c.title || c.campaign}</strong>
                    {item.runs.length < c.counts.total && <span className="muted small"> · {item.runs.length} of {c.counts.total} shown</span>}</td>
                  <td><CampaignProgress counts={c.counts} />{" "}
                    {item.runs.some((r) => r.awaiting_manual?.length) && <ManualFlag run={{ id: item.runs.find((r) => r.awaiting_manual?.length).id, awaiting_manual: item.runs.flatMap((r) => r.awaiting_manual || []) }} />}</td>
                  <td><CampaignBadge view={c} /></td>
                  <td title={time(c.submitted_at)}>{ago(c.submitted_at)}</td>
                  <td>{c.started_at ? <Elapsed from={c.started_at} to={c.last_ended_at} /> : ""}</td>
                  <td><Badge value={c.conform ? "conform" : "non-conform"} /></td>
                  <td className="reason"><a href={`#/campaigns/${c.id}`}>report</a></td>
                </tr>
                {!closed && item.runs.map((r) => <RunRow key={r.id} r={r} nested replaced={replacedBy.get(r.id)} />)}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {items.length === 0 && !runs.loading && <p className="muted">No run.</p>}
    </div>
  );
}
