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

// The long texts of requirements (description, rationale, comments): Markdown, or HTML as tools such as ALMs and
// issue trackers send it. HTML is rebuilt as React elements from an allowlist of tags and attributes: no script,
// style, event handler or javascript: link can come through.
import { createElement, Fragment, type ReactNode } from "react";
import { Markdown } from "./util";

const TAGS = new Set(["p", "br", "div", "span", "ul", "ol", "li", "b", "strong", "i", "em", "u", "s", "del", "ins", "sub", "sup",
  "code", "pre", "blockquote", "a", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
  "h1", "h2", "h3", "h4", "h5", "h6", "hr", "dl", "dt", "dd", "small", "mark", "kbd", "img", "figure", "figcaption"]);
const DROP = new Set(["script", "style", "iframe", "object", "embed", "noscript", "template", "head", "title", "link", "meta",
  "form", "input", "button", "select", "textarea", "svg", "math"]);          // left out with their content
const VOID = new Set(["br", "hr", "col", "img"]);
const SAFE_URL = /^(https?:|mailto:|#|\/|\.{0,2}\/|[^:]*$)/i;

/** True when a text is HTML rather than Markdown or plain text: it opens with a tag, or holds block or inline tags. */
export function isHtml(text: string): boolean {
  const t = text.trim();
  return /^<([a-z][a-z0-9]*)\b[^>]*>/i.test(t) && /<\/[a-z][a-z0-9]*>|<br\s*\/?>/i.test(t)
    || /<(p|div|br|ul|ol|li|table|tr|td|strong|em|b|i|span|a\s+href)\b[^>]*>/i.test(t) && /<\/(p|div|ul|ol|li|table|strong|em|b|i|span|a)>/i.test(t);
}

/** Line breaks written as the two characters \n (or \r\n), as some tools send them (a JSON string escaped twice):
 * real line breaks. */
export function unescapeBreaks(text: string): string {
  return text.includes("\\n") ? text.replace(/\\r\\n|\\n/g, "\n") : text;
}

/** The text of HTML, without its tags (a one-line summary). */
export function htmlToText(raw: string): string {
  const text = unescapeBreaks(raw);
  if (!isHtml(text)) return text;
  const doc = new DOMParser().parseFromString(text, "text/html");
  doc.querySelectorAll("br, p, div, li, tr, h1, h2, h3, h4, h5, h6").forEach((el) => el.append("\n"));
  return (doc.body.textContent || "").replace(/\n\s*\n+/g, "\n").trim();
}

function convert(node: Node, key: number): ReactNode {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  const el = node as Element;
  const tag = el.tagName.toLowerCase();
  if (DROP.has(tag)) return null;
  const children = Array.from(el.childNodes).map((c, i) => convert(c, i));
  if (!TAGS.has(tag)) return <Fragment key={key}>{children}</Fragment>;       // unknown tag: its content only
  const props: Record<string, any> = { key };
  if (tag === "a") {
    const href = el.getAttribute("href") || "";
    if (SAFE_URL.test(href.trim())) Object.assign(props, { href, target: "_blank", rel: "noreferrer noopener" });
  }
  if (tag === "img") {
    const src = el.getAttribute("src") || "";
    if (!/^https?:/i.test(src)) return el.getAttribute("alt") || null;      // no data: nor relative (tool-side) images
    Object.assign(props, { src, alt: el.getAttribute("alt") || "", loading: "lazy", referrerPolicy: "no-referrer" });
  }
  for (const a of ["colspan", "rowspan"]) if (el.hasAttribute(a) && (tag === "td" || tag === "th")) props[a === "colspan" ? "colSpan" : "rowSpan"] = Number(el.getAttribute(a)) || 1;
  if (el.getAttribute("title")) props.title = el.getAttribute("title");
  return VOID.has(tag) ? createElement(tag, props) : createElement(tag, props, ...children);
}

/** A long text, as HTML when it is (safe tags only), else as Markdown. */
export function RichText({ text: raw, className }: { text?: string | null; className?: string }) {
  if (!raw) return null;
  if (!isHtml(raw)) return <Markdown text={unescapeBreaks(raw)} className={className} />;
  const text = raw.replace(/\\r\\n|\\n/g, "<br>");          // in HTML, an escaped line break is a <br>
  const doc = new DOMParser().parseFromString(text, "text/html");
  return <div className={["rich-html", className].filter(Boolean).join(" ")}>{Array.from(doc.body.childNodes).map((c, i) => convert(c, i))}</div>;
}
