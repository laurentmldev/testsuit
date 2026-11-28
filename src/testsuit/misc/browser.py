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

"""Open a url in the user's browser, quietly.

On Linux, ``webbrowser`` runs ``xdg-open``, which runs ``gio open``: in a conda environment the ``gio`` found
first is the environment's own (from its glib), which has no handler for http and prints
``gio: <url>: Operation not supported`` while no browser opens (and ``webbrowser`` reports a success, as it
only sees ``xdg-open`` start). So on Linux the openers run here instead: with the environment's folders taken
out of the PATH and of the variables of glib (the desktop's own ``gio`` and settings then answer), what they
print captured rather than shown, and the next one tried when one fails. Elsewhere, ``webbrowser`` (then, on
Windows, the shell itself).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

# variables of path lists where a conda environment puts its own folders (its glib, its schemas, its mime data)
PATH_VARIABLES = ("PATH", "LD_LIBRARY_PATH", "XDG_DATA_DIRS", "GIO_MODULE_DIR", "GIO_EXTRA_MODULES",
                  "GSETTINGS_SCHEMA_DIR")
# after the desktop's openers, the browsers webbrowser would try too
BROWSERS = ("x-www-browser", "sensible-browser", "firefox", "google-chrome", "chromium", "chromium-browser",
            "microsoft-edge", "brave-browser", "epiphany", "konqueror")
WAIT = 4.0   # seconds an opener has to hand the url over; one still running then is a browser started


def uses_desktop_openers() -> bool:
    """True where the openers of the desktop are run here (Linux and the other Unixes but macOS)."""
    return os.name == "posix" and sys.platform != "darwin"


def conda_prefixes() -> list[str]:
    """The folders of the conda environment(s) this Python runs in (none for a system or venv Python)."""
    found = []
    for prefix in {sys.prefix, sys.base_prefix, os.environ.get("CONDA_PREFIX") or ""}:
        if prefix and (Path(prefix) / "conda-meta").is_dir():
            found.append(str(Path(prefix).resolve()))
    return found


def desktop_env(environ: dict | None = None, prefixes: list[str] | None = None) -> dict:
    """The environment for the openers: the entries inside a conda environment taken out of PATH_VARIABLES
    (a variable left empty is removed)."""
    env = dict(os.environ if environ is None else environ)
    prefixes = conda_prefixes() if prefixes is None else prefixes
    if not prefixes:
        return env

    def inside(entry: str) -> bool:
        try:
            resolved = str(Path(entry).resolve())
        except (OSError, RuntimeError):
            resolved = entry
        return any(resolved == p or resolved.startswith(p.rstrip(os.sep) + os.sep) for p in prefixes)

    for name in PATH_VARIABLES:
        if name not in env:
            continue
        kept = [e for e in env[name].split(os.pathsep) if e and not inside(e)]
        if kept:
            env[name] = os.pathsep.join(kept)
        else:
            del env[name]
    return env


def opener_commands(url: str, env: dict) -> list[list[str]]:
    """The commands to try, in order: $BROWSER, xdg-open, gio open, then BROWSERS; those found only."""
    commands: list[list[str]] = []
    for entry in (env.get("BROWSER") or "").split(os.pathsep):
        if entry.strip():
            words = shlex.split(entry)
            commands.append([w.replace("%s", url) for w in words] if "%s" in entry else words + [url])
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        commands += [["xdg-open", url], ["gio", "open", url]] + [[b, url] for b in BROWSERS]
    path = env.get("PATH", "")
    found = []
    for command in commands:
        exe = shutil.which(command[0], path=path)
        if exe:
            found.append([exe] + command[1:])
    return found


def run_opener(command: list[str], env: dict, wait: float = WAIT) -> str | None:
    """Run an opener, what it prints captured: None when it opened the url, else why not."""
    with tempfile.TemporaryFile() as out:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env,
                                       start_new_session=True)
        except OSError as e:
            return f"{command[0]}: {e}"
        try:
            code = process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            return None   # still running: a browser it started, or started itself
        if code == 0:
            return None
        out.seek(0)
        said = out.read().decode(errors="replace").strip()
        return f"{Path(command[0]).name} exited with {code}" + (f": {said}" if said else "")


def open_url(url: str) -> tuple[bool, list[str]]:
    """Open the url in the user's browser; never raises. Returns whether it opened, and what each way tried
    said (for a log)."""
    notes: list[str] = []
    if uses_desktop_openers():
        env = desktop_env()
        commands = opener_commands(url, env)
        if not commands:
            notes.append("no browser to run here (no display, no $BROWSER, or no opener installed)")
        for command in commands:
            failed = run_opener(command, env)
            if failed is None:
                notes.append(f"opened with {' '.join(command)}")
                return True, notes
            notes.append(failed)
        return False, notes
    try:
        if webbrowser.open(url):
            notes.append("opened with webbrowser")
            return True, notes
        notes.append("webbrowser could not open it")
    except Exception as e:  # any failure: the next way, and the notes say why
        notes.append(f"webbrowser failed: {e!r}")
    if hasattr(os, "startfile"):
        try:
            os.startfile(url)
            notes.append("opened with os.startfile")
            return True, notes
        except OSError as e:
            notes.append(f"os.startfile failed: {e!r}")
    return False, notes
