// Review the changes of the session and push them (sequencer README, section 13.2): the diff, a commit message
// and an optional tag, the author (kept in this browser), and credentials used for this push only.
import { useEffect, useState } from "react";
import { get, post } from "../api";
import { Choices, Dialog, Field, Text } from "../fields";
import { DiffView } from "../monaco";
import { attempt, classes, Markdown } from "../util";
import { useSession } from "./state";

const AUTHOR = "tsq.author";

function nextTag(tag?: string | null): string {
  const m = tag?.match(/^(.*?)(\d+)$/);
  return m ? `${m[1]}${Number(m[2]) + 1}` : "";
}

/** One diff per file, out of a unified patch. */
function splitPatch(patch: string): Record<string, { before: string; after: string }> {
  const result: Record<string, { before: string; after: string }> = {};
  for (const chunk of patch.split(/^diff --git /m).slice(1)) {
    const name = chunk.match(/^\+\+\+ b\/(.+)$/m)?.[1] || chunk.match(/^--- a\/(.+)$/m)?.[1] || chunk.split("\n")[0];
    const before: string[] = [];
    const after: string[] = [];
    let inHunk = false;
    for (const line of chunk.split("\n")) {
      if (line.startsWith("@@")) {
        if (inHunk) {
          before.push("⋯");
          after.push("⋯");
        }
        inHunk = true;
        continue;
      }
      if (!inHunk) continue;
      if (line.startsWith("-")) before.push(line.slice(1));
      else if (line.startsWith("+")) after.push(line.slice(1));
      else if (line.startsWith(" ")) {
        before.push(line.slice(1));
        after.push(line.slice(1));
      }
    }
    result[name] = { before: before.join("\n"), after: after.join("\n") };
  }
  return result;
}

export function PublishDialog({ onClose }: { onClose: () => void }) {
  const { sid, info } = useSession();
  const [diff, setDiff] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<string | null>(null);
  const stored = (() => {
    try {
      return JSON.parse(localStorage.getItem(AUTHOR) || "{}");
    } catch {
      return {};
    }
  })();
  const [name, setName] = useState<string>(stored.name || "");
  const [email, setEmail] = useState<string>(stored.email || "");
  const lastTag = [...(info.pushed || [])].reverse().find((p: any) => p.tag)?.tag;
  const [tag, setTag] = useState<string>(nextTag(lastTag));
  const [message, setMessage] = useState("");
  const [auth, setAuth] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);

  const load = () => get(`edit/sessions/${sid}/diff`).then((d) => {
    setDiff(d);
    if (!message) setMessage(suggest(d.files));
    setFile(d.files[0]?.path || null);
  }).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid]);

  const credentials = auth === "token" ? { username, password: secret } : auth === "ssh" ? { passphrase: secret } : {};
  const push = async () => {
    setBusy(true);
    try {
      localStorage.setItem(AUTHOR, JSON.stringify({ name, email }));
    } catch {
      // private window: the author is not remembered
    }
    try {
      setResult(await post(`edit/sessions/${sid}/push`, { message, author_name: name, author_email: email, tag: tag || null, ...credentials }));
    } catch (e: any) {
      setResult({ ok: false, error: e.message });
    } finally {
      setBusy(false);
    }
  };
  const rebase = async () => {
    setBusy(true);
    const r = await attempt(() => post(`edit/sessions/${sid}/rebase`, credentials));
    setBusy(false);
    if (r?.ok) {
      setResult(null);
      load();
    } else if (r) setResult({ ok: false, error: r.error + (r.conflicts?.length ? `: ${r.conflicts.join(", ")}` : "") });
  };

  const files = diff ? splitPatch(diff.patch) : {};
  if (result?.ok)
    return (
      <Dialog title="Pushed" onClose={onClose} footer={<button className="primary" onClick={onClose}>Close</button>}>
        <p>Commit <code>{result.commit.slice(0, 10)}</code> pushed to <strong>{result.branch}</strong>{result.tag && <> with the tag <code>{result.tag}</code></>}.</p>
        {info.kind === "lab" ? (
          <>
            <Markdown text="The running lab is not changed by a push: the operator reloads it when the lab is idle enough. The **Lab** page now shows that a newer configuration is published." />
            <button onClick={() => attempt(() => post("lab/reload", { pull: true, revision: result.tag || null }), "lab reloaded")}>Pull and reload the lab now</button>
          </>
        ) : <p>The next datapacks generated from this repository will carry this revision.</p>}
      </Dialog>
    );
  return (
    <Dialog title="Review and push" onClose={onClose} wide footer={
      <>
        {result && !result.ok && result.hint?.startsWith("the branch moved") && <button onClick={rebase} disabled={busy}>Rebase on the latest revision</button>}
        <button className="primary" disabled={busy || !diff?.files.length || !message.trim() || !name.trim() || !email.trim()} onClick={push}>
          {busy ? "Pushing…" : `Push to ${diff?.branch || info.branch}`}
        </button>
      </>
    }>
      {error && <pre className="error-box">{error}</pre>}
      {diff && !diff.files.length && <p>No change since the session started: nothing to push.</p>}
      {diff && diff.files.length > 0 && (
        <div className="publish">
          <div className="diff-files">
            {diff.files.map((f: any) => (
              <div key={f.path} className={classes("tree-row", file === f.path && "selected")} onClick={() => setFile(f.path)}>
                <span className={classes("status", `st-${f.status[0]}`)}>{f.status[0]}</span> {f.path}
              </div>
            ))}
          </div>
          <div className="diff-view">
            {file && files[file] ? <DiffView key={file} original={files[file].before} modified={files[file].after} height={260} /> : <p className="muted">{file ? "binary or renamed file" : ""}</p>}
          </div>
        </div>
      )}
      {result && !result.ok && (
        <div className="banner error">
          <strong>Not pushed:</strong> {result.error}
          {result.hint && <div>{result.hint}. Your changes are still in the session.</div>}
        </div>
      )}
      <Field label="Message" help="The commit message: what changed and why, for whoever reads the history of the lab or the tests." wide>
        <Text multiline value={message} onChange={setMessage} />
      </Field>
      <div className="inline-fields">
        <Field label="Author" help="Commits are made in your name, as if you had pushed from your own checkout. Kept in this browser."><Text value={name} onChange={setName} placeholder="Your Name" /></Field>
        <Field label="E-mail"><Text value={email} onChange={setEmail} placeholder="you@example.com" /></Field>
        <Field label="Tag" help={`An annotated tag on the commit, pushed with it. ${info.kind === "lab" ? "Operators reload the lab at a tag: `tsq lab reload --pull <tag>`." : "Datapacks record the revision they come from."}${lastTag ? ` Last tag of this session: \`${lastTag}\`.` : ""}`}>
          <Text mono value={tag} onChange={setTag} placeholder="none" />
        </Field>
      </div>
      <Field label="Credentials" help="Used for this push only, never stored: given to git through its askpass, then forgotten. Leave them out when tsqd itself can push (an SSH key of its account, a credential helper).">
        <Choices value={auth} onChange={setAuth} allowNone="those of tsqd" choices={[
          { value: "token", label: "user and token", description: "HTTPS remote: a personal access token" },
          { value: "ssh", label: "SSH key passphrase", description: "SSH remote: the passphrase of tsqd's key" },
        ]} />
      </Field>
      {auth === "token" && (
        <div className="inline-fields">
          <Field label="User"><Text value={username} onChange={setUsername} /></Field>
          <Field label="Token"><input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" /></Field>
        </div>
      )}
      {auth === "ssh" && <Field label="Passphrase"><input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" /></Field>}
    </Dialog>
  );
}

function suggest(files: { status: string; path: string }[]): string {
  if (!files.length) return "";
  const verb = (s: string) => (s.startsWith("A") ? "Add" : s.startsWith("D") ? "Remove" : "Update");
  if (files.length === 1) return `${verb(files[0].status)} ${files[0].path}`;
  const dirs = [...new Set(files.map((f) => f.path.split("/").slice(0, -1).join("/") || "."))];
  return `Update ${files.length} files in ${dirs.slice(0, 3).join(", ")}${dirs.length > 3 ? "…" : ""}`;
}
