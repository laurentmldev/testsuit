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

// Links between the lab's roles, components and inventory hosts in the editor: a click on a name opens the
// file that defines it, on that item (`?file=…&item=<kind>:<name>`, consumed by the form of that file), and on
// one of its commands opened for editing with `&command=<name>` (the Run page's "Edit the command" of a step).
import { Fragment, useEffect, type ReactNode } from "react";
import { setQuery, useRoute } from "../util";
import { hostLabel, useSession, type Session } from "./state";

export type ItemKind = "role" | "component" | "host" | "stage";

/** The session file that defines a role, a library component, an inventory host or stage, when it is editable here. */
export function itemFile(session: Session, kind: ItemKind, name: string): string | null {
  const { ctx, files } = session;
  const source: string | undefined = kind === "role" ? ctx.lab.roles?.[name]?.source
    : kind === "component" ? ctx.lab.library?.[name]?.source
    : kind === "stage" ? ctx.inventory
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
  const what = kind === "host" ? "inventory component" : kind === "component" ? "class" : kind;
  const file = itemFile(session, kind, name);
  if (!file) return <code>{text}</code>;
  return (
    <button type="button" className="link item-link" title={`Edit the ${what} ${name} (${file})`}
      onClick={(e) => { e.stopPropagation(); goTo(session, file, kind, name); }}><code>{text}</code></button>
  );
}

/** Several of them, comma separated. */
export function ItemLinks({ kind, names }: { kind: ItemKind; names: string[] }) {
  return <>{names.map((n, i) => <Fragment key={n}>{i > 0 && ", "}<ItemLink kind={kind} name={n} /></Fragment>)}</>;
}

/** A command of the selected item to open for editing, asked by a link; a new object for each request, so the
 *  same command asked again opens again. */
export type CommandRequest = { name: string } | null;

/** Selects the item a link asked for, once the form of its file is open (then forgets the request); command is
 *  the command of that item to open, when the link asked for one. */
export function useItemRequest(path: string, select: (kind: ItemKind, name: string, command: CommandRequest) => boolean | void) {
  const route = useRoute();
  const item = route.query.get("item");
  const command = route.query.get("command");
  const file = route.query.get("file");
  useEffect(() => {
    if (!item || file !== path) return;
    const i = item.indexOf(":");
    select(item.slice(0, i) as ItemKind, item.slice(i + 1), command ? { name: command } : null);
    setQuery("item", null);
    setQuery("command", null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item, command, file, path]);
}
