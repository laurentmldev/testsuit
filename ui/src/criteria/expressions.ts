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

// Completion and hover in the values of a scenario file, by the kind of their field: the helpers, names
// and computed parameters in expressions, the parameters of the data in parameter names. What a model
// can use is set per model URI by the criteria editor (setCriteriaContext).
import { monaco } from "../monaco";
import type { Description } from "./adapter";
import { computedBefore, entries, entryAt, fieldAt, kindsOf } from "./scenario";

export interface CriteriaContext {
  describe: Description | null;
  params: string[];          // '<file>::<param>'
}

const contexts = new Map<string, CriteriaContext>();
let registered = false;

export function setCriteriaContext(uri: string, ctx: CriteriaContext | null) {
  const key = monaco.Uri.parse(uri).toString();
  if (ctx) contexts.set(key, ctx);
  else contexts.delete(key);
  if (!registered) register();
}

/** The kind of the field at a position, and what is around it. */
function where(model: monaco.editor.ITextModel, position: monaco.Position) {
  const ctx = contexts.get(model.uri.toString());
  if (!ctx?.describe) return null;
  const text = model.getValue();
  const list = entries(text);
  const entry = entryAt(list, position.lineNumber);
  if (!entry?.type || position.lineNumber === entry.line) return null;
  const crit = ctx.describe.criteria[entry.type];
  const field = fieldAt(text, position.lineNumber, position.column);
  if (!crit || !field || field === "type") return null;
  return { ctx, list, entry, crit, field, kind: kindsOf(crit)[field] || null };
}

function paramNames(params: string[]): { name: string; files: string[] }[] {
  const by = new Map<string, string[]>();
  for (const p of params) {
    const [file, name] = p.includes("::") ? p.split("::") : ["", p];
    by.set(name, [...(by.get(name) || []), file]);
  }
  return [...by.entries()].map(([name, files]) => ({ name, files }));
}

function register() {
  registered = true;
  const K = monaco.languages.CompletionItemKind;
  monaco.languages.registerCompletionItemProvider("yaml", {
    triggerCharacters: [".", "(", "\"", "'"],
    provideCompletionItems(model, position) {
      const w = where(model, position);
      if (!w || !w.kind) return { suggestions: [] };
      const before = model.getLineContent(position.lineNumber).slice(0, position.column - 1);
      const word = before.match(/[\w]*$/)?.[0] || "";
      const range = new monaco.Range(position.lineNumber, position.column - word.length, position.lineNumber, position.column);
      const cps = computedBefore(w.list, w.entry.line, w.ctx.describe);
      const params = paramNames(w.ctx.params);
      const paramItems = (r: monaco.Range) => params.map((p) => ({
        label: p.name, kind: K.Field, insertText: p.name, range: r, detail: `in ${p.files.filter(Boolean).join(", ") || "the data"}`,
        documentation: { value: `A parameter of the data of the first unit-test case.` },
      }));
      if (w.kind === "param_name") return { suggestions: paramItems(range) };
      if (w.kind === "cp_name") {
        return { suggestions: cps.map((c) => ({ label: c, kind: K.Variable, insertText: c, range, detail: "computed parameter (an earlier entry)" })) };
      }
      // an expression
      if (/\bcp\.\w*$/.test(before)) {
        return { suggestions: cps.map((c) => ({ label: c, kind: K.Variable, insertText: c, range, detail: "computed parameter (an earlier entry)" })) };
      }
      const quoted = before.match(/\b(param|params|litteral)\(\s*["']([^"']*)$/);
      if (quoted) {
        const r = new monaco.Range(position.lineNumber, position.column - quoted[2].length, position.lineNumber, position.column);
        return { suggestions: paramItems(r) };
      }
      if (/\.\w*$/.test(before)) return { suggestions: [] };       // a member of something else (np., pd....)
      const d = w.ctx.describe!;
      return {
        suggestions: [
          ...Object.entries(d.helpers).map(([name, h]) => ({
            label: name, kind: K.Function, insertText: name, range, detail: h.signature + (h.source !== "builtin" ? `  (${h.source})` : ""),
            documentation: { value: h.doc || "" }, sortText: "0" + name,
          })),
          ...(cps.length ? [{ label: "cp", kind: K.Module, insertText: "cp.", range, detail: `computed parameters: ${cps.join(", ")}`, sortText: "1cp",
            command: { id: "editor.action.triggerSuggest", title: "" } }] : []),
          ...d.names.objects.filter((n) => n !== "cp").map((n) => ({ label: n, kind: K.Module, insertText: n, range, sortText: "2" + n })),
          ...d.names.builtins.map((n) => ({ label: n, kind: K.Keyword, insertText: n, range, detail: "Python built-in", sortText: "3" + n })),
        ],
      };
    },
  });
  monaco.languages.registerHoverProvider("yaml", {
    provideHover(model, position) {
      const w = where(model, position);
      if (!w || (w.kind !== "expression" && w.kind !== "sequence_expression")) return null;
      const word = model.getWordAtPosition(position);
      if (!word) return null;
      const helper = w.ctx.describe!.helpers[word.word];
      const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
      if (helper) return { range, contents: [{ value: "```python\n" + helper.signature + "\n```" }, { value: helper.doc || "" }] };
      const line = model.getLineContent(position.lineNumber);
      if (line.slice(0, word.startColumn - 1).endsWith("cp.")) {
        const e = w.list.find((x) => x.name === word.word);
        return { range, contents: [{ value: e ? `Computed parameter, entry **${e.name}** (line ${e.line}, ${e.type})` : `**${word.word}**: no earlier entry defines it` }] };
      }
      return null;
    },
  });
}
