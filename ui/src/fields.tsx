// Form controls of the Editor. Each field carries its help: it shows in the help panel when the field is
// focused or hovered, and its choices come with a description (component types with their commands,
// roles with the components that have them...).
import { createContext, ReactNode, useContext, useEffect, useMemo, useRef, useState } from "react";
import { classes, idInput, idProblem, Markdown } from "./util";

// ── help panel ──────────────────────────────────────────────────────────

export interface HelpTopic {
  title: string;
  text?: string;
  extra?: ReactNode;
}

const HelpContext = createContext<(topic: HelpTopic | null) => void>(() => {});

export function HelpProvider({ children, panel }: { children: (topic: HelpTopic | null) => ReactNode; panel?: boolean }) {
  const [topic, setTopic] = useState<HelpTopic | null>(null);
  return <HelpContext.Provider value={setTopic}>{children(panel === false ? null : topic)}</HelpContext.Provider>;
}

export function useHelp() {
  return useContext(HelpContext);
}

export function HelpPanel({ topic, fallback }: { topic: HelpTopic | null; fallback?: ReactNode }) {
  return (
    <aside className="help-panel">
      {topic ? (
        <>
          <h4>{topic.title}</h4>
          <Markdown text={topic.text} />
          {topic.extra}
        </>
      ) : (
        fallback || <p className="muted">Point at a field to see what it does.</p>
      )}
    </aside>
  );
}

// ── schema help lookup ─────────────────────────────────────────────────

/** The schema node describing the data at path (keys and list indexes), following properties,
 * additionalProperties and items. */
export function schemaAt(schema: any, path: (string | number)[]): any {
  let node = schema;
  for (const key of path) {
    if (!node) return null;
    if (typeof key === "number" || /^\d+$/.test(String(key))) node = node.items ?? null;
    else node = node.properties?.[key] ?? (typeof node.additionalProperties === "object" ? node.additionalProperties : null);
  }
  return node;
}

export function helpText(schema: any, path: (string | number)[]): string | undefined {
  const node = schemaAt(schema, path);
  return node?.markdownDescription || node?.description;
}

// ── keys: typing {{ in a field whose value is rendered with keys ───────────

export interface KeyItem {
  key: string;
  value?: string;      // its value, when known (datapack keys)
  help?: string;       // what it is (run-time keys)
  origin?: string;     // the file it comes from
}

const KeysContext = createContext<KeyItem[] | null>(null);

/** The keys available where the fields below it are rendered (run-time keys, a test's datapack keys). */
export function KeysProvider({ keys, children }: { keys: KeyItem[]; children: ReactNode }) {
  return <KeysContext.Provider value={keys}>{children}</KeysContext.Provider>;
}

type Editable = HTMLInputElement | HTMLTextAreaElement;

/** For a field rendered with keys: typing `{{` opens a list of the keys of the context with a quick
 * search; picking one writes `{{ key }}` where `{{` was typed. */
function useKeyPicker(enabled: boolean | undefined, onChange: (v: string) => void) {
  const keys = useContext(KeysContext);
  const [at, setAt] = useState<{ el: Editable; pos: number } | null>(null);
  const on = !!enabled && !!keys;
  const watch = (el: Editable) => {
    if (!on) return;
    const pos = el.selectionStart ?? el.value.length;
    if (el.value.slice(Math.max(0, pos - 2), pos) === "{{") setAt({ el, pos });
  };
  const insert = (key: string) => {
    if (!at) return;
    const { el, pos } = at;
    const before = el.value.slice(0, pos - 2), after = el.value.slice(pos).replace(/^\s*}}/, "");
    const text = `{{ ${key} }}`;
    onChange(before + text + after);
    setAt(null);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(before.length + text.length, before.length + text.length);
    });
  };
  const close = () => {
    const el = at?.el;
    setAt(null);
    requestAnimationFrame(() => el?.focus());
  };
  const picker = on && at ? <KeyPicker keys={keys!} onPick={insert} onClose={close} /> : null;
  return { watch, picker, open: !!at, hint: on ? "Type {{ to insert a key." : undefined };
}

function KeyPicker({ keys, onPick, onClose }: { keys: KeyItem[]; onPick: (key: string) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const found = keys.filter((k) => words.every((w) => `${k.key} ${k.value ?? ""} ${k.help ?? ""}`.toLowerCase().includes(w))).slice(0, 80);
    // a key the editor does not know (from a dictionary not chosen yet, a plugin...) can still be written
    const typed = q.trim();
    return typed && !keys.some((k) => k.key === typed) ? [...found, { key: typed, typed: true }] : found;
  }, [keys, q]) as (KeyItem & { typed?: boolean })[];
  useEffect(() => {
    const away = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && onClose();
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [onClose]);
  useEffect(() => {
    box.current?.querySelector(".key-item.active")?.scrollIntoView({ block: "nearest" });
  }, [active]);
  return (
    <div className="key-picker" ref={box} role="dialog" aria-label="Insert a key">
      <input className="key-search" autoFocus placeholder="search keys…" value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, shown.length - 1)), e.preventDefault();
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0)), e.preventDefault();
          else if (e.key === "Enter") {
            e.preventDefault();
            if (shown[active]) onPick(shown[active].key);
          } else if (e.key === "Escape") e.preventDefault(), onClose();
        }} />
      <div className="key-list">
        {shown.length === 0 && <div className="muted small pad-s">Type a key name; the list narrows as you type.</div>}
        {shown.map((k, i) => (
          <div key={(k.typed ? "typed:" : "") + k.key} className={classes("key-item", i === active && "active", k.typed && "typed")}
            onMouseEnter={() => setActive(i)}
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(k.key);
            }}>
            <code>{k.key}</code>
            {k.typed && <span className="key-help">not a known key: insert it as typed</span>}
            {k.value !== undefined && <span className="key-value" title={k.value}>{k.value}</span>}
            {k.help && <span className="key-help">{k.help}</span>}
            {k.origin && <span className="key-origin">{k.origin}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── fields ──────────────────────────────────────────────────────────────

export function Field({
  label, help, children, error, hint, wide,
}: { label: string; help?: string; children: ReactNode; error?: string | null; hint?: ReactNode; wide?: boolean }) {
  const setHelp = useHelp();
  const show = () => help && setHelp({ title: label, text: help });
  return (
    <div className={classes("field", wide && "wide", error && "has-error")} onFocusCapture={show} onMouseEnter={show}>
      <label>
        {label}
        {help && (
          <span className="q" title={help.replace(/[`*]/g, "")}>
            ?
          </span>
        )}
      </label>
      <div className="control">
        {children}
        {hint && <div className="hint">{hint}</div>}
        {error && <div className="error-text">{error}</div>}
      </div>
    </div>
  );
}

/** An id field: what is typed is kept as typed (in id characters), and handed to onChange only while
 * it is a valid id (not empty, not taken); otherwise the field shows red and the document keeps the
 * last valid id. */
export function IdText({ value, onChange, taken = [], placeholder, className }: {
  value?: string | null; onChange: (id: string) => void; taken?: string[]; placeholder?: string; className?: string;
}) {
  const [draft, setDraft] = useState(value || "");
  useEffect(() => setDraft(value || ""), [value]);
  const problem = idProblem(draft, taken.filter((t) => t !== value));
  return (
    <input className={classes("mono", className, problem && "invalid")} value={draft} placeholder={placeholder}
      title={problem ? `not a valid id: ${problem}` : undefined}
      onChange={(e) => {
        const v = idInput(e.target.value);
        setDraft(v);
        if (!idProblem(v, taken.filter((t) => t !== value)) && v !== value) onChange(v);
      }}
      onBlur={() => setDraft(value || "")} />
  );
}

export function Text({
  value, onChange, placeholder, mono, multiline, disabled, onBlur, keys, invalid,
}: {
  value?: string | number | null; onChange: (v: string) => void; placeholder?: string; mono?: boolean;
  multiline?: boolean; disabled?: boolean; onBlur?: () => void; invalid?: boolean;
  keys?: boolean;      // the value is rendered with keys: typing {{ proposes them
}) {
  const v = value === null || value === undefined ? "" : String(value);
  const picker = useKeyPicker(keys, onChange);
  const change = (e: { target: Editable }) => {
    onChange(e.target.value);
    picker.watch(e.target);
  };
  return (
    <div className={classes("text-field", picker.picker && "picking")} title={picker.hint}>
      {multiline ? (
        <textarea className={classes(mono && "mono")} value={v} placeholder={placeholder} disabled={disabled}
          rows={Math.min(8, Math.max(2, v.split("\n").length))} onChange={change} onBlur={onBlur} />
      ) : (
        <input className={classes(mono && "mono", invalid && "invalid")} value={v} placeholder={placeholder} disabled={disabled}
          onChange={change} onBlur={onBlur} />
      )}
      {picker.picker}
    </div>
  );
}

export function Toggle({ value, onChange, label }: { value?: boolean | null; onChange: (v: boolean) => void; label?: string }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export interface Choice {
  value: string;
  label?: string;
  description?: string;
  group?: string;
}

/** Radio-like choice with a description per option, for small fixed sets (lock mode, method). */
export function Choices({ value, onChange, choices, allowNone }: {
  value?: string | null; onChange: (v: string | null) => void; choices: Choice[]; allowNone?: string;
}) {
  return (
    <div className="choices">
      {allowNone && (
        <button type="button" className={classes("choice", !value && "selected")} onClick={() => onChange(null)}>
          <strong>{allowNone}</strong>
        </button>
      )}
      {choices.map((c) => (
        <button type="button" key={c.value} className={classes("choice", value === c.value && "selected")}
          onClick={() => onChange(c.value)} title={c.description}>
          <strong>{c.label || c.value}</strong>
          {c.description && <span><Markdown text={c.description} /></span>}
        </button>
      ))}
    </div>
  );
}

/** A text input with suggestions: each suggestion shows its description, free text stays possible
 * (strict: only the choices). */
export function Combo({
  value, onChange, choices, placeholder, strict, mono, onPick, autoFocus, keys,
}: {
  value?: string | null; onChange: (v: string) => void; choices: Choice[]; placeholder?: string; strict?: boolean;
  mono?: boolean; onPick?: (v: string) => void; autoFocus?: boolean;
  keys?: boolean;      // the value is rendered with keys: typing {{ proposes them
}) {
  const picker = useKeyPicker(keys, onChange);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [typed, setTyped] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const v = value ?? "";
  const shown = useMemo(() => {
    const q = typed ? v.toLowerCase() : "";
    return choices.filter((c) => !q || c.value.toLowerCase().includes(q) || (c.label || "").toLowerCase().includes(q)).slice(0, 60);
  }, [choices, v, typed]);
  useEffect(() => {
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const pick = (c: Choice) => {
    onChange(c.value);
    onPick?.(c.value);
    setOpen(false);
    setTyped(false);
  };
  const known = choices.find((c) => c.value === v);
  return (
    <div className="combo" ref={box}>
      <input className={classes(mono && "mono", strict && v && !known && "invalid")} value={v} placeholder={placeholder}
        autoFocus={autoFocus}
        onFocus={() => {
          setOpen(true);
          setTyped(false);
        }}
        onChange={(e) => {
          onChange(e.target.value);
          setTyped(true);
          setOpen(true);
          setActive(0);
          picker.watch(e.target);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, shown.length - 1)), setOpen(true), e.preventDefault();
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0)), e.preventDefault();
          else if (e.key === "Enter" && open && shown[active]) pick(shown[active]), e.preventDefault();
          else if (e.key === "Escape") setOpen(false);
        }} />
      {known?.description && !open && <div className="combo-desc"><Markdown text={known.description} /></div>}
      {picker.picker}
      {open && !picker.open && shown.length > 0 && (
        <div className="combo-list">
          {shown.map((c, i) => (
            <div key={c.value} className={classes("combo-item", i === active && "active", c.value === v && "current")}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(c);
              }} onMouseEnter={() => setActive(i)}>
              <div className="combo-value">
                {c.label || c.value}
                {c.group && <span className="combo-group">{c.group}</span>}
              </div>
              {c.description && <div className="combo-item-desc"><Markdown text={c.description} /></div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** A list of short strings (needs, stages, components...) as removable tags, with suggestions. */
export function Tags({ value, onChange, choices, placeholder, keys }: {
  value?: string[] | null; onChange: (v: string[]) => void; choices?: Choice[]; placeholder?: string;
  keys?: boolean;      // the items are rendered with keys: typing {{ proposes them
}) {
  const list = value || [];
  const [draft, setDraft] = useState("");
  const remaining = (choices || []).filter((c) => !list.includes(c.value));
  const add = (v: string) => {
    const t = v.trim();
    if (t && !list.includes(t)) onChange([...list, t]);
    setDraft("");
  };
  return (
    <div className="tags">
      {list.map((t) => (
        <span key={t} className="tag">
          {t}
          <button type="button" onClick={() => onChange(list.filter((x) => x !== t))} title="Remove">×</button>
        </span>
      ))}
      <div className="tag-input">
        <Combo value={draft} onChange={setDraft} choices={remaining} placeholder={placeholder || "add…"} onPick={add} keys={keys} />
        {draft && <button type="button" className="small" onClick={() => add(draft)}>add</button>}
      </div>
    </div>
  );
}

/** Variables as a key/value table; known keys are proposed with their help; values are YAML scalars. */
export function KeyValues({ value, onChange, known, exclude, valueChoices }: {
  value?: Record<string, any> | null; onChange: (v: Record<string, any>) => void; known?: Choice[]; exclude?: string[];
  valueChoices?: (key: string) => Choice[] | undefined;
}) {
  const entries = Object.entries(value || {}).filter(([k]) => !(exclude || []).includes(k));
  const [newKey, setNewKey] = useState("");
  const setHelp = useHelp();
  const set = (k: string, v: any) => onChange({ ...(value || {}), [k]: v });
  const remove = (k: string) => {
    const copy = { ...(value || {}) };
    delete copy[k];
    onChange(copy);
  };
  const rename = (k: string, to: string) => {
    if (!to || to === k) return;
    const copy: Record<string, any> = {};
    for (const [key, v] of Object.entries(value || {})) copy[key === k ? to : key] = v;
    onChange(copy);
  };
  return (
    <div className="kv">
      {entries.map(([k, v]) => {
        const info = known?.find((c) => c.value === k);
        return (
          <div key={k} className="kv-row" onMouseEnter={() => info?.description && setHelp({ title: k, text: info.description })}>
            <input className="mono kv-key" defaultValue={k} onBlur={(e) => rename(k, e.target.value.trim())} />
            {typeof v === "object" && v !== null ? (
              <span className="muted mono kv-value">{JSON.stringify(v)}</span>
            ) : valueChoices?.(k) ? (
              <div className="kv-value"><Combo value={v === null ? "" : String(v)} onChange={(x) => set(k, scalar(x))} choices={valueChoices(k)!} mono /></div>
            ) : (
              <input className="mono kv-value" value={v === null ? "" : String(v)} onChange={(e) => set(k, scalar(e.target.value))} />
            )}
            <button type="button" className="icon" onClick={() => remove(k)} title="Remove">×</button>
          </div>
        );
      })}
      <div className="kv-row">
        <div className="kv-key">
          <Combo value={newKey} onChange={setNewKey} choices={(known || []).filter((c) => !(c.value in (value || {})))}
            placeholder="new variable…" mono onPick={(k) => {
              set(k, "");
              setNewKey("");
            }} />
        </div>
        {newKey && <button type="button" className="small" onClick={() => {
          set(newKey.trim(), "");
          setNewKey("");
        }}>add</button>}
      </div>
    </div>
  );
}

/** A YAML scalar typed in a form: numbers and booleans as such, the rest as text. */
export function scalar(text: string): any {
  if (/^-?\d+$/.test(text) && text.length < 16) return Number(text);
  if (/^-?\d+\.\d+$/.test(text)) return Number(text);
  if (text === "true") return true;
  if (text === "false") return false;
  return text;
}

export function Section({ title, children, actions, help, collapsed }: {
  title: ReactNode; children: ReactNode; actions?: ReactNode; help?: string; collapsed?: boolean;
}) {
  const [open, setOpen] = useState(!collapsed);
  const setHelp = useHelp();
  return (
    <section className="form-section">
      <header onMouseEnter={() => help && setHelp({ title: typeof title === "string" ? title : "", text: help })}>
        <button type="button" className="fold" onClick={() => setOpen(!open)}>{open ? "▾" : "▸"}</button>
        <h3 onClick={() => setOpen(!open)}>{title}</h3>
        <div className="actions">{actions}</div>
      </header>
      {open && <div className="form-body">{children}</div>}
    </section>
  );
}

/** Modal dialog. */
export function Dialog({ title, children, onClose, footer, wide }: {
  title: string; children: ReactNode; onClose: () => void; footer?: ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={classes("dialog", wide && "wide")} role="dialog" aria-label={title}>
        <header>
          <h3>{title}</h3>
          <button type="button" className="icon" onClick={onClose} title="Close">×</button>
        </header>
        <div className="dialog-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>
  );
}
