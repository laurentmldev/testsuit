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

"""Issues bound to test results (sequencer SPEC section 11.3): the tickets file, the GitLab, GitHub and Jira backends
(against a fake server, Redmine too), issues pre-filled from failures, known issues across runs, and requirements named by issues."""
import asyncio
import json
import re
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import yaml

from sequencer_t import Bench, exploit_bench, run_until_done
from testsuit.datatools.datapack import datapack_tools
from testsuit.tickets import issues as iss
from testsuit.tickets.model import Issue, TicketError
from testsuit.tickets.sources import TicketSources

TOKEN = "s3cr3t-token"


# ── a fake GitLab, GitHub, Jira and Redmine ─────────────────────────────────────────

class Tracker:
    def __init__(self):
        self.issues = {"gitlab": [], "github": [], "jira": [], "redmine": []}
        self.calls = []


def _handler(tracker: Tracker):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, data, headers=None):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            h = self.headers
            return (h.get("PRIVATE-TOKEN") == TOKEN or h.get("Authorization") == f"Bearer {TOKEN}"
                    or h.get("X-Redmine-API-Key") == TOKEN or (h.get("Authorization") or "").startswith("Basic "))

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def _route(self, method):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            q.update({k: v for k, v in parse_qs(url.query).items() if k.endswith("[]")})
            path = unquote(url.path)
            tracker.calls.append((method, path, q))
            if not self._authorized():
                return self._send(401, {"message": "401 Unauthorized"})
            base = f"http://{self.headers['Host']}"
            # GitLab
            m = re.fullmatch(r"/api/v4/projects/(.+?)/issues(?:/(\d+))?(/notes)?", path)
            if m:
                issues = tracker.issues["gitlab"]
                if method == "POST" and m.group(3):
                    return self._send(201, {"id": 1})
                if method == "POST":
                    b = self._body()
                    item = {"iid": len(issues) + 1, "title": b["title"], "description": b.get("description", ""),
                            "state": "opened", "labels": [x for x in str(b.get("labels") or "").split(",") if x],
                            "web_url": f"{base}/{m.group(1)}/-/issues/{len(issues) + 1}", "assignees": [],
                            "author": {"username": "bot"}, "references": {"full": f"{m.group(1)}#{len(issues) + 1}"},
                            "updated_at": f"2026-11-25T10:0{len(issues)}:00Z"}
                    issues.append(item)
                    return self._send(201, item)
                if m.group(2):
                    found = [i for i in issues if str(i["iid"]) == m.group(2)]
                    return self._send(200, found[0]) if found else self._send(404, {"message": "404 Not found"})
                out = [i for i in issues if (q.get("state") in (None, i["state"]))
                       and (not q.get("search") or q["search"] in i["title"] + i["description"])]
                return self._send(200, out[::-1])
            # GitHub
            m = re.fullmatch(r"/repos/([^/]+/[^/]+)/issues(?:/(\d+))?(/comments)?", path)
            if m:
                issues = tracker.issues["github"]
                if method == "POST" and not m.group(3):
                    b = self._body()
                    n = len(issues) + 1
                    item = {"number": n, "title": b["title"], "body": b.get("body", ""), "state": "open",
                            "labels": [{"name": x} for x in b.get("labels") or []], "html_url": f"https://github.com/{m.group(1)}/issues/{n}",
                            "repository_url": f"{base}/repos/{m.group(1)}", "user": {"login": "bot"}, "assignees": []}
                    issues.append(item)
                    return self._send(201, item)
                if m.group(2):
                    found = [i for i in issues if str(i["number"]) == m.group(2)]
                    return self._send(200, found[0]) if found else self._send(404, {"message": "Not Found"})
                pr = {"number": 99, "title": "a pull request", "state": "open", "pull_request": {}}
                return self._send(200, [*issues[::-1], pr])
            # Jira
            if path.startswith("/rest/api/2/"):
                issues = tracker.issues["jira"]
                rest = path[len("/rest/api/2/"):]
                if rest == "search":
                    return self._send(200, {"issues": issues, "total": len(issues), "startAt": 0})
                if rest == "issue" and method == "POST":
                    b = self._body()["fields"]
                    key = f"{b['project']['key']}-{len(issues) + 1}"
                    issues.append({"key": key, "fields": {"summary": b["summary"], "description": b["description"],
                                                          "labels": b["labels"], "status": {"statusCategory": {"key": "new"}},
                                                          "project": {"key": b["project"]["key"]},
                                                          "issuelinks": [{"outwardIssue": {"key": "REQ-1"}}]}})
                    return self._send(201, {"key": key})
                m = re.fullmatch(r"issue/([A-Z]+-\d+)(/remotelink|/comment)?", rest)
                if m:
                    if m.group(2):
                        tracker.calls.append(("LINK", m.group(1), self._body()))
                        return self._send(201, {})
                    found = [i for i in issues if i["key"] == m.group(1)]
                    return self._send(200, found[0]) if found else self._send(404, {})
            # Redmine
            if path == "/trackers.json":
                return self._send(200, {"trackers": [{"id": 1, "name": "Feature"}, {"id": 3, "name": "Bug"}]})
            m = re.fullmatch(r"/issues(?:/(\d+))?\.json", path)
            if m:
                issues = tracker.issues["redmine"]
                if method == "POST":
                    b = self._body()["issue"]
                    n = len(issues) + 1
                    tracker_ = {1: "Feature", 3: "Bug"}[b.get("tracker_id", 1)]
                    item = {"id": n, "subject": b["subject"], "description": b["description"],
                            "project": {"id": 7, "name": b["project_id"]}, "tracker": {"id": 1, "name": tracker_},
                            "status": {"id": 1, "name": "New", "is_closed": False}, "author": {"name": "Bot"},
                            "updated_on": f"2026-11-25T10:0{n}:00Z", "relations": [{"issue_id": n, "issue_to_id": 40}]}
                    issues.append(item)
                    return self._send(201, {"issue": item})
                if m.group(1):
                    found = [i for i in issues if str(i["id"]) == m.group(1)]
                    if method == "PUT":
                        tracker.calls.append(("NOTE", m.group(1), self._body()))
                        return self._send(204, None) if found else self._send(404, {})
                    return self._send(200, {"issue": found[0]}) if found else self._send(404, {})
                text = q.get("v[any_searchable][]", [""])[0] if "v[any_searchable][]" in q else ""
                out = [i for i in issues[::-1] if not text or text in i["subject"] + i["description"]]
                page = out[int(q.get("offset", 0)):int(q.get("offset", 0)) + int(q.get("limit", 25))]
                return self._send(200, {"issues": page, "total_count": len(out), "offset": 0, "limit": 100})
            return self._send(404, {"message": f"no route {path}"})
    return H


@pytest.fixture
def tracker(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("TICKETS_TOKEN", TOKEN)
    t = Tracker()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(t))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    t.url = f"http://127.0.0.1:{server.server_port}"
    yield t
    server.shutdown()


def _tickets(url: str) -> dict:
    return {"default": "bench", "sources": {
        "bench": {"backend": "gitlab", "label": "Bench", "options": {"url": url, "project": "test/bench"},
                  "credentials": {"token": "env:TICKETS_TOKEN"}, "labels": ["tsq"]},
        "gh": {"backend": "github", "options": {"url": url, "repo": "acme/fw", "web": "https://github.com"}, "credentials": {"token": "env:TICKETS_TOKEN"}},
        "jira": {"backend": "jira", "options": {"url": url, "project": "BENCH", "cloud": False},
                 "credentials": {"email": "env:TICKETS_TOKEN", "token": "env:TICKETS_TOKEN"}}},
        "routes": [{"source": "gh", "step": "exploit:*", "check": "never*"}]}


# ── the tickets file ───────────────────────────────────────────────────────

def test_tickets_file(tracker):
    s = TicketSources(_tickets(tracker.url))
    assert s.names == ["bench", "gh", "jira"] and s.default == "bench"
    assert s.parse("bench#42") == ("bench", "42") and s.parse("#7") == ("bench", "7") and s.parse("12") == ("bench", "12")
    assert s.parse("BENCH-3") == ("jira", "BENCH-3") and s.parse("jira#BENCH-3") == ("jira", "BENCH-3")
    assert s.parse(f"{tracker.url}/test/bench/-/issues/5") == ("bench", "5")
    assert s.parse("https://github.com/acme/fw/issues/9") == ("gh", "9")
    with pytest.raises(TicketError, match="unknown tickets source 'nope'"):
        s.parse("nope#1")
    assert s.route({"step": "exploit:criteria", "check": "never_over"}) == "gh"
    assert s.route({"step": "main:run"}) == "bench"
    assert s.card("bench") == {"name": "bench", "backend": "gitlab", "project": "test/bench", "label": "Bench",
                               "url": tracker.url, "default": True, "group": False,
                               "capabilities": ["comment", "create", "group"]}
    with pytest.raises(TicketError, match="never be written in the file"):
        TicketSources({"sources": {"x": {"backend": "gitlab", "options": {"project": "a/b"}, "credentials": {"token": "abc"}}}})
    with pytest.raises(TicketError, match="unknown key"):
        TicketSources({"sources": {"x": {"backend": "gitlab"}}, "colour": 1})
    # one way to sign in: the credentials of one method of the backend
    with pytest.raises(TicketError, match=r"credentials oauth_token, token are not one way to sign in \(Access token"):
        TicketSources({"sources": {"x": {"backend": "gitlab", "options": {"project": "a/b"},
                                         "credentials": {"token": "env:A", "oauth_token": "env:B"}}}})
    with pytest.raises(TicketError, match="credentials token, user are not one way"):
        TicketSources({"sources": {"x": {"backend": "jira", "credentials": {"token": "env:A", "user": "env:B"}}}})
    TicketSources({"sources": {"x": {"backend": "jira", "credentials": {"email": "env:A", "token": "env:B"}}}})
    from testsuit.tickets import describe_ticket_backends
    auth = {b["name"]: [m["name"] for m in b["auth"]] for b in describe_ticket_backends()}
    assert auth["gitlab"] == ["access_token", "oauth"] and auth["jira"] == ["cloud", "pat", "basic"]
    # the TLS check: false, written as a string too, or a CA bundle relative to the file's folder
    for verify, expected in ((False, False), ("false", False), ("no", False), (True, True), ("certs/ca.pem", "/base/certs/ca.pem")):
        b = TicketSources({"sources": {"x": {"backend": "gitlab", "options": {"project": "a/b", "verify": verify}}}},
                          Path("/base")).backend("x")
        assert b.api.verify == expected, verify
    with pytest.raises(TicketError, match="a pattern with"):
        TicketSources({"sources": {"x": {"backend": "gitlab"}}, "requirements": {"labels": "req"}})


# ── the backends ───────────────────────────────────────────────────────────

def test_backends(tracker):
    s = TicketSources(_tickets(tracker.url))
    for name in ("bench", "gh", "jira"):
        b = s.backend(name)
        made = b.create("speed over limit", "REQ-1 is not met", ["tsq"])
        assert made.source == name and made.title == "speed over limit" and made.state == "open"
        found = b.get([made.id])[made.id]
        assert found.title == "speed over limit" and found.url
        listed = b.search(state="open")
        assert [i.id for i in listed] == [made.id], name           # a pull request is not an issue
        b.comment(made.id, "still failing")
        assert s.parse(found.url) == (name, made.id)
    assert s.backend("jira").get(["BENCH-1"])["BENCH-1"].links == ["REQ-1"]
    assert s.backend("bench").get(["404"])["404"] is None
    # a wrong token: the error says so, and never shows the token
    bad = s.backend("bench").with_token("wrong-token-123")
    with pytest.raises(TicketError, match=r"HTTP 401.*token is wrong") as e:
        bad.search()
    assert "wrong-token-123" not in str(e.value)
    trace = []
    b = s.backend("bench")
    b.trace = trace
    b.search()
    assert trace and TOKEN not in json.dumps(trace)


def test_redmine_backend(tracker):
    s = TicketSources({"sources": {
        "rm": {"backend": "redmine", "options": {"url": tracker.url, "project": "bench", "tracker": "Bug"},
               "credentials": {"api_key": "env:TICKETS_TOKEN"}},
        "rm2": {"backend": "redmine", "options": {"url": tracker.url, "project": "bench", "markup": "markdown"},
                "credentials": {"user": "env:TICKETS_TOKEN", "password": "env:TICKETS_TOKEN"}}}})
    assert s.card("rm")["capabilities"] == ["comment", "create"] and s.card("rm")["project"] == "bench"
    assert s.backend("rm").markup == "textile" and s.backend("rm2").markup == "markdown"
    b = s.backend("rm2")
    made = b.create("speed over limit", "REQ-1 is not met (tsq:fingerprint=abc)", ["tsq"])
    assert (made.source, made.id, made.state, made.labels) == ("rm2", "1", "open", ["Feature"])
    assert made.url == f"{tracker.url}/issues/1" and s.parse(made.url) == ("rm", "1") and s.parse("rm2#1") == ("rm2", "1")
    found = b.get(["1", "#1", "9"])
    assert found["1"].title == "speed over limit" and found["1"].links == ["40"] and found["#1"] and found["9"] is None
    assert [i.id for i in b.search("fingerprint=abc", state=None)] == ["1"] and b.search("nothing such") == []
    assert [i.id for i in b.search(labels=["feature"])] == ["1"] and b.search(labels=["Bug"]) == []
    b.comment("1", "still failing")
    assert ("NOTE", "1", {"issue": {"notes": "still failing"}}) in tracker.calls
    q = next(c[2] for c in tracker.calls if c[0] == "GET" and "v[any_searchable][]" in c[2])
    assert q["op[any_searchable]"] == "~" and q["project_id"] == "bench"
    # a tracker by name (the API key in its header)
    bug = s.backend("rm").create("x", "y")
    assert bug.labels == ["Bug"] and bug.id == "2"
    with pytest.raises(TicketError, match="no tracker 'Nope'"):
        TicketSources({"sources": {"x": {"backend": "redmine", "options": {"url": tracker.url, "project": "p", "tracker": "Nope"},
                                         "credentials": {"api_key": "env:TICKETS_TOKEN"}}}}).backend("x").create("x", "y")
    with pytest.raises(TicketError, match="option 'project' is missing"):
        TicketSources({"sources": {"x": {"backend": "redmine", "options": {"url": tracker.url}}}}).backend("x")
    with pytest.raises(TicketError, match=r"HTTP 401.*token is wrong") as e:
        s.backend("rm2").with_token("wrong-key-123").search()
    assert "wrong-key-123" not in str(e.value)


# ── issues about results, requirements named by issues ─────────────────────

def test_issues_and_requirements():
    target = {"kind": "check", "step": "exploit:criteria[1]", "criteria": "criteria/count", "scenario": "count",
              "check": "counted_enough"}
    fp = iss.fingerprint("short", target)
    assert fp == iss.fingerprint("short", {**target, "step": "exploit:criteria"}) != iss.fingerprint("long", target)
    run = {"id": "r1", "testdef": "short", "name": "short_dp", "verdict": "succeeded", "raw_verdict": "failed",
           "binding": {"stage_slots": {"stage": "bench_1"}}}
    d = iss.draft(run, target, "FAILED", {"run": "http://tsq/#/runs/r1", "exploit": "http://tsq/x"}, ["REQ-1"], "ERROR: boom")
    assert d["title"] == "[tsq] short: check counted_enough failed"
    assert f"<!-- tsq:fingerprint={fp} -->" in d["body"] and "[r1](http://tsq/#/runs/r1)" in d["body"]
    assert "(raw failed)" in d["body"] and "ERROR: boom" in d["body"] and "stage=bench_1" in d["body"]
    assert iss.mark_of(Issue("b", "1", body=d["body"])) == fp
    jira = iss.draft(run, target, "FAILED", {"run": "http://tsq/#/runs/r1"}, [], markup="jira")
    assert "[r1|http://tsq/#/runs/r1]" in jira["body"] and f"tsq:fingerprint={fp}" in jira["body"]
    assert iss.draft(run, target, "FAILED", {}, [], markup=False)["body"] == iss.draft(run, target, "FAILED", {}, [], markup="jira")["body"]
    textile = iss.draft(run, target, "FAILED", {"run": "http://tsq/#/runs/r1"}, ["REQ-1"], "ERROR: boom", markup="textile")
    assert '"r1":http://tsq/#/runs/r1' in textile["body"] and "<pre>" in textile["body"] and "* Requirements: REQ-1" in textile["body"]
    assert iss.mark_of(Issue("b", "1", body=textile["body"])) == fp
    assert iss.req_labels({"labels": "req:{id}", "add": True}, ["REQ-1", "jama:SYS 4"]) == ["req:REQ-1", "req:jama:SYS_4"]
    assert "ERROR: x" in iss.excerpt("ok\nERROR: x\nok\n")

    rules = {"labels": "req:{id}", "text": True, "jira_links": True}
    m = iss.RequirementMatcher(["REQ-1", "REQ-12", "jama:SYS-4"])
    issue = Issue("bench", "3", title="REQ-12 broken", body="also SYS-4, not REQ-123", labels=["req:REQ-1", "bug"])
    assert m.match(issue, rules) == {"REQ-1": "label req:REQ-1", "REQ-12": "named in it", "jama:SYS-4": "named in it"}
    assert m.match(issue, {**rules, "labels": False, "text": False}) == {}
    assert m.match(Issue("jira", "B-1", links=["REQ-1"]), rules) == {"REQ-1": "issue link"}

    from testsuit.reqs.coverage import Link, Test
    test = Test(key="t", testdef="short", links=[
        Link("REQ-1", "check", step="criteria", criteria="criteria/count", scenario="count", check="counted_enough"),
        Link("REQ-7", "check", step="criteria", criteria="criteria/count", scenario="count", check="other"),
        Link("REQ-9", "testdef")])
    bindings = [{"id": 1, "testdef": "short", "target": {**target, "step": "exploit:criteria"},
                 "issue": {"source": "bench", "id": "5", "key": "bench#5", "title": "slow", "state": "open"}},
                {"id": 2, "testdef": "short", "target": {"kind": "run"}, "removed": {"reason": "x"},
                 "issue": {"source": "bench", "id": "6", "key": "bench#6"}}]
    index = iss.issues_by_requirement([issue], {"bench": rules}, ["REQ-1", "REQ-7", "REQ-9", "REQ-12"], bindings, [test])
    assert sorted(index) == ["REQ-1", "REQ-12"]
    assert [(e["issue"]["key"], e["via"]) for e in index["REQ-1"]] == [
        ("bench#3", ["label req:REQ-1"]), ("bench#5", ["check counted_enough"])]
    assert iss.derived_requirements({"testdef": "short", "target": {"kind": "step", "step": "exploit:criteria"}},
                                    [test]) == {"REQ-1": "step exploit:criteria", "REQ-7": "step exploit:criteria"}


# ── on the sequencer ───────────────────────────────────────────────────────

CHECK = {"kind": "check", "step": "exploit:criteria", "criteria": "criteria/count", "scenario": "count",
         "check": "counted_enough"}


@pytest.fixture
def failed_run(tmp_path, monkeypatch, tracker):
    """A run whose exploit check is not met, of a datapack generated with the tickets file of its repository."""
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": None, "clean": False})
    bench = Bench(tmp_path, stages=1)
    (bench.dp / "tickets.yml").write_text(yaml.safe_dump(_tickets(tracker.url)))
    zip_path = exploit_bench(bench, "short", {"criteria": ["criteria/count"], "data": ["test_run:dut/output/*.csv"]},
                             min_count=100000)

    async def main():
        seq = bench.sequencer()
        run = seq.submit(zip_path)
        await run_until_done(seq, [run["id"]], timeout=120)
        return seq, run["id"]
    seq, run_id = asyncio.run(main())
    yield bench, seq, run_id
    asyncio.run(seq.close())


def test_tickets_on_the_sequencer(failed_run, tracker):
    bench, seq, run_id = failed_run
    view = seq.tickets.run_view(run_id)
    assert [c["name"] for c in view["sources"]] == ["bench", "gh", "jira"] and view["default"] == "bench"
    labels = {t["label"]: t for t in view["targets"]}
    check = next(t for t in view["targets"] if t["target"].get("check") == "counted_enough")
    assert check["status"] == "failed" and check["issues"] == [] and check["route"] == "bench"
    assert set(labels) == {"run", "step exploit:criteria", "check exploit:criteria: criteria/count::counted_enough"}
    # a new issue, pre-filled, created and bound
    d = seq.tickets.draft(run_id, CHECK, "http://tsq:8640/", "dana")
    assert d["source"] == "bench" and d["title"] == "[tsq] short: check counted_enough failed"
    assert "http://tsq:8640/#/runs/" in d["body"] and f"tsq:fingerprint={d['fingerprint']}" in d["body"]
    assert d["labels"][0] == "tsq" and d["duplicates"] == [] and d["known"] == []
    b = seq.tickets.create(run_id, {"target": CHECK, "source": "bench", "title": d["title"], "body": d["body"],
                                    "labels": d["labels"]}, "dana", "http://tsq:8640/")
    assert b["issue"]["key"] == "bench#1" and b["created"] and b["by"] == "dana"
    with pytest.raises(Exception, match="already bound"):
        seq.tickets.bind(run_id, {"target": CHECK, "issue": "bench#1"}, "dana")
    view = seq.tickets.run_view(run_id)
    check = next(t for t in view["targets"] if t["target"].get("check") == "counted_enough")
    assert [x["issue"]["key"] for x in check["issues"]] == ["bench#1"]
    # an issue opened by hand, with the fingerprint: found before creating another (a duplicate)
    d2 = seq.tickets.draft(run_id, CHECK, "", "dana")
    assert [k["issue"]["key"] for k in d2["known"]] == ["bench#1"]
    # an issue of another tool, bound by its URL; its state comes back from the tool
    gh = TicketSources(_tickets(tracker.url)).backend("gh").create("bench too slow", "", [])
    b2 = seq.tickets.bind(run_id, {"target": {"kind": "run"}, "issue": gh.url}, "lee")
    assert b2["issue"]["key"] == "gh#1"
    tracker.issues["github"][0]["state"] = "closed"
    view = seq.tickets.run_view(run_id, refresh=True)
    run_t = next(t for t in view["targets"] if t["target"]["kind"] == "run")
    assert run_t["issues"][0]["issue"]["state"] == "closed"
    assert next(b for b in seq.tickets.bindings() if b["id"] == b2["id"])["issue"]["state"] == "closed"   # as last seen
    # unbound with a reason
    with pytest.raises(Exception, match="say why"):
        seq.tickets.unbind(b2["id"], "", "lee")
    seq.tickets.unbind(b2["id"], "not the cause", "lee")
    assert [b["issue"]["key"] for b in seq.tickets.bindings()] == ["bench#1"]
    # another run of the test definition that fails the same way: the issue is a known issue of its failure
    zip_path = seq.store.get_run(run_id)["zip"] if seq.store.get_run(run_id).get("zip") else None

    async def again():
        new = seq.submit(zip_path or sorted((bench.root / "zips").glob("short*.zip"))[0])
        await run_until_done(seq, [new["id"]], timeout=120)
        return new["id"]
    new_id = asyncio.run(again())
    check = next(t for t in seq.tickets.run_view(new_id)["targets"] if t["target"].get("check") == "counted_enough")
    assert [x["issue"]["key"] for x in check["issues"]] == ["bench#1"]
    assert [(k["key"], k["target"]) for k in seq.tickets.known_brief(seq.reviewed(seq.store.get_run(new_id)))] == [
        ("bench#1", "check exploit:criteria: criteria/count::counted_enough")]


def test_ticket_cli(failed_run, tracker, capsys):
    """tsq ticket, its client mapped straight onto the API endpoints."""
    from testsuit.cli import tsq
    from testsuit.sequencer.api import create_app
    bench, seq, run_id = failed_run
    routes = {(r.path, m): r.endpoint for r in create_app(seq).routes if hasattr(r, "endpoint") for m in r.methods}
    request = SimpleNamespace(headers={}, base_url="http://tsq:8640/")

    async def main():
        loop = asyncio.get_running_loop()

        def call(coro):
            return asyncio.run_coroutine_threadsafe(coro, loop).result()

        class Http:
            def get(self, path, **kw):
                url = urlparse(path)
                parts = url.path.strip("/").split("/")
                if parts[2] == "review":
                    return call(routes[("/runs/{run_id}/review", "GET")](parts[1]))
                return call(routes[("/runs/{run_id}/tickets", "GET")](parts[1], "refresh" in url.query))

            def post(self, path, json=None, **kw):
                parts = path.strip("/").split("/")
                if parts[0] == "tickets":
                    return call(routes[("/tickets/bindings/{binding_id}/unbind", "POST")](int(parts[2]), json, request))
                if parts[-1] == "draft":
                    return call(routes[("/runs/{run_id}/tickets/draft", "POST")](parts[1], json, request))
                return call(routes[("/runs/{run_id}/tickets", "POST")](parts[1], json, request))

        def args(**kw):
            base = dict(run=run_id, link=None, create=False, unbind=None, on=None, source=None, project=None, title=None,
                        label=None, note=None, reason=None, yes=False, my_token=False, refresh=False, by="dana")
            return SimpleNamespace(**{**base, **kw})
        on = "check:criteria::counted_enough"
        assert await asyncio.to_thread(tsq.cmd_ticket, args(create=True, on=on, yes=True, label=["bench-1"]), Http()) == 0
        assert "created and bound: bench#1 [open] [tsq] short: check counted_enough failed" in capsys.readouterr().out
        assert tracker.issues["gitlab"][0]["labels"] == ["tsq", "bench-1"]
        assert await asyncio.to_thread(tsq.cmd_ticket, args(), Http()) == 0
        out = capsys.readouterr().out
        assert "known issue bench#1 [open]" in out and "sources: bench (gitlab test/bench) default" in out
        assert await asyncio.to_thread(tsq.cmd_ticket, args(run=None, unbind=1, reason="wrong"), Http()) == 0
        assert "unbound bench#1" in capsys.readouterr().out
    asyncio.run(main())


def test_tickets_in_the_editor(tmp_path, tracker):
    """The Editor's tickets form: the backends it offers, and a source tested as being edited (not saved)."""
    from editor_t import Repos
    repos = Repos(tmp_path)
    session = repos.editor.open("workspace0")
    ctx = repos.editor.context(session)
    assert {"gitlab", "github", "jira", "redmine"} <= {b["name"] for b in ctx["ticket_backends"]}
    redmine = next(b for b in ctx["ticket_backends"] if b["name"] == "redmine")
    assert "api_key" in redmine["credentials"] and "tracker" in redmine["options"]
    data = {"sources": {"rm": {"backend": "redmine", "options": {"url": tracker.url, "project": "bench"},
                               "credentials": {"api_key": "env:TICKETS_TOKEN"}}}}
    tracker.issues["redmine"].append({"id": 4, "subject": "drift", "description": "", "status": {"is_closed": False}})
    out = repos.editor.tickets_test(session, "rm", data)
    assert out.get("error") is None and out["count"] == 1 and out["issues"][0]["title"] == "drift"
    assert out["calls"] and TOKEN not in json.dumps(out)
    bad = repos.editor.tickets_test(session, "rm", {"sources": {"rm": {"backend": "redmine", "options": {"url": tracker.url}}}})
    assert "option 'project' is missing" in bad["error"]
    with pytest.raises(Exception, match="no tickets.yml"):
        repos.editor.tickets_test(session, "rm")
