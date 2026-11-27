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

"""HTTP requests (README section 7.4): the ``http`` command, step, status probe and ready check.

One request description everywhere::

    http:
      url: "{{ component.vars.api }}/jobs"      # the only required field (a plain string is the URL)
      method: POST                              # default GET
      headers: {Authorization: "Bearer {{ component.vars.token }}"}
      params: {verbose: 1}                      # query string
      json: {mode: run}                         # or body: "raw text", or body_file: path (only one of them)
      timeout: 10s                              # of the request (default 30 s)
      verify: true                              # TLS certificate check (false for a self-signed lab server)
      extract: .jobs[0].state                   # jq expression: what the expectations and states see
      expect: {status: [200, 201], regex: "ok", json: {state: started}}   # default: a status below 400
      save: start_response.json                 # commands and steps: the answer, kept with the results

The request is sent from the sequencer host, or with ``via`` from another component (with ``curl`` there),
for an API only reachable from inside a stage.

``body_file`` sends a file's bytes as they are (like ``curl --data-binary @file``), read where the request is
sent from: on the sequencer host (a relative path is in the run folder, and refused outside a run), or with
``via`` on that component (curl reads it there).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Callable

from testsuit.sequencer.util import SequencerError, parse_duration

HTTP_KEYS = ("url", "method", "headers", "params", "json", "body", "body_file", "timeout", "verify", "extract", "expect", "save")
BODY_KEYS = ("json", "body", "body_file")
EXPECT_KEYS = ("status", "regex", "json")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
SECRET = re.compile(r"auth|token|key|secret|password|cookie", re.IGNORECASE)
STATUS_MARK = "\n__tsq_http_status__ "
BODY_MARK = "\n__tsq_http_body__\n"          # through via with a body_file: the answer, after curl's progress meter
SIZE_MARK = "__tsq_upload_size__ "
Progress = Callable[[int, int], None]           # bytes sent, bytes to send
MAX_BODY = 4 << 20


@dataclass
class Answer:
    status: int | None          # None: no answer (connection refused, timeout...)
    body: str = ""              # the answer, or the part ``extract`` takes out of it
    error: str = ""
    raw: str | None = None      # the whole answer, when ``extract`` took a part of it
    extract_error: str = ""     # why ``extract`` could not take its part


_PATH_TOKEN = re.compile(r"""\.(?P<name>[A-Za-z_][A-Za-z0-9_-]*)|\.?"(?P<quoted>(?:[^"\\]|\\.)*)"|\.?\[\s*(?:(?P<index>-?\d+)|"(?P<key>(?:[^"\\]|\\.)*)"|(?P<all>))\s*\]""")


def parse_path(path: str) -> list[tuple[str, Any]]:
    """A jq-style path in steps: ``.a.b``, ``."a b"``, ``.["a"]``, ``.items[0]``, ``.items[-1]``, ``.items[]``
    (every element), and ``.`` (the whole answer). Raises ValueError on anything else."""
    text = path.strip()
    if text in ("", "."):
        return []
    if not text.startswith("."):
        raise ValueError("starts with '.'")
    steps, pos = [], 0
    while pos < len(text):
        m = _PATH_TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"unexpected '{text[pos:]}'")
        if m["name"] is not None:
            steps.append(("key", m["name"]))
        elif m["quoted"] is not None:
            steps.append(("key", json.loads(f'"{m["quoted"]}"')))
        elif m["index"] is not None:
            steps.append(("index", int(m["index"])))
        elif m["key"] is not None:
            steps.append(("key", json.loads(f'"{m["key"]}"')))
        else:
            steps.append(("all", None))
        pos = m.end()
    return steps


def _path_extract(data: Any, steps: list[tuple[str, Any]]) -> Any:
    """The part of a JSON value a simple path gives, as jq does: a missing key or index gives null; ``[]``
    gives the list of what follows for each element."""
    values, many = [data], False
    for kind, arg in steps:
        nxt = []
        for v in values:
            if kind == "key":
                if v is not None and not isinstance(v, dict):
                    raise ValueError(f"cannot take '{arg}' of a {type(v).__name__}")
                nxt.append(None if v is None else v.get(arg))
            elif kind == "index":
                if v is not None and not isinstance(v, list):
                    raise ValueError(f"cannot index a {type(v).__name__}")
                nxt.append(v[arg] if v is not None and -len(v) <= arg < len(v) else None)
            else:
                if isinstance(v, dict):
                    nxt.extend(v.values())
                elif isinstance(v, list):
                    nxt.extend(v)
                else:
                    raise ValueError(f"cannot iterate over a {type(v).__name__}")
                many = True
        values = nxt
    return values if many else values[0]


def _jq_program(expr: str):
    """The jq expression compiled; ValueError with jq's message when it is not one."""
    import jq                   # the sequencer extra: pip install testsuit[sequencer]
    try:
        return jq.compile(expr)
    except ValueError as e:
        raise ValueError(str(e).splitlines()[0].rstrip(":")) from None


def one_line(expr: Any) -> str:
    """An expression written over several lines, on one line (for the logs and the errors)."""
    return " ".join(str(expr).split())


def extract(data: Any, expr: str) -> Any:
    """The part of a JSON value an ``extract`` gives. A simple path (``.a.b``, ``."a b"``, ``.items[0]``,
    ``.items[-1]``, ``.items[]``, ``.``) keeps its meaning: a ``[]`` gives a list. Any other jq expression
    (``.jobs | map(select(.state == "run")) | length``, several lines allowed) is run by jq: one value gives
    that value, none gives null, several give the list of them. Raises ValueError when it does not apply."""
    try:
        steps = parse_path(expr)
    except ValueError:
        steps = None
    if steps is not None:
        return _path_extract(data, steps)
    try:
        values = _jq_program(expr).input_value(data).all()
    except ValueError as e:
        raise ValueError(str(e).splitlines()[0]) from None
    return None if not values else values[0] if len(values) == 1 else values


def apply_extract(spec: dict, answer: Answer) -> Answer:
    """The answer as the expectations, states and logs see it: the ``extract`` part of the JSON answer (a string
    as is, like ``jq -r``, anything else as JSON); the whole answer stays in ``raw``."""
    path = spec.get("extract")
    if path is None or str(path).strip() in ("", ".") or answer.status is None:
        return answer
    try:
        value = extract(json.loads(answer.body), str(path))
    except ValueError as e:      # not JSON (JSONDecodeError is a ValueError), or a path that does not apply
        reason = "the answer is not JSON" if isinstance(e, json.JSONDecodeError) else str(e)
        return Answer(answer.status, "", answer.error, answer.body, f"cannot extract '{one_line(path)}': {reason}")
    text = value if isinstance(value, str) else json.dumps(value)
    return Answer(answer.status, text, answer.error, answer.body)


def normalize(spec: Any, where: str) -> dict:
    """The request as a mapping, checked (a plain string is the URL)."""
    if isinstance(spec, str):
        spec = {"url": spec}
    if not isinstance(spec, dict):
        raise SequencerError(f"{where}: 'http' takes a URL or a mapping with 'url'")
    moved = [k for k in ("status", "regex") if k in spec]
    if moved:
        raise SequencerError(f"{where}: 'http' {', '.join(moved)} is now 'expect: {{{', '.join(moved)}}}'")
    unknown = sorted(set(spec) - set(HTTP_KEYS))
    if unknown:
        raise SequencerError(f"{where}: unknown 'http' key(s) {', '.join(unknown)} (known: {', '.join(HTTP_KEYS)})")
    if not spec.get("url"):
        raise SequencerError(f"{where}: 'http' needs a 'url'")
    bodies = [k for k in BODY_KEYS if k in spec]
    if len(bodies) > 1:
        raise SequencerError(f"{where}: 'http' takes one of 'json', 'body' or 'body_file', not {' and '.join(bodies)}")
    if "body_file" in spec:
        path = spec["body_file"]
        if isinstance(path, (PurePath, int, float)) and not isinstance(path, bool):
            spec = {**spec, "body_file": str(path)}             # a key giving a path or a number
        elif not isinstance(path, str) or not path.strip():
            raise SequencerError(f"{where}: 'http.body_file' takes the path of the file to send, "
                                 f"not {_shown(path)}")
    method = str(spec.get("method") or "GET").upper()
    if method not in METHODS and "{{" not in method:
        raise SequencerError(f"{where}: 'http' method '{method}' is not one of {', '.join(METHODS)}")
    expect = spec.get("expect")
    if expect is not None:
        if not isinstance(expect, dict):
            raise SequencerError(f"{where}: 'http.expect' takes a mapping ({', '.join(EXPECT_KEYS)})")
        unknown = sorted(set(expect) - set(EXPECT_KEYS))
        if unknown:
            raise SequencerError(f"{where}: unknown 'http.expect' key(s) {', '.join(unknown)} "
                                 f"(known: {', '.join(EXPECT_KEYS)})")
    if spec.get("extract") is not None and "{{" not in str(spec["extract"]):
        expr = str(spec["extract"])
        try:
            parse_path(expr)
        except ValueError:
            try:
                _jq_program(expr)
            except ValueError as e:
                raise SequencerError(f"{where}: 'http.extract' '{one_line(expr)}' is not a jq expression "
                                     f"like .items[0].state ({e})") from None
    for key in ("headers", "params"):
        if spec.get(key) is not None and not isinstance(spec[key], dict):
            raise SequencerError(f"{where}: 'http.{key}' takes a mapping")
    return dict(spec)


def _shown(value: Any) -> str:
    """What a field got instead of a string, said so it can be fixed."""
    from jinja2 import Undefined
    if isinstance(value, Undefined):
        return f"an undefined key ({getattr(value, '_undefined_name', None) or '?'} is not defined)"
    if value is None or (isinstance(value, str) and not value.strip()):
        return "an empty value (a key that gives nothing?)"
    if isinstance(value, dict):
        return (f"a mapping {json.dumps(value, default=str)} (in YAML, quote a value that starts with a key: "
                "body_file: \"{{ ... }}\")")
    return f"a {type(value).__name__} {json.dumps(value, default=str)}"


def _url(spec: dict) -> str:
    url = str(spec["url"])
    params = spec.get("params") or {}
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({k: str(v) for k, v in params.items()})
    return url


def _headers(spec: dict) -> dict:
    headers = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
    kind = "application/json" if "json" in spec else "application/octet-stream" if "body_file" in spec else None
    if kind and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = kind
    return headers


def body_path(spec: dict, base: Path | None) -> Path:
    """The file ``body_file`` names, on the sequencer host: a relative path is in ``base`` (the run folder)."""
    path = Path(str(spec["body_file"]).strip()).expanduser()
    if path.is_absolute():
        return path
    if base is None:
        raise ValueError("a relative path is only read in a run (from the run folder): give an absolute path")
    return Path(base) / path


def _payload(spec: dict, base: Path | None = None) -> bytes | None:
    """The body to send from this host (reads ``body_file``: OSError or ValueError when it cannot)."""
    if "json" in spec:
        return json.dumps(spec["json"]).encode()
    if "body" in spec:
        body = spec["body"]
        return (body if isinstance(body, str) else json.dumps(body)).encode()
    if "body_file" in spec:
        return body_path(spec, base).read_bytes()
    return None


def describe(spec: dict) -> str:
    """The request in one line, header values that look secret hidden."""
    shown = " ".join(f"-H '{k}: {'***' if SECRET.search(k) else v}'" for k, v in _headers(spec).items())
    if "body_file" in spec:
        shown += f" --data-binary @{spec['body_file']}"
    return f"{str(spec.get('method') or 'GET').upper()} {_url(spec)}" + (f" {shown.strip()}" if shown.strip() else "")


def timeout_of(spec: dict, default: float = 30.0) -> float:
    return parse_duration(spec["timeout"]) if spec.get("timeout") is not None else default


class _FileBody:
    """A body file read as it is sent (not held in memory), its progress told at most every quarter second."""

    def __init__(self, path: Path, progress: Progress | None):
        self.file = open(path, "rb")
        self.size = os.fstat(self.file.fileno()).st_size
        self.done, self.progress, self.told = 0, progress, 0.0

    def read(self, n: int = -1) -> bytes:
        chunk = self.file.read(1 << 16 if n is None or n < 0 else n)
        self.done += len(chunk)
        if self.progress is not None:
            now = time.monotonic()
            if now - self.told >= 0.25 or self.done >= self.size:
                self.told = now
                self.progress(self.done, self.size)
        return chunk

    def close(self) -> None:
        self.file.close()


def send(spec: dict, timeout: float | None = None, base: Path | None = None,
         progress: Progress | None = None) -> Answer:
    """Send the request from this host (blocking: run it in a thread); ``base``: the run folder, where a
    relative ``body_file`` is; ``progress`` is told how much of the body file is sent (from this thread)."""
    headers = _headers(spec)
    body = None
    try:
        if "body_file" in spec:
            body = data = _FileBody(body_path(spec, base), progress)
            headers["Content-Length"] = str(body.size)
            if progress is not None:
                progress(0, body.size)
        else:
            data = _payload(spec, base)
    except (OSError, ValueError) as e:
        reason = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
        return Answer(None, "", f"cannot read the body file '{spec.get('body_file')}': {reason}")
    request = urllib.request.Request(_url(spec), data=data, headers=headers,
                                     method=str(spec.get("method") or "GET").upper())
    context = None
    if spec.get("verify") is False:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=timeout or timeout_of(spec), context=context) as r:
            return Answer(r.status, r.read(MAX_BODY).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            text = e.read(MAX_BODY).decode("utf-8", "replace")
        except Exception:
            text = ""
        return Answer(e.code, text)
    except Exception as e:
        reason = getattr(e, "reason", None)
        return Answer(None, "", str(reason or e) or type(e).__name__)
    finally:
        if body is not None:
            body.close()


def curl_command(spec: dict, timeout: float | None = None) -> str:
    """The same request as a curl command, for another component (``via``): the answer then its status. With
    ``body_file`` (read there by curl), the file's size, curl's progress meter and the status, then the answer
    (kept in a temporary file meanwhile)."""
    headers = _headers(spec)
    upload = "body_file" in spec
    args = ["curl", "-S" if upload else "-sS", "-X", str(spec.get("method") or "GET").upper(),
            "--max-time", f"{timeout or timeout_of(spec):g}", "-w", STATUS_MARK.replace("\n", "\\n") + "%{http_code}"]
    if spec.get("verify") is False:
        args.append("-k")
    for k, v in headers.items():
        args += ["-H", f"{k}: {v}"]
    if upload:
        args += ["--data-binary", "@" + str(spec["body_file"]).strip()]
    else:
        data = _payload(spec)
        if data is not None:
            args += ["--data-binary", data.decode()]
    args.append(_url(spec))
    command = " ".join(shlex.quote(a) for a in args)
    if not upload:
        return command
    path = shlex.quote(str(spec["body_file"]).strip())       # curl alone would not say which file it cannot read
    reason = shlex.quote(f"cannot read the body file '{str(spec['body_file']).strip()}' on this component: ")
    return (f"if [ ! -e {path} ]; then echo {reason}'no such file'; exit 66; "
            f"elif [ ! -f {path} ] || [ ! -r {path} ]; then echo {reason}'not a readable file'; exit 66; fi; "
            f"t=$(mktemp) || exit 70; echo \"{SIZE_MARK}$(wc -c < {path} | tr -d ' ')\"; "
            f"{command} -o \"$t\" 2>&1; s=$?; printf '{BODY_MARK.replace(chr(10), chr(92) + 'n')}'; cat \"$t\"; "
            f"rm -f \"$t\"; exit $s")


_METER_ROW = re.compile(r"^\s*\d+\s+\S+\s+\d+\s+\S+\s+\d+\s+(?P<sent>[\d.]+[kMGTP]?)\s")
_UNITS = {"": 1, "k": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40, "P": 1 << 50}


def _meter_line(line: str) -> bool:
    return bool(_METER_ROW.match(line)) or "% Total" in line or "Dload  Upload" in line or line.startswith(SIZE_MARK)


class UploadMeter:
    """Reads the output of an upload through via as it comes: the file's size, then curl's progress meter
    (a row each second, after a carriage return); tells ``progress`` the bytes sent."""

    def __init__(self, progress: Progress):
        self.progress, self.pending, self.total = progress, "", 0

    def feed(self, data: bytes) -> None:
        *lines, self.pending = re.split(r"[\r\n]", self.pending + data.decode("utf-8", "replace"))
        for line in lines:
            if line.startswith(SIZE_MARK) and line[len(SIZE_MARK):].strip().isdigit():
                self.total = int(line[len(SIZE_MARK):].strip())
                self.progress(0, self.total)
            elif self.total and (m := _METER_ROW.match(line)):
                sent = m["sent"]
                unit = sent[-1] if sent[-1] in _UNITS else ""
                self.progress(min(int(float(sent[:-1] if unit else sent) * _UNITS[unit]), self.total), self.total)


def parse_curl(output: str, exit_status: int | None, error: str = "") -> Answer:
    if BODY_MARK in output:                      # an upload: its meter and status, then the answer
        head, _, answer = output.partition(BODY_MARK)
        head = "\n".join(line for line in re.split(r"[\r\n]", head) if line.strip() and not _meter_line(line))
        before, _, status = head.rpartition(STATUS_MARK.strip("\n"))
        status = status.strip()
        if exit_status or not status.isdigit() or status == "000":
            return Answer(None, "", (error or before or head).strip()[-500:] or f"curl exit status {exit_status}")
        return Answer(int(status), answer)
    body, _, status = output.rpartition(STATUS_MARK.strip("\n"))
    status = status.strip()
    if exit_status or not status.isdigit() or status == "000":
        return Answer(None, "", (error or output).strip()[-500:] or f"curl exit status {exit_status}")
    return Answer(int(status), body[:-1] if body.endswith("\n") else body)


async def request(spec: dict, transport=None, timeout: float | None = None, base: Path | None = None,
                  progress: Progress | None = None) -> Answer:
    """Send the request: from the sequencer host, or through a component's transport (curl there); with
    ``extract``, the answer's body is that part of it (the whole answer in ``raw``). ``base``: the run folder,
    where a relative ``body_file`` is read on the sequencer host; ``progress`` is told how much of the body file
    is sent, in the event loop."""
    if transport is None:
        told = None
        if progress is not None and "body_file" in spec:
            loop = asyncio.get_running_loop()
            told = lambda done, total: loop.call_soon_threadsafe(progress, done, total)   # noqa: E731
        return apply_extract(spec, await asyncio.to_thread(send, spec, timeout, base, told))
    limit = timeout or timeout_of(spec)
    if progress is not None and "body_file" in spec and hasattr(transport, "run"):
        result = await transport.run(curl_command(spec, limit), timeout=limit + 5, log=UploadMeter(progress).feed)
    else:
        result = await transport.shell(curl_command(spec, limit), timeout=limit + 5)
    if not result.completed:
        return Answer(None, "", result.error or "no answer")
    return apply_extract(spec, parse_curl(result.output, result.exit_status, result.error or ""))


def _contains(answer: Any, wanted: Any) -> bool:
    if isinstance(wanted, dict):
        return isinstance(answer, dict) and all(k in answer and _contains(answer[k], v) for k, v in wanted.items())
    if isinstance(wanted, list):
        return isinstance(answer, list) and len(answer) == len(wanted) and all(map(_contains, answer, wanted))
    return answer == wanted or (isinstance(wanted, str) and not isinstance(answer, str) and str(answer) == wanted)


def judge(spec: dict, answer: Answer) -> tuple[bool, str]:
    """Whether the answer is the expected one, and why not."""
    if answer.status is None:
        return False, f"no answer: {answer.error}"
    expect = spec.get("expect") or {}
    statuses = expect.get("status")
    if statuses is None:
        if answer.status >= 400:
            return False, f"status {answer.status}"
    else:
        wanted = [int(s) for s in (statuses if isinstance(statuses, list) else [statuses])]
        if answer.status not in wanted:
            return False, f"status {answer.status}, expected {', '.join(map(str, wanted))}"
    if answer.extract_error:
        return False, answer.extract_error
    if expect.get("regex") and not re.search(str(expect["regex"]), answer.body, re.MULTILINE):
        return False, f"the answer does not match '{expect['regex']}'"
    if expect.get("json") is not None:
        try:
            parsed = json.loads(answer.body if answer.raw is None else answer.raw)
            if answer.raw is not None:                 # the extracted part, a string included
                parsed = extract(parsed, str(spec["extract"]))
        except ValueError:
            return False, "the answer is not JSON"
        if not _contains(parsed, expect["json"]):
            return False, f"the answer does not contain {json.dumps(expect['json'])}"
    return True, f"status {answer.status}"
