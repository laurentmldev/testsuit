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

// How the criteria editor talks to its server. The component never calls a route itself: tsqd gives an
// adapter on an editing session, `mxp edit` one on its folder (feasibility study, section 6.5).
import { get, post, put, url } from "../api";

export interface Problem {
  file: string;
  line: number | null;
  severity: "error" | "warning" | "info";
  message: string;
  entry?: string | null;
  field?: string | null;
}

export interface CaseInfo {
  name: string;
  description: string;
  data: string[];
  data_files: string[];
  /** its data is outside the folder edited */
  data_elsewhere: boolean;
  expect: Record<string, { status: string; message: string | null }>;
  default: string;
}

export interface UtestsInfo {
  path: string;
  scenario?: string;
  error?: string;
  dictionaries?: string[];
  cases?: CaseInfo[];
  last?: { ok: boolean; fully_covered: boolean; coverage: Record<string, string>; cases_ok: number; cases: number; at: number; job: string; partial: boolean } | null;
}

export interface ScenarioInfo {
  path: string;
  name: string;
  files: string[];
  critlibs: string[];
  testdef: string | null;
  /** the dictionaries of its test definition */
  dictionaries: string[];
  utests: string[];
}

export interface Overview {
  root: string;
  scenarios: ScenarioInfo[];
  utests: UtestsInfo[];
  git: { branch: string; changed: number } | null;
}

export interface CriterionInfo {
  source: string;
  source_file?: string | null;
  declared: boolean;
  doc: string;
  asserts: boolean;
  defines_cp: boolean;
  template: string;
  schema: any;
}

export interface Description {
  format: number;
  testsuit: string;
  criteria: Record<string, CriterionInfo>;
  helpers: Record<string, { signature: string; doc: string; source: string }>;
  names: { objects: string[]; builtins: string[] };
  scenario_schema: any;
}

export interface FileDoc {
  path: string;
  content: string;
  version: string | null;
}

export interface CheckResult {
  expected: string;
  expected_message: string | null;
  got: string | null;
  message: string;
  ok: boolean;
}

export interface CaseResult {
  ok: boolean;
  error: string | null;
  report: string | null;
  log: string | null;
  checks: Record<string, CheckResult>;
  description?: string;
}

export interface UtestReport {
  utests: string;
  scenario: string;
  ok: boolean;
  fully_covered: boolean;
  checks: { name: string; asserts: boolean }[];
  cases: Record<string, CaseResult>;
  coverage: Record<string, string>;
  job?: string;
}

export interface UtestJob {
  job: string;
  utests: string;
  cases: string[];
  done: boolean;
  stopping: boolean;
  results: Record<string, CaseResult>;
  report: UtestReport | null;
  error: string | null;
}

export interface CriteriaAdapter {
  /** what the editor runs in, shown in its bar */
  label: string;
  overview(): Promise<Overview>;
  describe(scenario?: string | null): Promise<Description>;
  read(path: string): Promise<FileDoc>;
  version(path: string): Promise<string | null>;
  /** refused with an ApiError 409 (body.current: the file as it is now) when the file is not base any more */
  write(path: string, content: string, base: string | null, create?: boolean): Promise<FileDoc>;
  lint(path: string, text?: string): Promise<{ problems: Problem[]; notes: string[]; keys: string; data: string[] }>;
  keys(scenario: string): Promise<{ keys: { key: string; value: string; origin: string }[]; error: string | null; origin: string }>;
  params(scenario: string): Promise<{ params: string[]; data: string[]; error?: string }>;
  runUtests(utests: string, cases?: string[]): Promise<{ job: string; cases: string[] }>;
  utestJob(job: string): Promise<UtestJob>;
  stopUtests(job: string): Promise<unknown>;
  lastReport(utests: string): Promise<UtestReport | null>;
  /** URL of a file a run wrote (a case's report), to open in a new tab */
  jobFileUrl(job: string, path: string): string;
  /** URL of a file of the folder as it is */
  rawUrl(path: string): string;
}

/** The adapter on the REST routes of testsuit.exploit.editor.routes mounted at base. */
export function restAdapter(base: string, label: string): CriteriaAdapter {
  const enc = (path: string) => path.split("/").map(encodeURIComponent).join("/");
  return {
    label,
    overview: () => get(base),
    describe: (scenario) => get(`${base}/describe${scenario ? `?scenario=${encodeURIComponent(scenario)}` : ""}`),
    read: (path) => get(`${base}/files/${enc(path)}`),
    version: async (path) => (await get(`${base}/version/${enc(path)}`)).version,
    write: (path, content, base_, create) => put(`${base}/files/${enc(path)}`,
      { content, ...(base_ !== null ? { base: base_ } : {}), ...(create ? { create: true } : {}) }),
    lint: (path, text) => post(`${base}/lint`, { path, ...(text !== undefined ? { text } : {}) }),
    keys: (scenario) => post(`${base}/keys`, { scenario }),
    params: (scenario) => post(`${base}/params`, { scenario }),
    runUtests: (utests, cases) => post(`${base}/utests/jobs`, { utests, cases }),
    utestJob: (job) => get(`${base}/utests/jobs/${job}`),
    stopUtests: (job) => post(`${base}/utests/jobs/${job}/stop`),
    lastReport: async (utests) => (await post(`${base}/utests/last`, { utests })).report,
    jobFileUrl: (job, path) => url(`${base}/utests/jobs/${job}/files/${enc(path)}`),
    rawUrl: (path) => url(`${base}/raw/${enc(path)}`),
  };
}
