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

export function CodeEditor({
  uri, value, onChange, language, readOnly, markers, height, onSave, schema, keys, revealLine,
}: {
  uri: string; value: string; onChange?: (v: string) => void; language?: string; readOnly?: boolean; markers?: Marker[];
  height?: number | string; onSave?: () => void; schema?: any; keys?: { label: string; detail?: string; doc?: string }[];
  revealLine?: number | null;
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
      model, readOnly, automaticLayout: true, minimap: { enabled: false }, fontSize: 13, tabSize: 2,
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
