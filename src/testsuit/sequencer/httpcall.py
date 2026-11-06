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
      json: {mode: run}                         # or body: "raw text" (not both)
      timeout: 10s                              # of the request (default 30 s)
      verify: true                              # TLS certificate check (false for a self-signed lab server)
      extract: .jobs[0].state                   # jq-style path: what the expectations and states see
      expect: {status: [200, 201], regex: "ok", json: {state: started}}   # default: a status below 400
      save: start_response.json                 # commands and steps: the answer, kept with the results

The request is sent from the sequencer host, or with ``via`` from another component (with ``curl`` there),
for an API only reachable from inside a stage.
"""
from __future__ import annotations

import asyncio
import json
import re
import shlex
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from testsuit.sequencer.util import SequencerError, parse_duration

HTTP_KEYS = ("url", "method", "headers", "params", "json", "body", "timeout", "verify", "extract", "expect", "save")
EXPECT_KEYS = ("status", "regex", "json")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
SECRET = re.compile(r"auth|token|key|secret|password|cookie", re.IGNORECASE)
STATUS_MARK = "\n__tsq_http_status__ "
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


def extract(data: Any, path: str) -> Any:
    """The part of a JSON value a jq-style path gives, as jq does: a missing key or index gives null; ``[]``
    gives the list of what follows for each element."""
    values, many = [data], False
    for kind, arg in parse_path(path):
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
        return Answer(answer.status, "", answer.error, answer.body, f"cannot extract '{path}': {reason}")
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
    if "json" in spec and "body" in spec:
        raise SequencerError(f"{where}: 'http' takes 'json' or 'body', not both")
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
        try:
            parse_path(str(spec["extract"]))
        except ValueError as e:
            raise SequencerError(f"{where}: 'http.extract' '{spec['extract']}' is not a path like "
                                 f".items[0].state ({e})") from None
    for key in ("headers", "params"):
        if spec.get(key) is not None and not isinstance(spec[key], dict):
            raise SequencerError(f"{where}: 'http.{key}' takes a mapping")
    return dict(spec)


def _url(spec: dict) -> str:
    url = str(spec["url"])
    params = spec.get("params") or {}
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({k: str(v) for k, v in params.items()})
    return url


def _payload(spec: dict) -> tuple[bytes | None, dict]:
    headers = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
    if "json" in spec:
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"
        return json.dumps(spec["json"]).encode(), headers
    if "body" in spec:
        body = spec["body"]
        return (body if isinstance(body, str) else json.dumps(body)).encode(), headers
    return None, headers


def describe(spec: dict) -> str:
    """The request in one line, header values that look secret hidden."""
    _, headers = _payload(spec)
    shown = " ".join(f"-H '{k}: {'***' if SECRET.search(k) else v}'" for k, v in headers.items())
    return f"{str(spec.get('method') or 'GET').upper()} {_url(spec)}" + (f" {shown}" if shown else "")


def timeout_of(spec: dict, default: float = 30.0) -> float:
    return parse_duration(spec["timeout"]) if spec.get("timeout") is not None else default


def send(spec: dict, timeout: float | None = None) -> Answer:
    """Send the request from this host (blocking: run it in a thread)."""
    data, headers = _payload(spec)
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
            body = e.read(MAX_BODY).decode("utf-8", "replace")
        except Exception:
            body = ""
        return Answer(e.code, body)
    except Exception as e:
        reason = getattr(e, "reason", None)
        return Answer(None, "", str(reason or e) or type(e).__name__)


def curl_command(spec: dict, timeout: float | None = None) -> str:
    """The same request as a curl command, for another component (``via``): the answer then its status."""
    data, headers = _payload(spec)
    args = ["curl", "-sS", "-X", str(spec.get("method") or "GET").upper(), "--max-time", f"{timeout or timeout_of(spec):g}",
            "-w", STATUS_MARK.replace("\n", "\\n") + "%{http_code}"]
    if spec.get("verify") is False:
        args.append("-k")
    for k, v in headers.items():
        args += ["-H", f"{k}: {v}"]
    if data is not None:
        args += ["--data-binary", data.decode()]
    args.append(_url(spec))
    return " ".join(shlex.quote(a) for a in args)


def parse_curl(output: str, exit_status: int | None, error: str = "") -> Answer:
    body, _, status = output.rpartition(STATUS_MARK.strip("\n"))
    status = status.strip()
    if exit_status or not status.isdigit() or status == "000":
        return Answer(None, "", (error or output).strip()[-500:] or f"curl exit status {exit_status}")
    return Answer(int(status), body[:-1] if body.endswith("\n") else body)


async def request(spec: dict, transport=None, timeout: float | None = None) -> Answer:
    """Send the request: from the sequencer host, or through a component's transport (curl there); with
    ``extract``, the answer's body is that part of it (the whole answer in ``raw``)."""
    if transport is None:
        return apply_extract(spec, await asyncio.to_thread(send, spec, timeout))
    limit = timeout or timeout_of(spec)
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
