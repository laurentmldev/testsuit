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

// Monaco with YAML language support (monaco-yaml): schema completion, hover help and validation from the
// schemas tsqd serves, server-side problems as markers, and completion of datapack keys in templates.
import "monaco-editor/esm/vs/editor/edcore.main";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api";
import "monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution";
import "monaco-editor/esm/vs/basic-languages/shell/shell.contribution";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution";
import "monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution";
import "monaco-editor/esm/vs/basic-languages/ini/ini.contribution";
import { configureMonacoYaml, type MonacoYaml, type SchemasSettings } from "monaco-yaml";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import YamlWorker from "./yaml.worker?worker";
import { useEffect, useRef } from "react";

(self as any).MonacoEnvironment = {
  getWorker(_: string, label: string) {
    return label === "yaml" ? new YamlWorker() : new EditorWorker();
  },
};

let yaml: MonacoYaml | null = null;
const schemaSettings = new Map<string, SchemasSettings>();

function yamlSupport(): MonacoYaml {
  if (!yaml) yaml = configureMonacoYaml(monaco, { enableSchemaRequest: false, hover: true, completion: true, validate: true, schemas: [] });
  return yaml;
}

/** Associates a JSON Schema to the model of a file (by its model URI). */
export function setSchema(modelUri: string, schema: any | null) {
  if (schema) schemaSettings.set(modelUri, { uri: `schema://${encodeURIComponent(modelUri)}`, fileMatch: [modelUri], schema });
  else schemaSettings.delete(modelUri);
  yamlSupport().update({ schemas: [...schemaSettings.values()], enableSchemaRequest: false, hover: true, completion: true, validate: true });
}

// datapack keys offered in templates, by model URI
const keySets = new Map<string, { label: string; detail?: string; doc?: string }[]>();
let keyProvider: monaco.IDisposable | null = null;

export function setKeys(modelUri: string, keys: { label: string; detail?: string; doc?: string }[] | null) {
  if (keys) keySets.set(modelUri, keys);
  else keySets.delete(modelUri);
  if (keyProvider) return;
  const provider: monaco.languages.CompletionItemProvider = {
    triggerCharacters: ["{", ".", " "],
    provideCompletionItems(model, position) {
      const keys = keySets.get(model.uri.toString());
      if (!keys) return { suggestions: [] };
      const before = model.getLineContent(position.lineNumber).slice(0, position.column - 1);
      const open = before.lastIndexOf("{{");
      if (open < 0 || before.lastIndexOf("}}") > open) return { suggestions: [] };
      const word = before.slice(open + 2).match(/[\w.]*$/)?.[0] || "";
      const range = new monaco.Range(position.lineNumber, position.column - word.length, position.lineNumber, position.column);
      return {
        suggestions: keys.map((k) => ({
          label: k.label, kind: monaco.languages.CompletionItemKind.Variable, insertText: k.label.replace(/<.*?>/g, ""),
          detail: k.detail, documentation: k.doc ? { value: k.doc } : undefined, range,
        })),
      };
    },
  };
  keyProvider = monaco.languages.registerCompletionItemProvider(["yaml", "plaintext", "shell", "ini", "python"], provider);
}

/** A value of a rendered file: its place (UTF-16 offsets) and what to show on hover (Markdown). */
export interface ValueHover {
  start: number;
  end: number;
  hover: string;
}

// values of rendered files, by model URI, sorted by start
const valueSets = new Map<string, ValueHover[]>();
let valueProvider: monaco.IDisposable | null = null;

export function setValueHovers(modelUri: string, values: ValueHover[] | null) {
  const key = monaco.Uri.parse(modelUri).toString();      // as the model gives it back
  if (values?.length) valueSets.set(key, values);
  else valueSets.delete(key);
  if (valueProvider) return;
  valueProvider = monaco.languages.registerHoverProvider(["yaml", "plaintext", "shell", "ini", "python", "markdown"], {
    provideHover(model, position) {
      const values = valueSets.get(model.uri.toString());
      if (!values) return null;
      const offset = model.getOffsetAt(position);
      let lo = 0;
      let hi = values.length - 1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (values[mid].end <= offset) lo = mid + 1;
        else if (values[mid].start > offset) hi = mid - 1;
        else {
          const v = values[mid];
          const a = model.getPositionAt(v.start);
          const b = model.getPositionAt(v.end);
          return { range: new monaco.Range(a.lineNumber, a.column, b.lineNumber, b.column), contents: [{ value: v.hover }] };
        }
      }
      return null;
    },
  });
}

export interface Marker {
  line?: number;
  column?: number;
  message: string;
  severity?: string;
}

export function languageOf(path: string): string {
  if (/\.(ya?ml)$/.test(path)) return "yaml";
  if (/\.(sh|bash)$/.test(path)) return "shell";
  if (/\.py$/.test(path)) return "python";
  if (/\.(md|markdown)$/.test(path)) return "markdown";
  if (/\.(ini|cfg|conf|toml)$/.test(path)) return "ini";
  return "plaintext";
}

const TOP_KEY = /^(?!-|#|\s)("[^"]+"|'[^']+'|[^:#\s][^:#]*?)\s*:(\s|$)/;

/** The top-level keys of a YAML text with their line: the sections of a key file or a setup. */
export function yamlSections(text: string): { key: string; line: number }[] {
  const out: { key: string; line: number }[] = [];
  text.split("\n").forEach((l, i) => {
    const m = l.match(TOP_KEY);
    if (m) out.push({ key: m[1].replace(/^["']|["']$/g, ""), line: i + 1 });
  });
  return out;
}

/**
 * The lines where a rule separates two top-level YAML sections: the first line of each section after
 * the first one, or of the comment lines just above it, which describe it. A one-line key that follows
 * another one directly (a list of scalar settings) stays with it.
 */
export function sectionStarts(text: string): number[] {
  const lines = text.split("\n");
  const starts: number[] = [];
  yamlSections(text).slice(1).forEach(({ line }) => {
    let first = line - 1;
    while (first > 0 && lines[first - 1].startsWith("#")) first--;
    const block = /:\s*(#.*)?$/.test(lines[line - 1]);
    const apart = first > 0 && !lines[first - 1].trim();
    if (block || apart) starts.push(first + 1);
  });
  return starts;
}

export function CodeEditor({
  uri, value, onChange, language, readOnly, markers, height, onSave, schema, keys, revealLine, reveal, sections, values,
}: {
  uri: string; value: string; onChange?: (v: string) => void; language?: string; readOnly?: boolean; markers?: Marker[];
  height?: number | string; onSave?: () => void; schema?: any; keys?: { label: string; detail?: string; doc?: string }[];
  revealLine?: number | null;
  /** a line to show at the top of the editor (a new object each time, to go there again) */
  reveal?: { line: number } | null;
  /** draw a rule above each top-level YAML section */
  sections?: boolean;
  /** values of a rendered file: underlined, with where they come from on hover */
  values?: ValueHover[] | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const editor = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const changeRef = useRef(onChange);
  changeRef.current = onChange;
  const saveRef = useRef(onSave);
  saveRef.current = onSave;
  const settingValue = useRef(false);

  useEffect(() => {
    const modelUri = monaco.Uri.parse(uri);
    let model = monaco.editor.getModel(modelUri);
    if (!model) model = monaco.editor.createModel(value, language || languageOf(uri), modelUri);
    else if (model.getValue() !== value) model.setValue(value);
    const instance = monaco.editor.create(box.current!, {
      model, readOnly, automaticLayout: true, minimap: { enabled: false }, fontSize: 13, tabSize: 2, fixedOverflowWidgets: true,
      scrollBeyondLastLine: false, wordWrap: "off", quickSuggestions: { other: true, comments: false, strings: true },
      theme: window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "vs-dark" : "vs",
    });
    editor.current = instance;
    const sub = model.onDidChangeContent(() => {
      if (!settingValue.current) changeRef.current?.(model!.getValue());
    });
    instance.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveRef.current?.());
    return () => {
      sub.dispose();
      instance.dispose();
      editor.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uri]);

  useEffect(() => {
    const model = editor.current?.getModel();
    if (model && model.getValue() !== value) {
      settingValue.current = true;
      model.pushEditOperations([], [{ range: model.getFullModelRange(), text: value }], () => null);
      settingValue.current = false;
    }
  }, [value]);

  useEffect(() => {
    editor.current?.updateOptions({ readOnly });
  }, [readOnly]);

  useEffect(() => {
    if (schema !== undefined) setSchema(uri, schema);
  }, [uri, schema]);

  useEffect(() => {
    setKeys(uri, keys || null);
  }, [uri, keys]);

  useEffect(() => {
    const model = editor.current?.getModel();
    if (!model) return;
    monaco.editor.setModelMarkers(model, "tsq", (markers || []).map((m) => {
      const line = Math.min(Math.max(1, m.line || 1), model.getLineCount());
      return {
        startLineNumber: line, startColumn: m.column || 1, endLineNumber: line, endColumn: model.getLineMaxColumn(line),
        message: m.message,
        severity: m.severity === "warning" ? monaco.MarkerSeverity.Warning : m.severity === "info" ? monaco.MarkerSeverity.Info : monaco.MarkerSeverity.Error,
      };
    }));
  }, [markers, value]);

  // a rule above each top-level section (and the comments that introduce it)
  const ruleDecorations = useRef<monaco.editor.IEditorDecorationsCollection | null>(null);
  useEffect(() => {
    const instance = editor.current;
    if (!instance) return;
    try {
      ruleDecorations.current?.clear();
    } catch {
      // the collection of an editor already disposed (another file opened)
    }
    ruleDecorations.current = instance.createDecorationsCollection(sections ? sectionStarts(value).map((line) => ({
      range: new monaco.Range(line, 1, line, 1),
      options: { isWholeLine: true, className: "yaml-section-rule", linesDecorationsClassName: "yaml-section-gutter" },
    })) : []);
  }, [uri, value, sections]);

  const valueDecorations = useRef<monaco.editor.IEditorDecorationsCollection | null>(null);
  useEffect(() => {
    const instance = editor.current;
    const model = instance?.getModel();
    if (!instance || !model) return;
    setValueHovers(uri, values || null);
    try {
      valueDecorations.current?.clear();
    } catch {
      // the collection of an editor already disposed
    }
    valueDecorations.current = instance.createDecorationsCollection((values || []).map((v) => {
      const a = model.getPositionAt(v.start);
      const b = model.getPositionAt(v.end);
      return { range: new monaco.Range(a.lineNumber, a.column, b.lineNumber, b.column), options: { inlineClassName: "rendered-value" } };
    }));
    return () => setValueHovers(uri, null);
  }, [uri, value, values]);

  useEffect(() => {
    if (reveal && editor.current) {
      editor.current.revealLineNearTop(reveal.line);
      editor.current.setPosition({ lineNumber: reveal.line, column: 1 });
      editor.current.focus();
    }
  }, [reveal]);

  useEffect(() => {
    if (revealLine && editor.current) {
      editor.current.revealLineInCenter(revealLine);
      editor.current.setPosition({ lineNumber: revealLine, column: 1 });
      editor.current.focus();
    }
  }, [revealLine]);

  return <div className="code-editor" ref={box} style={{ height: height || "100%" }} />;
}

export function DiffView({ original, modified, language, height }: { original: string; modified: string; language?: string; height?: number | string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const diff = monaco.editor.createDiffEditor(box.current!, {
      readOnly: true, automaticLayout: true, renderSideBySide: false, minimap: { enabled: false }, fontSize: 12,
    });
    const a = monaco.editor.createModel(original, language || "yaml");
    const b = monaco.editor.createModel(modified, language || "yaml");
    diff.setModel({ original: a, modified: b });
    return () => {
      diff.dispose();
      a.dispose();
      b.dispose();
    };
  }, [original, modified, language]);
  return <div className="code-editor" ref={box} style={{ height: height || 300 }} />;
}

export { monaco };
