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

// The unit tests of a scenario: run all cases or one, the checks x cases matrix with what each case
// expects and got, the coverage of each check, and the expectations not met.
import { useEffect, useRef, useState } from "react";
import { attempt, classes, Spinner } from "../util";
import type { CaseResult, CriteriaAdapter, UtestJob, UtestReport, UtestsInfo } from "./adapter";

const SHORT: Record<string, string> = { SUCCESS: "pass", FAILED: "fail", ERROR: "error", any: "any" };

const COVERAGE_HELP: Record<string, string> = {
  covered: "a case expects it to pass, and another one to fail: both seen",
  "pass-only": "no case makes it fail: add a case where it must fail",
  "fail-only": "no case makes it pass: add a case where it must pass",
  untested: "no case expects anything of it that was met",
};

export function UnitTests({ adapter, utests, onOpen, locate, onRan, scenarioChecks }: {
  adapter: CriteriaAdapter;
  utests: UtestsInfo;
  /** the checks of the scenario as written now (before a run), and whether each can fail */
  scenarioChecks?: { name: string; asserts: boolean }[];
  onOpen: (path: string, line?: number) => void;
  /** where a check is written: its scenario file and line */
  locate: (check: string) => { file: string; line: number } | null;
  onRan?: () => void;
}) {
  const [report, setReport] = useState<UtestReport | null>(null);
  const [job, setJob] = useState<UtestJob | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    setReport(null);
    setJob(null);
    adapter.lastReport(utests.path).then(setReport).catch(() => setReport(null));
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [adapter, utests.path]);

  const follow = (id: string) => {
    adapter.utestJob(id).then((j) => {
      setJob(j);
      if (j.done) {
        if (j.report) setReport({ ...j.report, job: j.job });
        onRan?.();
      } else timer.current = window.setTimeout(() => follow(id), 800);
    }).catch(() => { timer.current = window.setTimeout(() => follow(id), 2000); });
  };

  const run = async (cases?: string[]) => {
    const started = await attempt(() => adapter.runUtests(utests.path, cases));
    if (started) {
      setJob({ job: started.job, utests: utests.path, cases: started.cases, done: false, stopping: false, results: {}, report: null, error: null });
      follow(started.job);
    }
  };

  const running = !!job && !job.done;
  if (utests.error) return <div className="banner error">{utests.path}: {utests.error}</div>;
  const cases = utests.cases || [];
  // what to show: the run going on (its cases as they end) over the last report
  const results: Record<string, CaseResult> = { ...(report?.cases || {}), ...(running ? job!.results : {}) };
  const jobOf = running ? job!.job : report?.job || job?.job || "";
  const checks = report?.checks.length ? report.checks : scenarioChecks?.length ? scenarioChecks : checksFromExpectations(cases);
  const coverage = report?.coverage || {};
  const mismatches = Object.entries(results).flatMap(([name, c]) => c.error ? [{ case: name, check: null as string | null, text: c.error }]
    : Object.entries(c.checks).filter(([, r]) => !r.ok).map(([check, r]) => ({
      case: name, check, text: `expected ${r.expected}${r.expected_message ? ` /${r.expected_message}/` : ""}, got ${r.got || "not run"}${r.message ? `: ${r.message.split("\n")[0].slice(0, 300)}` : ""}`,
    })));
  const uncovered = Object.entries(coverage).filter(([, v]) => v !== "covered");

  return (
    <div className="utests">
      <div className="utests-bar">
        <strong>Unit tests</strong>
        <button className="link small" onClick={() => onOpen(utests.path)} title="Edit the cases and their expectations">{utests.path.split("/").slice(-2).join("/")}</button>
        <span className="spacer" />
        {running ? (
          <>
            <Spinner text={`running ${Object.keys(job!.results).length}/${job!.cases.length} case(s)…`} />
            <button className="small" disabled={job!.stopping} onClick={() => attempt(() => adapter.stopUtests(job!.job))}>Stop</button>
          </>
        ) : (
          <button className="primary small" onClick={() => run()} title="Run every case, each in its own mxp process">Run all</button>
        )}
        {report && !running && <span className={classes("badge", report.ok ? "ok" : "bad")}>{report.ok ? "as expected" : `${mismatches.length} not as expected`}</span>}
        {report && !running && <span className={classes("badge", report.fully_covered ? "ok" : "warn")} title="Coverage: each check that can fail is seen both passing and failing">
          {report.fully_covered ? "every check covered" : `${uncovered.length} check(s) not covered`}</span>}
      </div>
      {job?.error && <div className="error-box">{job.error}</div>}
      <div className="utests-matrix-wrap">
        <table className="utests-matrix">
          <thead>
            <tr>
              <th>check</th>
              {cases.map((c) => (
                <th key={c.name} title={c.description}>
                  <div className="case-head">
                    <span>{c.name}</span>
                    {!running && <button className="icon small" title={`Run only ${c.name}`} onClick={() => run([c.name])}>▶</button>}
                  </div>
                  {results[c.name] && <CaseLinks adapter={adapter} job={jobOf} result={results[c.name]} />}
                </th>
              ))}
              <th title="Seen passing and failing">coverage</th>
            </tr>
          </thead>
          <tbody>
            {checks.map(({ name, asserts }) => {
              const at = locate(name);
              return (
                <tr key={name}>
                  <td>{at ? <button className="link" onClick={() => onOpen(at.file, at.line)}>{name}</button> : name}</td>
                  {cases.map((c) => {
                    const expected = c.expect[name]?.status ?? c.default;
                    const res = results[c.name];
                    const r = res?.checks[name];
                    const pending = running && job!.cases.includes(c.name) && !job!.results[c.name];
                    const cls = pending ? "pending" : res?.error ? "bad" : r ? (r.ok ? "ok" : "bad") : "";
                    const title = res?.error ? res.error : r ? `expected ${r.expected}${r.expected_message ? ` /${r.expected_message}/` : ""}, got ${r.got || "not run"}${r.message ? "\n" + r.message.slice(0, 500) : ""}` : `expected ${expected}`;
                    return (
                      <td key={c.name} className={classes("cell", cls, expected === "FAILED" && "expects-fail")} title={title}>
                        {SHORT[expected] || expected}{r && r.got && r.got !== expected && expected !== "any" ? ` → ${SHORT[r.got] || r.got}` : ""}
                      </td>
                    );
                  })}
                  <td>{asserts ? (coverage[name] ? <span className={classes("badge", coverage[name] === "covered" ? "ok" : "warn")} title={COVERAGE_HELP[coverage[name]]}>{coverage[name]}</span> : <span className="muted">-</span>)
                    : <span className="muted" title="This criterion computes or plots: it cannot fail">no assert</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!running && mismatches.length > 0 && (
        <div className="problems utests-mismatches">
          {mismatches.map((m, i) => {
            const at = m.check ? locate(m.check) : null;
            return (
              <div key={i} className="problem" onClick={() => at && onOpen(at.file, at.line)}>
                <span className="where">{m.case}{m.check ? ` · ${m.check}` : ""}</span>{m.text}
              </div>
            );
          })}
        </div>
      )}
      {!report && !running && <p className="muted small">Not run yet in this editor. Each column is a case: <em>pass</em> or <em>fail</em> is what it expects of the check.</p>}
    </div>
  );
}

function CaseLinks({ adapter, job, result }: { adapter: CriteriaAdapter; job: string; result: CaseResult }) {
  return (
    <div className="case-links">
      <span className={classes("dot", result.ok ? "on" : "off")} title={result.ok ? "as expected" : "not as expected"} />
      {result.report && job && <a href={adapter.jobFileUrl(job, result.report)} target="_blank" rel="noreferrer" title="The mexploit report of this case">report</a>}
      {result.log && job && <a href={adapter.jobFileUrl(job, result.log)} target="_blank" rel="noreferrer" title="What mxp printed">log</a>}
    </div>
  );
}

function checksFromExpectations(cases: { expect: Record<string, unknown> }[]): { name: string; asserts: boolean }[] {
  const names: string[] = [];
  for (const c of cases) for (const n of Object.keys(c.expect)) if (!names.includes(n)) names.push(n);
  return names.map((name) => ({ name, asserts: true }));
}
