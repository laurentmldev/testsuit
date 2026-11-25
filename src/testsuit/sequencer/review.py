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

"""Reviews of run results (SPEC section 11.1): who judged a result, how and why, after the run.

The results of a run (``run.yml``, the sealed results zip) never change. A **review** is a journal kept beside them,
``review.yml`` in the run's results folder (``storage.results/<run>/``), a campaign run's in
``storage.results/campaigns/<id>/``. Its entries are only ever added:

- ``review``: on a target (a check of an exploit step, a step, the run, or the campaign run), a status (``accepted``:
  counted as met; ``rejected``: counted as not met) with its reason, or a comment alone;
- ``revoke``: an earlier review entry no longer counts;
- ``sign_off``: the review is closed, its reports are final; ``reopen`` (with a reason) opens it again.

``apply_review`` gives the run as reviewed: each check, step and the verdict as the reviews make them, the raw ones
kept beside (``raw_status``, ``raw_state``, ``raw_verdict``). Checks make their exploit step, steps the verdict, as
when the run ran (SPEC section 9.4); a review of a step or of the run is over what its checks or steps give. The
campaign view, the reports and the requirements coverage read runs so reviewed.
"""
from __future__ import annotations

import copy
import html
import json
import os
from pathlib import Path
from typing import Any

import yaml

from testsuit.sequencer.util import SequencerError, now

REVIEW_FILE = "review.yml"
FORMAT = 1
ACCEPTED, REJECTED = "accepted", "rejected"
SUCCEEDED, FAILED, ERROR, SKIPPED = "succeeded", "failed", "error", "skipped"
KINDS = ("check", "step", "run", "campaign")
ACTIONS = ("review", "revoke", "sign_off", "reopen")
NONE, OPEN, SIGNED_OFF = "none", "open", "signed_off"
_RANK = {SUCCEEDED: 0, SKIPPED: 0, FAILED: 1, ERROR: 2}
_MET = ("SUCCESS", "ACCEPTED")
_NOT_MET = ("FAILED", "REJECTED")
ENDED = ("done", "cancelled")


class ReviewError(SequencerError):
    """A review entry refused."""


def worst(states) -> str:
    result = SUCCEEDED
    for s in states:
        if _RANK.get(s, 2) > _RANK[result]:
            result = s
    return result


# ── targets ────────────────────────────────────────────────────────────────

def step_key(record: dict) -> str:
    """A step record's key: ``<phase>:<step>``, ``[<slot>]`` for one slot of a step run on several."""
    return f"{record.get('phase')}:{record.get('id')}" + (f"[{record['slot']}]" if record.get("slot") else "")


def check_target(record: dict, check: dict) -> dict:
    return {"kind": "check", "step": step_key(record), "criteria": str(check.get("criteria") or ""),
            "scenario": str(check.get("scenario") or ""), "check": str(check.get("check") or "")}


def target_key(target: dict) -> str:
    kind = target.get("kind")
    if kind == "check":
        return "check|" + "|".join(str(target.get(k) or "") for k in ("step", "criteria", "scenario", "check"))
    if kind == "step":
        return f"step|{target.get('step')}"
    return str(kind)


def target_label(target: dict) -> str:
    """A target as people read it: ``check main:speed/criteria/scn::check``, ``step main:speed``, ``run``."""
    kind = target.get("kind")
    if kind == "check":
        folder = target.get("criteria") or ""
        if target.get("scenario") and not str(folder).rstrip("/").endswith(str(target["scenario"])):
            folder = f"{folder}/{target['scenario']}"
        return f"check {target.get('step')}: {folder}" + (f"::{target['check']}" if target.get("check") else "")
    if kind == "step":
        return f"step {target.get('step')}"
    return str(kind)


def normalize_target(target: Any, campaign: bool = False) -> dict:
    if not isinstance(target, dict):
        raise ReviewError("a review needs a target: {kind: check|step|run|campaign, ...}")
    kind = target.get("kind")
    if campaign:
        if kind != "campaign":
            raise ReviewError("the review of a campaign run is on the campaign run itself (kind: campaign); review "
                              "its checks, steps and runs on each run")
        return {"kind": "campaign"}
    if kind not in ("check", "step", "run"):
        raise ReviewError(f"unknown review target kind '{kind}' (check, step or run)")
    if kind == "run":
        return {"kind": "run"}
    if not target.get("step"):
        raise ReviewError(f"a {kind} target names its step ('<phase>:<step>')")
    if kind == "step":
        return {"kind": "step", "step": str(target["step"])}
    return {"kind": "check", "step": str(target["step"]), "criteria": str(target.get("criteria") or ""),
            "scenario": str(target.get("scenario") or ""), "check": str(target.get("check") or "")}


def find_record(run: dict, step: str) -> dict | None:
    return next((r for r in run.get("steps") or [] if step_key(r) == step), None)


def find_check(run: dict, target: dict) -> dict | None:
    record = find_record(run, target["step"])
    if record is None:
        return None
    for c in (record.get("exploit") or {}).get("checks") or []:
        if target_key(check_target(record, c)) == target_key(target):
            return c
    return None


def raw_status(run: dict, target: dict) -> str | None:
    """The result of a target as the run gave it (None: no such target in the run)."""
    kind = target["kind"]
    if kind == "run":
        if run.get("state") not in ENDED:
            return None
        return run.get("verdict") or ERROR
    if kind == "step":
        record = find_record(run, target["step"])
        return record.get("state") if record else None
    if kind == "check":
        check = find_check(run, target)
        return str(check.get("status") or ERROR).upper() if check else None
    return None


def _allowed(kind: str, raw: str, status: str) -> str | None:
    """Why a status cannot be given to a target of that raw result (None: it can)."""
    if kind == "check":
        met, not_met = raw in _MET, raw in _NOT_MET
        if status == ACCEPTED and not not_met:
            return ("an ERROR check cannot be accepted: its outcome is unknown (run it again, or reject it)"
                    if raw == "ERROR" else f"the check is {raw}: it is met already")
        if status == REJECTED and not (met or raw == "ERROR"):
            return f"the check is {raw}: it is not met already"
        return None
    if status == ACCEPTED and raw != FAILED:
        return ("an error cannot be accepted: its outcome is unknown (run it again, or reject it)" if raw == ERROR
                else f"it {raw}: only a failure can be accepted")
    if status == REJECTED and raw not in (SUCCEEDED, ERROR):
        return f"it {raw}: only a success or an error can be rejected"
    return None


# ── the journal ────────────────────────────────────────────────────────────

def empty(of: dict) -> dict:
    return {"format": FORMAT, **of, "entries": []}


def load(path: Path, of: dict | None = None) -> dict:
    """The review in a file (an empty one when there is none)."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty(of or {})
    except (OSError, yaml.YAMLError) as e:
        raise ReviewError(f"cannot read the review {path}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
        raise ReviewError(f"{path}: not a review (format {FORMAT}: entries)")
    data.setdefault("entries", [])
    return data


def save(path: Path, review: dict) -> None:
    """Written whole, atomically: a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text("# who reviewed the results, how and why (SPEC section 11.1): entries are only ever added\n"
                   + yaml.safe_dump(review, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


def state(review: dict | None) -> str:
    """``none`` (no entry), ``open`` or ``signed_off``."""
    entries = (review or {}).get("entries") or []
    marks = [e for e in entries if e.get("action") in ("sign_off", "reopen")]
    if marks and marks[-1]["action"] == "sign_off":
        return SIGNED_OFF
    return OPEN if entries else NONE


def signed_off(review: dict | None) -> dict | None:
    """The sign-off in force, if any."""
    entries = (review or {}).get("entries") or []
    marks = [e for e in entries if e.get("action") in ("sign_off", "reopen")]
    return marks[-1] if marks and marks[-1]["action"] == "sign_off" else None


def revoked(review: dict) -> set:
    return {e.get("revokes") for e in review.get("entries") or [] if e.get("action") == "revoke"}


def reviews(review: dict | None) -> list[dict]:
    """The review entries that count (not revoked), oldest first."""
    if not review:
        return []
    gone = revoked(review)
    return [e for e in review.get("entries") or [] if e.get("action") == "review" and e.get("id") not in gone]


def active(review: dict | None) -> dict[str, dict]:
    """Target key -> the status review in force on it (the last one not revoked)."""
    out: dict[str, dict] = {}
    for e in reviews(review):
        if e.get("status"):
            out[target_key(e["target"])] = e
    return out


def comments(review: dict | None) -> dict[str, list[dict]]:
    """Target key -> its review entries that count, statuses and comments (oldest first)."""
    out: dict[str, list[dict]] = {}
    for e in reviews(review):
        out.setdefault(target_key(e["target"]), []).append(e)
    return out


def brief(entry: dict) -> dict:
    return {k: entry[k] for k in ("id", "at", "by", "status", "was", "reason", "comment", "ref", "reapplied_from")
            if entry.get(k) not in (None, "")}


def add(review: dict, body: dict, by: str, run: dict | None = None, campaign: bool = False) -> dict:
    """Add an entry to a review (a run's, with its record; or a campaign run's). Returns the entry.

    body: ``{action: review, target, status?, reason?, comment?, ref?}``, ``{action: revoke, entry, reason}``,
    ``{action: sign_off, comment?}`` or ``{action: reopen, reason}``."""
    action = str(body.get("action") or "review")
    if action not in ACTIONS:
        raise ReviewError(f"unknown review action '{action}' ({', '.join(ACTIONS)})")
    by = str(by or "").strip()
    if not by:
        raise ReviewError("a review entry names who makes it (by)")
    reason = str(body.get("reason") or "").strip()
    comment = str(body.get("comment") or "").strip()
    if run is not None and run.get("state") not in ENDED:
        raise ReviewError(f"run {run.get('id')} is {run.get('state')}: only the results of an ended run can be reviewed")
    current = state(review)
    entries = review.setdefault("entries", [])
    entry: dict[str, Any] = {"id": max([int(e.get("id") or 0) for e in entries] + [0]) + 1, "at": now(), "by": by,
                             "action": action}
    if action in ("review", "revoke") and current == SIGNED_OFF:
        raise ReviewError("the review is signed off: reopen it first (with a reason)")
    if action == "review":
        target = normalize_target(body.get("target"), campaign)
        status = body.get("status") or None
        if status not in (None, ACCEPTED, REJECTED):
            raise ReviewError(f"unknown review status '{status}' (accepted or rejected; none for a comment)")
        if status and campaign:
            raise ReviewError("a campaign run's verdict comes from its runs: review their checks, steps or verdicts")
        if status and not reason:
            raise ReviewError("accepting or rejecting a result needs its reason")
        if not status and not comment and not reason:
            raise ReviewError("a review entry needs a status (with its reason) or a comment")
        was = None
        if run is not None:
            was = raw_status(run, target)
            if was is None:
                raise ReviewError(f"run {run.get('id')} has no {target_label(target)}")
            if status:
                why = _allowed(target["kind"], str(was), status)
                if why:
                    raise ReviewError(f"{target_label(target)}: {why}")
        entry.update({"target": target, **({"status": status} if status else {}), **({"was": was} if was else {}),
                      **({"reason": reason} if reason else {}), **({"comment": comment} if comment else {}),
                      **({"ref": str(body["ref"])} if body.get("ref") else {}),
                      **({"reapplied_from": body["reapplied_from"]} if body.get("reapplied_from") else {})})
    elif action == "revoke":
        try:
            number = int(body.get("entry"))
        except (TypeError, ValueError):
            raise ReviewError("revoke names the entry it revokes (entry: <id>)") from None
        target_entry = next((e for e in entries if e.get("id") == number), None)
        if target_entry is None or target_entry.get("action") != "review":
            raise ReviewError(f"no review entry {number} to revoke")
        if number in revoked(review):
            raise ReviewError(f"entry {number} is revoked already")
        if not reason:
            raise ReviewError("revoking a review entry needs its reason")
        entry.update({"revokes": number, "reason": reason})
    elif action == "sign_off":
        if current == SIGNED_OFF:
            raise ReviewError("the review is signed off already")
        if comment:
            entry["comment"] = comment
    else:                                                     # reopen
        if current != SIGNED_OFF:
            raise ReviewError("the review is not signed off: nothing to reopen")
        if not reason:
            raise ReviewError("reopening a signed-off review needs its reason")
        entry["reason"] = reason
    entries.append(entry)
    return entry


# ── the run as reviewed ────────────────────────────────────────────────────

def _met(check: dict) -> bool:
    return str(check.get("status") or "").upper() in _MET


def apply_review(run: dict, review: dict | None) -> dict:
    """The run as its review makes it: checks, steps and verdict (raw ones in raw_status, raw_state, raw_verdict),
    ``review`` (its state, entries in force, warnings). The run itself is left as it is."""
    entries = reviews(review)
    st = state(review)
    if not entries and st == NONE:
        return run
    out = copy.deepcopy(run)
    act = active(review)
    warnings: list[str] = []
    steps = out.get("steps") or []
    changed = False
    for record in steps:
        key = step_key(record)
        exploit = record.get("exploit") or {}
        checks = exploit.get("checks") or []
        checks_changed = False
        for c in checks:
            e = act.get(target_key(check_target(record, c)))
            if e:
                c["raw_status"] = c.get("status")
                c["status"] = "ACCEPTED" if e["status"] == ACCEPTED else "REJECTED"
                c["review"] = brief(e)
                checks_changed = True
        if checks_changed and record.get("state") in (SUCCEEDED, FAILED):
            new = SUCCEEDED if all(_met(c) for c in checks) else FAILED
            if new != record["state"]:
                record["raw_state"], record["state"] = record["state"], new
                record["reviewed"] = "checks"
                changed = True
        e = act.get(f"step|{key}")
        if e:
            record.setdefault("raw_state", record.get("state"))
            record["state"] = SUCCEEDED if e["status"] == ACCEPTED else FAILED
            record["reviewed"] = "step"
            record["review"] = brief(e)
            changed = changed or record["state"] != record["raw_state"]
    raw_verdict = out.get("verdict")
    verdict = raw_verdict
    if changed and out.get("state") in ENDED:
        counted = [r for r in steps if not r.get("continue_on_error")]
        before = worst(r.get("raw_state", r.get("state")) for r in counted)
        if out.get("state") == "done" and before == raw_verdict and not out.get("abort"):
            verdict = worst(r.get("state") for r in counted)
            if verdict == SUCCEEDED and raw_verdict != SUCCEEDED:
                held = [step_key(r) for r in steps if r.get("state") == SKIPPED and str(r.get("reason") or "").startswith("needs")]
                if held:
                    warnings.append(f"{len(held)} step(s) did not run, held back by a result now accepted: "
                                    + ", ".join(held[:5]) + ("…" if len(held) > 5 else ""))
        else:
            warnings.append(f"its verdict ({raw_verdict}) does not come from its steps alone"
                            + (f" ({out.get('reason')})" if out.get("reason") else "")
                            + ": the reviews of its steps and checks leave it as it is")
    e = act.get("run")
    if e and out.get("state") in ENDED:
        verdict = SUCCEEDED if e["status"] == ACCEPTED else FAILED
    if verdict != raw_verdict:
        out["raw_verdict"] = raw_verdict
        out["verdict"] = verdict
    by_target = comments(review)
    out["review"] = {
        "state": st, "verdict": verdict, "raw_verdict": raw_verdict,
        "accepted": sum(1 for x in act.values() if x["status"] == ACCEPTED),
        "rejected": sum(1 for x in act.values() if x["status"] == REJECTED),
        "entries": [{**brief(x), "target": x["target"], "label": target_label(x["target"])} for x in entries],
        "targets": {k: [brief(x) for x in v] for k, v in by_target.items()},
        **({"signed_off": brief(signed_off(review))} if signed_off(review) else {}),
        **({"warnings": warnings} if warnings else {}),
    }
    return out


def review_view(run: dict, review: dict, reviewed: dict | None = None) -> dict:
    """What ``GET /runs/{id}/review`` gives: the review, the run's effective results, the targets it can review."""
    reviewed = reviewed or apply_review(run, review)
    summary = reviewed.get("review") or {"state": state(review), "verdict": run.get("verdict"),
                                         "raw_verdict": run.get("verdict"), "accepted": 0, "rejected": 0,
                                         "entries": [], "targets": {}}
    steps = [{"step": step_key(r), "state": r.get("state"), **({"raw_state": r["raw_state"]} if "raw_state" in r else {}),
              **({"reviewed": r["reviewed"]} if r.get("reviewed") else {})} for r in reviewed.get("steps") or []]
    checks = [{**check_target(r, c), "status": c.get("status"), **({"raw_status": c["raw_status"]} if "raw_status" in c else {})}
              for r in reviewed.get("steps") or [] for c in (r.get("exploit") or {}).get("checks") or []]
    return {"run": run.get("id"), **summary, "journal": review.get("entries") or [], "steps": steps, "checks": checks}


def reapplicable(run: dict, earlier: dict, earlier_review: dict | None, review: dict | None) -> list[dict]:
    """The reviews of an earlier attempt (a re-run's ``rerun_of``) that fit this run: same target, same raw result,
    not reviewed here yet."""
    here = {target_key(e["target"]) for e in reviews(review)}
    out = []
    for e in reviews(earlier_review):
        key = target_key(e["target"])
        if key in here or e["target"].get("kind") == "campaign":
            continue
        now_raw = raw_status(run, e["target"])
        if now_raw is None or (e.get("was") and str(now_raw) != str(e["was"])):
            continue
        out.append({**brief(e), "target": e["target"], "label": target_label(e["target"]), "run": earlier.get("id")})
    return out


# ── exploit reports, as reviewed ───────────────────────────────────────────

_OVERLAY = """
<style>
.tsq-review-banner { position:sticky; top:0; z-index:50; margin:0 0 8px; padding:8px 14px; border-radius:6px;
  font:13px/1.4 system-ui,sans-serif; background:#fef0c7; color:#7a2e0e; border:1px solid #fec84b; }
.tsq-review-banner.final { background:#e7f6ee; color:#0f5132; border-color:#9fdcbc; }
fieldset.tsq-review { border:1px solid #fec84b; border-radius:6px; margin:4px 0; padding:4px 8px; font-size:12.5px; }
fieldset.tsq-review legend { font-weight:600; padding:0 4px; }
fieldset.tsq-review .raw { color:#667085; }
</style>
<script>
(function () {
  var R = __REVIEW__;
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function rows(scn, chk) {
    return Array.prototype.filter.call(document.querySelectorAll("tr.result-row"), function (r) {
      if (r.getAttribute("data-name") !== chk) return false;
      var cell = r.querySelector("td.tc-name"), path = cell ? (cell.getAttribute("onclick") || "") : "";
      return !scn || path.indexOf(" / " + scn + " / ") >= 0;
    });
  }
  function apply() {
    var banner = document.createElement("div");
    banner.className = "tsq-review-banner" + (R.state === "signed_off" ? " final" : "");
    banner.innerHTML = R.banner;
    document.body.insertBefore(banner, document.body.firstChild);
    R.items.forEach(function (x) {
      rows(x.scenario, x.check).forEach(function (row) {
        if (x.status) {
          var cell = row.querySelector(".status-cell");
          var old = cell ? cell.textContent.trim() : "";
          if (cell && old !== x.status) {
            cell.className = "status-cell status-" + x.status.toLowerCase();
            cell.textContent = x.status;
            row.setAttribute("data-status", x.status);
            try { updateParentStatuses(row); updateTopLevelCounters(old, x.status); } catch (e) {}
          }
        }
        var td = row.children[2];
        if (!td) return;
        var box = document.createElement("fieldset");
        box.className = "tsq-review";
        box.innerHTML = "<legend>Review</legend>" + x.notes.map(function (n) {
          return "<div>" + (n.status ? "<b>" + esc(n.status) + "</b> <span class='raw'>(was " + esc(n.was) + ")</span> " : "")
            + esc(n.by) + ", " + esc(n.at) + (n.reason ? ": " + esc(n.reason) : "")
            + (n.comment ? "<div>" + esc(n.comment) + "</div>" : "") + (n.ref ? " <span class='raw'>" + esc(n.ref) + "</span>" : "") + "</div>";
        }).join("");
        td.insertBefore(box, td.firstChild);
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply); else apply();
})();
</script>
"""


def exploit_overlay(record: dict, review_summary: dict | None) -> str:
    """The script that shows the reviews of an exploit step's checks in its exploit report (statuses as reviewed,
    who and why beside each check, a banner on the state of the review); empty when nothing is reviewed."""
    if not review_summary or review_summary.get("state") == NONE:
        return ""
    targets = review_summary.get("targets") or {}
    items = []
    for c in (record.get("exploit") or {}).get("checks") or []:
        notes = targets.get(target_key(check_target(record, c))) or []
        if not notes:
            continue
        status = None
        if "raw_status" in c:
            status = str(c.get("status") or "").upper()
        items.append({"scenario": c.get("scenario") or "", "check": c.get("check") or "", "status": status,
                      "notes": [{**n, "status": (n.get("status") or "").upper() or None} for n in notes]})
    e = html.escape
    step_notes = targets.get(f"step|{step_key(record)}") or []
    st = review_summary.get("state")
    if st == SIGNED_OFF:
        so = review_summary.get("signed_off") or {}
        banner = f"Final: review signed off by {e(str(so.get('by', '')))} on {e(str(so.get('at', '')))}."
    else:
        banner = "<b>DRAFT</b>: review open, results may still be accepted or rejected."
    if items:
        banner += f" {len(items)} check(s) reviewed here; the statuses shown are as reviewed."
    for n in step_notes:
        banner += (f"<br>Step {e(str(n.get('status') or 'comment'))} by {e(str(n.get('by')))}"
                   + (f": {e(str(n.get('reason') or ''))}" if n.get("reason") else "")
                   + (f" {e(str(n.get('comment') or ''))}" if n.get("comment") else ""))
    payload = json.dumps({"state": st, "banner": banner, "items": items}, default=str).replace("</", "<\\/")
    return _OVERLAY.replace("__REVIEW__", payload)


def reviewed_exploit_html(page: str, record: dict, review_summary: dict | None) -> str:
    """An exploit report (exploit_synthesis.html) with the reviews of its checks shown in it."""
    overlay = exploit_overlay(record, review_summary)
    if not overlay:
        return page
    at = page.rfind("</body>")
    return page[:at] + overlay + page[at:] if at >= 0 else page + overlay
