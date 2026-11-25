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

"""Issues bound to test results (sequencer SPEC section 11.3): the issues file, the GitLab, GitHub and Jira backends
(against a fake server, Redmine too), issues pre-filled from failures, known issues across runs, and requirements named by issues."""
import asyncio
import json
import re
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import yaml

from sequencer_t import Bench, exploit_bench, run_until_done
from testsuit.datatools.datapack import datapack_tools
from testsuit.issues import results as iss
from testsuit.issues.model import Issue, IssueError
from testsuit.issues.sources import IssueSources

TOKEN = "s3cr3t-token"


# ── a fake GitLab, GitHub, Jira and Redmine ─────────────────────────────────────────

class Tracker:
    def __init__(self):
        self.issues = {"gitlab": [], "github": [], "jira": [], "redmine": []}
        self.calls = []
        self.delay = 0.0


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
                since = str(q.get("updated_on") or "").lstrip(">=")
                time.sleep(tracker.delay)
                out = [i for i in issues[::-1] if (not text or text in i["subject"] + i["description"])
                       and (not since or str(i.get("updated_on") or "") >= since)
                       and q.get("status_id", "*") in ("*", "closed" if i["status"]["is_closed"] else "open")]
                page = out[int(q.get("offset", 0)):int(q.get("offset", 0)) + int(q.get("limit", 25))]
                return self._send(200, {"issues": page, "total_count": len(out), "offset": 0, "limit": 100})
            return self._send(404, {"message": f"no route {path}"})
    return H


@pytest.fixture
def tracker(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("ISSUES_TOKEN", TOKEN)
    t = Tracker()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(t))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    t.url = f"http://127.0.0.1:{server.server_port}"
    t.server = server
    yield t
    server.shutdown()


def _issues_cfg(url: str) -> dict:
    return {"default": "bench", "sources": {
        "bench": {"backend": "gitlab", "label": "Bench", "options": {"url": url, "project": "test/bench"},
                  "credentials": {"token": "env:ISSUES_TOKEN"}, "labels": ["tsq"]},
        "gh": {"backend": "github", "options": {"url": url, "repo": "acme/fw", "web": "https://github.com"}, "credentials": {"token": "env:ISSUES_TOKEN"}},
        "jira": {"backend": "jira", "options": {"url": url, "project": "BENCH", "cloud": False},
                 "credentials": {"email": "env:ISSUES_TOKEN", "token": "env:ISSUES_TOKEN"}}},
        "routes": [{"source": "gh", "step": "exploit:*", "check": "never*"}]}


# ── the issues file ───────────────────────────────────────────────────────

def test_issues_file(tracker):
    s = IssueSources(_issues_cfg(tracker.url))
    assert s.names == ["bench", "gh", "jira"] and s.default == "bench"
    assert s.parse("bench#42") == ("bench", "42") and s.parse("#7") == ("bench", "7") and s.parse("12") == ("bench", "12")
    assert s.parse("BENCH-3") == ("jira", "BENCH-3") and s.parse("jira#BENCH-3") == ("jira", "BENCH-3")
    assert s.parse(f"{tracker.url}/test/bench/-/issues/5") == ("bench", "5")
    assert s.parse("https://github.com/acme/fw/issues/9") == ("gh", "9")
    with pytest.raises(IssueError, match="unknown issue source 'nope'"):
        s.parse("nope#1")
    assert s.route({"step": "exploit:criteria", "check": "never_over"}) == "gh"
    assert s.route({"step": "main:run"}) == "bench"
    assert s.card("bench") == {"name": "bench", "backend": "gitlab", "project": "test/bench", "label": "Bench",
                               "url": tracker.url, "default": True, "group": False,
                               "capabilities": ["comment", "create", "group"]}
    with pytest.raises(IssueError, match="never be written in the file"):
        IssueSources({"sources": {"x": {"backend": "gitlab", "options": {"project": "a/b"}, "credentials": {"token": "abc"}}}})
    with pytest.raises(IssueError, match="unknown key"):
        IssueSources({"sources": {"x": {"backend": "gitlab"}}, "colour": 1})
    # one way to sign in: the credentials of one method of the backend
    with pytest.raises(IssueError, match=r"credentials oauth_token, token are not one way to sign in \(Access token"):
        IssueSources({"sources": {"x": {"backend": "gitlab", "options": {"project": "a/b"},
                                         "credentials": {"token": "env:A", "oauth_token": "env:B"}}}})
    with pytest.raises(IssueError, match="credentials token, user are not one way"):
        IssueSources({"sources": {"x": {"backend": "jira", "credentials": {"token": "env:A", "user": "env:B"}}}})
    IssueSources({"sources": {"x": {"backend": "jira", "credentials": {"email": "env:A", "token": "env:B"}}}})
    from testsuit.issues import describe_issue_backends
    auth = {b["name"]: [m["name"] for m in b["auth"]] for b in describe_issue_backends()}
    assert auth["gitlab"] == ["access_token", "oauth"] and auth["jira"] == ["cloud", "pat", "basic"]
    # the TLS check: false, written as a string too, or a CA bundle relative to the file's folder
    for verify, expected in ((False, False), ("false", False), ("no", False), (True, True), ("certs/ca.pem", "/base/certs/ca.pem")):
        b = IssueSources({"sources": {"x": {"backend": "gitlab", "options": {"project": "a/b", "verify": verify}}}},
                          Path("/base")).backend("x")
        assert b.api.verify == expected, verify
    with pytest.raises(IssueError, match="a pattern with"):
        IssueSources({"sources": {"x": {"backend": "gitlab"}}, "requirements": {"labels": "req"}})


# ── the backends ───────────────────────────────────────────────────────────

def test_backends(tracker):
    s = IssueSources(_issues_cfg(tracker.url))
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
    with pytest.raises(IssueError, match=r"HTTP 401.*token is wrong") as e:
        bad.search()
    assert "wrong-token-123" not in str(e.value)
    trace = []
    b = s.backend("bench")
    b.trace = trace
    b.search()
    assert trace and TOKEN not in json.dumps(trace)


def test_redmine_backend(tracker):
    s = IssueSources({"sources": {
        "rm": {"backend": "redmine", "options": {"url": tracker.url, "project": "bench", "tracker": "Bug"},
               "credentials": {"api_key": "env:ISSUES_TOKEN"}},
        "rm2": {"backend": "redmine", "options": {"url": tracker.url, "project": "bench", "markup": "markdown"},
                "credentials": {"user": "env:ISSUES_TOKEN", "password": "env:ISSUES_TOKEN"}}}})
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
    with pytest.raises(IssueError, match="no tracker 'Nope'"):
        IssueSources({"sources": {"x": {"backend": "redmine", "options": {"url": tracker.url, "project": "p", "tracker": "Nope"},
                                         "credentials": {"api_key": "env:ISSUES_TOKEN"}}}}).backend("x").create("x", "y")
    with pytest.raises(IssueError, match="option 'project' is missing"):
        IssueSources({"sources": {"x": {"backend": "redmine", "options": {"url": tracker.url}}}}).backend("x")
    with pytest.raises(IssueError, match=r"HTTP 401.*token is wrong") as e:
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
    """A run whose exploit check is not met, of a datapack generated with the issues file of its repository."""
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": None, "clean": False})
    bench = Bench(tmp_path, stages=1)
    (bench.dp / "issues.yml").write_text(yaml.safe_dump(_issues_cfg(tracker.url)))
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


def test_issues_on_the_sequencer(failed_run, tracker):
    bench, seq, run_id = failed_run
    view = seq.issues.run_view(run_id)
    assert [c["name"] for c in view["sources"]] == ["bench", "gh", "jira"] and view["default"] == "bench"
    labels = {t["label"]: t for t in view["targets"]}
    check = next(t for t in view["targets"] if t["target"].get("check") == "counted_enough")
    assert check["status"] == "failed" and check["issues"] == [] and check["route"] == "bench"
    assert set(labels) == {"run", "step exploit:criteria", "check exploit:criteria: criteria/count::counted_enough"}
    # a new issue, pre-filled, created and bound
    d = seq.issues.draft(run_id, CHECK, "http://tsq:8640/", "dana")
    assert d["source"] == "bench" and d["title"] == "[tsq] short: check counted_enough failed"
    assert "http://tsq:8640/#/runs/" in d["body"] and f"tsq:fingerprint={d['fingerprint']}" in d["body"]
    assert d["labels"][0] == "tsq" and d["duplicates"] == [] and d["known"] == []
    b = seq.issues.create(run_id, {"target": CHECK, "source": "bench", "title": d["title"], "body": d["body"],
                                    "labels": d["labels"]}, "dana", "http://tsq:8640/")
    assert b["issue"]["key"] == "bench#1" and b["created"] and b["by"] == "dana"
    with pytest.raises(Exception, match="already bound"):
        seq.issues.bind(run_id, {"target": CHECK, "issue": "bench#1"}, "dana")
    view = seq.issues.run_view(run_id)
    check = next(t for t in view["targets"] if t["target"].get("check") == "counted_enough")
    assert [x["issue"]["key"] for x in check["issues"]] == ["bench#1"]
    # an issue opened by hand, with the fingerprint: found before creating another (a duplicate)
    d2 = seq.issues.draft(run_id, CHECK, "", "dana")
    assert [k["issue"]["key"] for k in d2["known"]] == ["bench#1"]
    # an issue of another tool, bound by its URL; its state comes back from the tool
    gh = IssueSources(_issues_cfg(tracker.url)).backend("gh").create("bench too slow", "", [])
    b2 = seq.issues.bind(run_id, {"target": {"kind": "run"}, "issue": gh.url}, "lee")
    assert b2["issue"]["key"] == "gh#1"
    tracker.issues["github"][0]["state"] = "closed"
    view = seq.issues.run_view(run_id, refresh=True)
    run_t = next(t for t in view["targets"] if t["target"]["kind"] == "run")
    assert run_t["issues"][0]["issue"]["state"] == "closed"
    assert next(b for b in seq.issues.bindings() if b["id"] == b2["id"])["issue"]["state"] == "closed"   # as last seen
    # unbound with a reason
    with pytest.raises(Exception, match="say why"):
        seq.issues.unbind(b2["id"], "", "lee")
    seq.issues.unbind(b2["id"], "not the cause", "lee")
    assert [b["issue"]["key"] for b in seq.issues.bindings()] == ["bench#1"]
    # another run of the test definition that fails the same way: the issue is a known issue of its failure
    zip_path = seq.store.get_run(run_id)["zip"] if seq.store.get_run(run_id).get("zip") else None

    async def again():
        new = seq.submit(zip_path or sorted((bench.root / "zips").glob("short*.zip"))[0])
        await run_until_done(seq, [new["id"]], timeout=120)
        return new["id"]
    new_id = asyncio.run(again())
    check = next(t for t in seq.issues.run_view(new_id)["targets"] if t["target"].get("check") == "counted_enough")
    assert [x["issue"]["key"] for x in check["issues"]] == ["bench#1"]
    assert [(k["key"], k["target"]) for k in seq.issues.known_brief(seq.reviewed(seq.store.get_run(new_id)))] == [
        ("bench#1", "check exploit:criteria: criteria/count::counted_enough")]


def test_issue_cli(failed_run, tracker, capsys):
    """tsq issue, its client mapped straight onto the API endpoints."""
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
                return call(routes[("/runs/{run_id}/issues", "GET")](parts[1], "refresh" in url.query))

            def post(self, path, json=None, **kw):
                parts = path.strip("/").split("/")
                if parts[0] == "issues":
                    return call(routes[("/issues/bindings/{binding_id}/unbind", "POST")](int(parts[2]), json, request))
                if parts[-1] == "draft":
                    return call(routes[("/runs/{run_id}/issues/draft", "POST")](parts[1], json, request))
                return call(routes[("/runs/{run_id}/issues", "POST")](parts[1], json, request))

        def args(**kw):
            base = dict(run=run_id, link=None, create=False, unbind=None, on=None, source=None, project=None, title=None,
                        label=None, note=None, reason=None, yes=False, my_token=False, refresh=False, by="dana")
            return SimpleNamespace(**{**base, **kw})
        on = "check:criteria::counted_enough"
        assert await asyncio.to_thread(tsq.cmd_issue, args(create=True, on=on, yes=True, label=["bench-1"]), Http()) == 0
        assert "created and bound: bench#1 [open] [tsq] short: check counted_enough failed" in capsys.readouterr().out
        assert tracker.issues["gitlab"][0]["labels"] == ["tsq", "bench-1"]
        assert await asyncio.to_thread(tsq.cmd_issue, args(), Http()) == 0
        out = capsys.readouterr().out
        assert "known issue bench#1 [open]" in out and "sources: bench (gitlab test/bench) default" in out
        assert await asyncio.to_thread(tsq.cmd_issue, args(run=None, unbind=1, reason="wrong"), Http()) == 0
        assert "unbound bench#1" in capsys.readouterr().out
    asyncio.run(main())


def test_issues_in_the_editor(tmp_path, tracker):
    """The Editor's issues form: the backends it offers, and a source tested as being edited (not saved)."""
    from editor_t import Repos
    repos = Repos(tmp_path)
    session = repos.editor.open("workspace0")
    ctx = repos.editor.context(session)
    assert {"gitlab", "github", "jira", "redmine"} <= {b["name"] for b in ctx["issue_backends"]}
    redmine = next(b for b in ctx["issue_backends"] if b["name"] == "redmine")
    assert "api_key" in redmine["credentials"] and "tracker" in redmine["options"]
    data = {"sources": {"rm": {"backend": "redmine", "options": {"url": tracker.url, "project": "bench"},
                               "credentials": {"api_key": "env:ISSUES_TOKEN"}}}}
    tracker.issues["redmine"].append({"id": 4, "subject": "drift", "description": "", "status": {"is_closed": False}})
    out = repos.editor.issues_test(session, "rm", data)
    assert out.get("error") is None and out["count"] == 1 and out["issues"][0]["title"] == "drift"
    assert out["calls"] and TOKEN not in json.dumps(out)
    bad = repos.editor.issues_test(session, "rm", {"sources": {"rm": {"backend": "redmine", "options": {"url": tracker.url}}}})
    assert "option 'project' is missing" in bad["error"]
    with pytest.raises(Exception, match="no issues.yml"):
        repos.editor.issues_test(session, "rm")


# ── classes, variables, filters ───────────────────────────────────────────────

def _issue(**k) -> Issue:
    return Issue(**{"source": "x", "id": "1", **k})


def test_issue_filter():
    from datetime import datetime, timezone

    from testsuit.issues.filter import IssueFilter
    now = datetime(2026, 11, 25, tzinfo=timezone.utc)
    f = IssueFilter({"state": "open", "labels": ["Bench"], "any_labels": ["bug", "drift"], "not_labels": ["wontfix"],
                     "text": "speed|counter", "not_text": "duplicate of", "title": r"^\[tsq\]", "projects": ["fw/*"],
                     "authors": ["Alice"], "assignees": ["carol"], "created_after": "2026-01-01", "updated_after": "90d"})
    ok = dict(title="[tsq] speed over limit", state="open", labels=["bench", "Bug"], project="fw/core", author="alice",
              assignees=["carol"], created="2026-03-01T10:00:00Z", updated="2026-11-01T10:00:00Z")
    assert f and f.post and f.match(_issue(**ok), now)
    for change in ({"state": "closed"}, {"labels": ["Bug"]}, {"labels": ["bench"]}, {"labels": ["bench", "bug", "wontfix"]},
                   {"title": "[tsq] noise"}, {"body": "duplicate of #3"}, {"title": "speed over limit"}, {"project": "hw/core"},
                   {"author": "bob"}, {"assignees": []}, {"created": "2025-12-31"}, {"updated": "2026-05-01T00:00:00Z"},
                   {"updated": ""}):
        assert not f.match(_issue(**{**ok, **change}), now), change
    assert f.match(_issue(**{**ok, "title": "[TSQ] Counter wraps"}), now)        # case independent
    # state and labels alone: asked of the tool, nothing applied after
    g = IssueFilter({"state": "closed", "labels": ["x"]})
    assert g and not g.post and g.state_for("open") is False and g.state_for(None) == "closed" and g.state_for("closed") == "closed"
    assert not IssueFilter(None) and IssueFilter({"state": "all"}).state_for("open") == "open"
    for spec, message in (({"text": "("}, "not a regular expression"), ({"updated_after": "soon"}, "a time ago"),
                          ({"colour": 1}, "unknown key"), ({"state": "done"}, "open, closed or all"), ([], "a mapping")):
        with pytest.raises(IssueError, match=message):
            IssueFilter(spec)
    h = IssueFilter({"created_before": "2w", "updated_before": "2026-06-01 12:00"})
    assert h.match(_issue(created="2026-11-01", updated="2026-06-01T11:00:00Z"), now)
    assert not h.match(_issue(created="2026-11-20", updated="2026-06-01T11:00:00Z"), now)


def test_issue_classes():
    data = {"classes": {"corp": {"backend": "gitlab", "description": "the company GitLab",
                                 "options": {"url": "https://gitlab.corp", "project": "{{ team }}/bench"},
                                 "credentials": {"token": "env:GITLAB_TOKEN_{{ team | upper }}"}, "labels": ["tsq", "{{ team }}"],
                                 "filter": {"labels": ["{{ team }}"], "updated_after": "{{ since }}"},
                                 "vars": {"team": {"mandatory": True, "help": "its team"}, "since": {"value": "1y"}}}},
            "sources": {"fw": {"class": "corp", "label": "Firmware {{ team }}", "vars": {"team": "fw"}},
                        "hw": {"class": "corp", "vars": {"team": "hw", "since": "90d"}},
                        "own": {"backend": "github", "options": {"repo": "{{ org }}/x"}, "vars": {"org": "acme"},
                                "filter": {"not_labels": ["{{ org }}-noise"]}}}}
    s = IssueSources(data)
    assert s.specs["fw"]["options"] == {"url": "https://gitlab.corp", "project": "fw/bench"}
    assert s.specs["fw"]["credentials"] == {"token": "env:GITLAB_TOKEN_FW"} and s.specs["fw"]["labels"] == ["tsq", "fw"]
    assert s.label("fw") == "Firmware fw" and s.filters["fw"].spec == {"labels": ["fw"], "updated_after": "1y"}
    assert s.filters["hw"].spec["updated_after"] == "90d" and s.specs["own"]["options"]["repo"] == "acme/x"
    assert s.filters["own"].not_labels == {"acme-noise"} and s.vars("hw") == {"team": "hw", "since": "90d"}
    card = s.card("fw")
    assert card["class"] == "corp" and card["vars"] == {"team": "fw", "since": "1y"} and card["filter"]["labels"] == ["fw"]
    assert s.raw["fw"] == data["sources"]["fw"]
    bad = [({"x": {"class": "corp"}}, "mandatory variable 'team' has no value"),
           ({"x": {"class": "nope"}}, "unknown class 'nope'"),
           ({"x": {"class": "corp", "vars": {"team": "a", "colour": 1}}}, "colour: not a variable of class 'corp'"),
           ({"x": {"class": "corp", "vars": {"team": "a"}, "options": {}}}, "a source of a class sets only its label"),
           ({"x": {"backend": "gitlab", "options": {"project": "{{ nope }}"}}}, "nope"),
           ({"x": {"backend": "gitlab", "options": {"project": "a/b"}, "filter": {"text": "("}}}, "not a regular expression")]
    for sources, message in bad:
        with pytest.raises(IssueError, match=message):
            IssueSources({"classes": data["classes"], "sources": sources})
    with pytest.raises(IssueError, match="names its 'backend'"):
        IssueSources({"classes": {"c": {"vars": {}}}, "sources": {"x": {"class": "c"}}})


def test_filtered_search(tracker):
    issues = tracker.issues["gitlab"]
    for n, (title, labels, state, updated) in enumerate([
            ("speed over limit", ["bench"], "opened", "2026-11-20T10:00:00Z"),
            ("counter wraps", ["bench", "wontfix"], "opened", "2026-11-21T10:00:00Z"),
            ("old speed issue", ["bench"], "opened", "2024-01-01T10:00:00Z"),
            ("speed when closed", ["bench"], "closed", "2026-11-22T10:00:00Z"),
            ("docs typo", ["docs"], "opened", "2026-11-23T10:00:00Z")], start=1):
        issues.append({"iid": n, "title": title, "description": "", "state": state, "labels": labels,
                       "web_url": f"{tracker.url}/test/bench/-/issues/{n}", "assignees": [], "author": {"username": "bot"},
                       "references": {"full": f"test/bench#{n}"}, "updated_at": updated, "created_at": updated})
    cfg = _issues_cfg(tracker.url)
    cfg["sources"]["bench"]["filter"] = {"labels": ["bench"], "not_labels": ["wontfix"], "updated_after": "180d"}
    s = IssueSources(cfg)
    tracker.calls.clear()
    assert [i.id for i in s.backend("bench").search(state=None)] == ["4", "1"]
    assert [i.id for i in s.backend("bench").search(state="open")] == ["1"]
    assert [i.id for i in s.backend("bench").search("speed", state=None, limit=1)] == ["4"]
    asked = [c[2] for c in tracker.calls if c[1].endswith("/issues")]
    assert asked[0]["labels"] == "bench" and asked[1]["state"] == "opened"
    cfg["sources"]["bench"]["filter"] = {"state": "closed"}
    s = IssueSources(cfg)
    assert s.backend("bench").search(state="open") == [] and [i.id for i in s.backend("bench").search(state=None)] == ["4"]
    assert s.backend("bench").get(["5"])["5"].title == "docs typo"          # by its reference: whatever the filter


def test_issues_file_former_name(tmp_path):
    """tickets.yml (the former name) still reads, and the bindings of results/tickets move to results/issues."""
    from testsuit.issues.sources import find_issues_file, is_issues_file
    (tmp_path / ".git").mkdir()
    (tmp_path / "tickets.yml").write_text("sources: {}\n")
    assert find_issues_file(tmp_path) == tmp_path / "tickets.yml"
    assert is_issues_file("tickets.yml", {"sources": {}}) and is_issues_file("issues.yml", {"sources": {}})
    (tmp_path / "issues.yml").write_text("sources: {}\n")
    assert find_issues_file(tmp_path) == tmp_path / "issues.yml"


def _listed(editor, session, **k):
    """A page of the Editor's issues list once every list is read."""
    page = editor.issues_list(session, **k)
    k.pop("refresh", None)
    for _ in range(400):
        if not page["loading"]:
            return page
        time.sleep(0.02)
        page = editor.issues_list(session, **k)
    raise AssertionError(f"still loading: {page['loading']}")


def test_issues_list_pages(tmp_path, tracker):
    """The Editor's issues list: pages of a list read once, found by words, by requirements, counted."""
    from editor_t import Repos
    repos = Repos(tmp_path)
    session = repos.editor.open("workspace0")
    for n in range(1, 251):
        tracker.issues["redmine"].append({"id": n, "subject": f"issue {n}" + (" drift" if n % 10 == 0 else ""),
                                          "description": "", "status": {"is_closed": False},
                                          "updated_on": f"2026-11-{1 + n // 10:02d}T10:00:{n % 60:02d}Z"})
    (session.folder / "issues.yml").write_text(yaml.safe_dump({"sources": {"rm": {
        "backend": "redmine", "options": {"url": tracker.url, "project": "bench"}, "credentials": {"api_key": "env:ISSUES_TOKEN"}}}}))
    page = _listed(repos.editor, session, limit=100)
    # two sources of the same project, filtered differently: each its own list
    (session.folder / "issues.yml").write_text(yaml.safe_dump({"sources": {
        n: {"backend": "redmine", "options": {"url": tracker.url, "project": "bench"}, "credentials": {"api_key": "env:ISSUES_TOKEN"},
            **({"filter": {"title": "drift"}} if n == "drifts" else {})} for n in ("rm", "drifts")}}))
    assert _listed(repos.editor, session, source="drifts")["total"] == 25
    assert _listed(repos.editor, session, source="rm")["total"] == 250
    (session.folder / "issues.yml").write_text(yaml.safe_dump({"sources": {"rm": {
        "backend": "redmine", "options": {"url": tracker.url, "project": "bench"}, "credentials": {"api_key": "env:ISSUES_TOKEN"}}}}))
    page = _listed(repos.editor, session, limit=100)
    assert (page["total"], page["listed"], len(page["issues"]), page["offset"], page["errors"]) == (250, 250, 100, 0, {})
    reads = len(tracker.calls)
    last = repos.editor.issues_list(session, offset=200, limit=100)
    assert len(last["issues"]) == 50 and len(tracker.calls) == reads            # the list is read once
    keys = [i["key"] for i in page["issues"] + repos.editor.issues_list(session, offset=100)["issues"] + last["issues"]]
    assert len(set(keys)) == 250
    found = repos.editor.issues_list(session, find="DRIFT")
    assert found["total"] == 25 and all("drift" in i["title"] for i in found["issues"])
    assert repos.editor.issues_list(session, only="bound")["total"] == 0
    assert repos.editor.issues_list(session, limit=10000)["limit"] == 500
    _listed(repos.editor, session, refresh=True)
    assert len(tracker.calls) > reads


def test_issue_list_progress_and_refresh(tmp_path, tracker, monkeypatch):
    """A long list: its first pages shown while the rest is read, with the total the tool gives; then refreshed with
    only the issues updated since (changed, closed, new); and kept on disk for a restart."""
    from editor_t import Repos
    from testsuit.sequencer import issues as seq_issues
    repos = Repos(tmp_path)
    session = repos.editor.open("workspace0")
    rm = tracker.issues["redmine"]
    for n in range(1, 251):
        rm.append({"id": n, "subject": f"issue {n}", "description": "", "status": {"is_closed": False},
                   "updated_on": f"2026-11-{1 + n // 10:02d}T10:00:{n % 60:02d}Z"})
    (session.folder / "issues.yml").write_text(yaml.safe_dump({"sources": {"rm": {
        "backend": "redmine", "options": {"url": tracker.url, "project": "bench"}, "credentials": {"api_key": "env:ISSUES_TOKEN"}}}}))
    tracker.delay = 0.3
    page = repos.editor.issues_list(session)
    assert page["loading"]["rm"]["mode"] == "first" and page["listed"] == 0
    seen = None
    for _ in range(100):
        time.sleep(0.02)
        page = repos.editor.issues_list(session)
        if page["loading"] and page["listed"]:
            seen = page
            break
    assert seen and seen["loading"]["rm"]["total"] == 250 and seen["loading"]["rm"]["read"] == seen["listed"] < 250
    assert len(seen["issues"]) == min(100, seen["listed"])                   # the first ones, shown at once
    tracker.delay = 0
    page = _listed(repos.editor, session)
    assert page["total"] == 250 and page["read_at"]["rm"]
    # a refresh: only what changed since
    rm[4].update(subject="issue 5 renamed", updated_on="2099-01-01T00:00:00Z")
    rm[5].update(status={"is_closed": True}, updated_on="2099-01-01T00:00:00Z")
    rm.append({"id": 251, "subject": "a new one", "description": "", "status": {"is_closed": False},
               "updated_on": "2099-01-01T00:00:01Z"})
    monkeypatch.setattr(seq_issues, "TTL", 0.0)                             # read more than TTL ago
    tracker.calls.clear()
    repos.editor.issues_list(session)
    monkeypatch.setattr(seq_issues, "TTL", 300.0)
    page = _listed(repos.editor, session)
    titles = {i["key"]: i["title"] for i in repos.editor.issues_list(session, limit=500)["issues"]}
    assert page["total"] == 250 and titles["rm#5"] == "issue 5 renamed" and "rm#6" not in titles and "rm#251" in titles
    asked = [c[2] for c in tracker.calls if c[1] == "/issues.json"]
    assert asked and all(q.get("updated_on", "").startswith(">=") and q["status_id"] == "*" for q in asked)
    # a restart: the list as last read, at once, while it is read again
    fresh = seq_issues.Issues(repos.editor.seq)
    _, sources = repos.editor._issue_sources(session)
    tracker.delay = 0.3
    known = fresh.listing(sources, "rm", "open")
    assert known["complete"] and len(known["issues"]) == 250 and known["loading"]
    tracker.delay = 0


def test_issue_server_down(tmp_path, tracker):
    """An issue server down: the lists and issues as last read (said so), one wait and not one per call."""
    import time

    from editor_t import Repos
    from testsuit import netguard
    repos = Repos(tmp_path)
    session = repos.editor.open("workspace0")
    tracker.issues["redmine"].append({"id": 4, "subject": "drift", "description": "", "status": {"is_closed": False}})
    (session.folder / "issues.yml").write_text(yaml.safe_dump({"sources": {"rm": {
        "backend": "redmine", "options": {"url": tracker.url, "project": "bench"}, "credentials": {"api_key": "env:ISSUES_TOKEN"}}}}))
    assert _listed(repos.editor, session)["total"] == 1
    _, sources = repos.editor._issue_sources(session)
    assert repos.editor.seq.issues.issue(sources, "rm", "4").title == "drift"
    tracker.server.shutdown()
    tracker.server.server_close()
    page = _listed(repos.editor, session, refresh=True)
    assert page["total"] == 1 and "the list shown was read at" in page["errors"]["rm"]
    assert repos.editor.seq.issues.issue(sources, "rm", "4", refresh=True).title == "drift"
    start = time.monotonic()
    with pytest.raises(IssueError, match="cannot be reached since"):
        sources.backend("rm").search("x")
    assert time.monotonic() - start < 1 and tracker.url in netguard.down_hosts()
