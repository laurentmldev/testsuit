// Links between the lab's roles, components and inventory hosts in the editor: a click on a name opens the
// file that defines it, on that item (`?file=…&item=<kind>:<name>`, consumed by the form of that file).
import { Fragment, useEffect, type ReactNode } from "react";
import { setQuery, useRoute } from "../util";
import { hostLabel, useSession, type Session } from "./state";

export type ItemKind = "role" | "component" | "host";

/** The session file that defines a role, a library component or an inventory host, when it is editable here. */
export function itemFile(session: Session, kind: ItemKind, name: string): string | null {
  const { ctx, files } = session;
  const source: string | undefined = kind === "role" ? ctx.lab.roles?.[name]?.source
    : kind === "component" ? ctx.lab.library?.[name]?.source
    : ctx.lab.components?.[name] ? ctx.inventory : undefined;
  return source && files.some((f) => f.path === source) ? source : null;
}

/** What an `extends` names: a library component, or an inventory host (hosts can extend hosts). */
export function extendsKind(session: Session, name: string): ItemKind {
  return session.ctx.lab.library?.[name] || !session.ctx.lab.components?.[name] ? "component" : "host";
}

export function goTo(session: Session, file: string, kind: ItemKind, name: string) {
  setQuery("view", null);
  setQuery("item", `${kind}:${name}`);
  session.open(file);
}

/** A role, component or host name that opens its editor when clicked (plain code when not editable here). */
export function ItemLink({ kind, name, label }: { kind: ItemKind; name: string; label?: ReactNode }) {
  const session = useSession();
  if (!name) return null;
  const text = label ?? (kind === "host" ? hostLabel(session.ctx, name) : name);
  const file = itemFile(session, kind, name);
  if (!file) return <code>{text}</code>;
  return (
    <button type="button" className="link item-link" title={`Edit the ${kind === "host" ? "inventory component" : kind} ${name} (${file})`}
      onClick={(e) => { e.stopPropagation(); goTo(session, file, kind, name); }}><code>{text}</code></button>
  );
}

/** Several of them, comma separated. */
export function ItemLinks({ kind, names }: { kind: ItemKind; names: string[] }) {
  return <>{names.map((n, i) => <Fragment key={n}>{i > 0 && ", "}<ItemLink kind={kind} name={n} /></Fragment>)}</>;
}

/** Selects the item a link asked for, once the form of its file is open (then forgets the request). */
export function useItemRequest(path: string, select: (kind: ItemKind, name: string) => boolean | void) {
  const route = useRoute();
  const item = route.query.get("item");
  const file = route.query.get("file");
  useEffect(() => {
    if (!item || file !== path) return;
    const i = item.indexOf(":");
    select(item.slice(0, i) as ItemKind, item.slice(i + 1));
    setQuery("item", null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item, file, path]);
}
