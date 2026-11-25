# Copyright (C) 2026 Laurent Millet-Lacombe
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of testsuit.
# testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
# General Public License as published by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
# testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
# Public License for more details.
# You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
# see <https://www.gnu.org/licenses/>.

"""Where the links of the reports lead (SPEC section 11.2): one report generator, three ways to link.

- ``OnlineLinks``: to the sequencer's pages and files, from its root URL (as seen from where the page is served, or
  absolute in a downloaded report; empty: the page is served at the root); a root None gives no link.
- ``PageLinks``: to the pages of an exported report (its own run reports, logs, exploit reports, previews of input
  files), relative to the page being written; a target the export does not hold falls back to the sequencer (its
  absolute root URL, when known) or to no link.

A method gives the URL, or None for no link (the report then shows plain text).
"""
from __future__ import annotations

import posixpath
from urllib.parse import quote


class OnlineLinks:
    """Links to the sequencer: its UI (``#/runs/<id>``) and its API (logs, files, reports)."""

    def __init__(self, base: str | None = ""):
        self.base = base

    def _at(self, path: str) -> str | None:
        return None if self.base is None else f"{self.base}{path}"

    def run(self, run_id: str) -> str | None:
        return self._at(f"#/runs/{quote(run_id)}")

    def campaign(self, campaign_id: str) -> str | None:
        return self._at(f"#/campaigns/{quote(campaign_id)}")

    def run_report(self, run_id: str) -> str | None:
        return self._at(f"runs/{quote(run_id)}/report")

    def log(self, run_id: str, step: str) -> str | None:
        return self._at(f"runs/{quote(run_id)}/logs/{quote(step)}")

    def exploit(self, run_id: str, step: str, path: str = "") -> str | None:
        """An exploit step's report, as reviewed."""
        return self._at(f"runs/{quote(run_id)}/exploit/{quote(step, safe='')}/report")

    def results(self, run_id: str) -> str | None:
        return self._at(f"runs/{quote(run_id)}/files/results.zip?results=true")

    def coverage(self, campaign_id: str | None = None, run_id: str | None = None) -> str | None:
        if campaign_id:
            return self._at(f"campaigns/{quote(campaign_id)}/requirements/report")
        return self._at(f"runs/{quote(run_id or '')}/requirements/report")

    def run_file(self, run_id: str, file: str, line: int = 0) -> str | None:
        return self._at(f"#/runs/{quote(run_id)}?tab=files&file={quote(file, safe='')}" + (f"&line={line}" if line else ""))

    def input_preview(self, run_id: str, file: str) -> str | None:
        return self.run_file(run_id, file)


class PageLinks:
    """Links between the pages of an exported report. pages: run id -> its folder in the export
    (``runs/<test>/attempt-<n>``); held: the paths the export writes (logs, exploit reports, previews...);
    online: where to link what the export does not hold (an OnlineLinks with the absolute root URL, or without)."""

    def __init__(self, pages: dict[str, str], held: set[str], online: OnlineLinks | None = None, current: str = "index.html"):
        self.pages = pages
        self.held = held
        self.online = online or OnlineLinks(None)
        self.current = current

    def at(self, current: str) -> "PageLinks":
        """The same links, written from another page."""
        return PageLinks(self.pages, self.held, self.online, current)

    def rel(self, target: str) -> str:
        return posixpath.relpath(target, posixpath.dirname(self.current) or ".")

    def _held(self, target: str) -> str | None:
        return quote(self.rel(target)) if target in self.held else None

    def run(self, run_id: str) -> str | None:
        return self.run_report(run_id)

    def campaign(self, campaign_id: str) -> str | None:
        return self._held("index.html")

    def run_report(self, run_id: str) -> str | None:
        folder = self.pages.get(run_id)
        return self._held(f"{folder}/index.html") if folder else self.online.run(run_id)

    @staticmethod
    def log_path(folder: str, step: str) -> str:
        return f"{folder}/logs/{safe_name(step)}.html"

    @staticmethod
    def exploit_path(folder: str, step: str) -> str:
        return f"{folder}/exploit/{safe_name(step)}.html"

    @staticmethod
    def preview_path(folder: str, file: str) -> str:
        return f"{folder}/inputs/{file}.html"

    def log(self, run_id: str, step: str) -> str | None:
        folder = self.pages.get(run_id)
        return (folder and self._held(self.log_path(folder, step))) or self.online.log(run_id, step)

    def exploit(self, run_id: str, step: str, path: str = "") -> str | None:
        folder = self.pages.get(run_id)
        return (folder and self._held(self.exploit_path(folder, step))) or self.online.exploit(run_id, step, path)

    def results(self, run_id: str) -> str | None:
        folder = self.pages.get(run_id)
        return (folder and self._held(f"{folder}/results.zip")) or self.online.results(run_id)

    def coverage(self, campaign_id: str | None = None, run_id: str | None = None) -> str | None:
        if campaign_id:
            return self._held("coverage.html") or self.online.coverage(campaign_id)
        folder = self.pages.get(run_id or "")
        return (folder and self._held(f"{folder}/coverage.html")) or self.online.coverage(run_id=run_id)

    def run_file(self, run_id: str, file: str, line: int = 0) -> str | None:
        return self.input_preview(run_id, file) or self.online.run_file(run_id, file, line)

    def input_preview(self, run_id: str, file: str) -> str | None:
        folder = self.pages.get(run_id)
        return folder and self._held(self.preview_path(folder, file))


def safe_name(text: str) -> str:
    """A name usable as a file name everywhere (a step key ``main:speed[slot]`` -> ``main_speed_slot_``)."""
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in str(text)) or "_"
