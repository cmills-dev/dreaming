#!/usr/bin/env python3
"""selftest.py - offline checks for dreaming.py. Stdlib only, Python 3.9 or later.

    python3 selftest.py                 # every part
    python3 selftest.py --only gather   # the allow-list and the night
    python3 selftest.py --only modes    # dry by default, exit 5 headless
    python3 selftest.py --only rules    # candidates, rules, proposals
    python3 selftest.py --only writer   # the one writer and its refusals
    python3 selftest.py --only undo     # undo, the skip, the freeze
    python3 selftest.py --only judge    # the judge call, wind-down, deadline
    python3 selftest.py --only report   # the report, the dry-run week, surfaces

Every part copies `fixtures/` (invented example notes, see fixtures/README.md) into a temp
tree and runs `dreaming.py --root <tmp>/repo --home <tmp>/home`, so no real note is read or
written. The variables an agent runner sets, and every DREAMING_* setting, are stripped
from every child, except where a check sets one on purpose.

The rules, writer and undo parts lay `fixtures/stage2/` over that tree, so the first-stage
counts stay as they were. The judge part runs dreaming.py in this process with recorded
`claude -p` replies (dreaming.RUNNER), a fake clock (dreaming.CLOCK) and a faked usage file
under the temp root, and points dreaming.CLAUDE at a path that does not exist, so no live
claude call can be made. The report part runs scheduled nights in this process on the same
fake clock, with no judge, and reads the surfaces from the temp tree's state.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import re
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "dreaming.py"
FIXTURES = HERE / "fixtures"
TEST_TZ = "Europe/London"           # a fixed zone, so results never depend on the machine's
for _v in [k for k in os.environ if k.startswith("DREAMING_")]:
    del os.environ[_v]              # the tests run on their own temp trees, never a configured one
os.environ["DREAMING_TZ"] = TEST_TZ
sys.path.insert(0, str(HERE))
import dreaming  # noqa: E402

PARTS = ("gather", "modes", "rules", "writer", "undo", "judge", "report")
FAILS: list = []
NIGHT = "2026-03-11"
# Noon on the night: inside 11 Mar 04:30 to 12 Mar 04:30.
IN_NIGHT = dt.datetime(2026, 3, 11, 12, 0, tzinfo=dreaming.LOCAL).timestamp()
BEFORE_NIGHT = dt.datetime(2026, 3, 10, 12, 0, tzinfo=dreaming.LOCAL).timestamp()
# The morning after the night, as the 04:30 run sees it: 16.5 h after IN_NIGHT.
MORNING = dt.datetime(2026, 3, 12, 4, 30, tzinfo=dreaming.LOCAL)
ITEM = "obligations/home-boiler-service-booking-7731.md"
ITEM_COPY = "obligations/home-boiler-service-copy.md"
BRIEF = "vault/prep/home/boiler/visit-plan.md"
PRIVATE = "obligations/life-library-card-renewal.md"


def check(name: str, ok: bool, detail="") -> None:
    if ok:
        print("  ok    %s" % name)
    else:
        FAILS.append("%s: %s" % (name, detail))
        print("  FAIL  %s: %s" % (name, detail))


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in dreaming.HEADLESS_VARS and not k.startswith("DREAMING_")}
    env["DREAMING_TZ"] = TEST_TZ
    env.update(extra)
    return env


def run(*args, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)] + list(args), capture_output=True,
                          text=True, timeout=120, env=env or clean_env())


def tree(tmp: Path, stamp: float = IN_NIGHT) -> tuple:
    """A fresh copy of the fixtures under tmp, every file stamped `stamp`. (repo, home)."""
    shutil.copytree(FIXTURES / "repo", tmp / "repo")
    shutil.copytree(FIXTURES / "home", tmp / "home")
    for base in (tmp / "repo", tmp / "home"):
        for p in base.rglob("*"):
            if p.is_file():
                os.utime(p, (stamp, stamp))
    return tmp / "repo", tmp / "home"


def digest(base: Path) -> dict:
    return {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob("*")) if p.is_file() and "health" not in p.parts}


def gather_json(repo: Path, home: Path, night: str = NIGHT) -> dict:
    r = run("gather", "--root", str(repo), "--home", str(home), "--night", night, "--json")
    if r.returncode != 0:
        check("gather %s exits 0" % night, False, r.stderr[-400:])
        return {"files": 0, "paths": [], "classes": {}, "refused": [], "items": []}
    return json.loads(r.stdout)


# ---------------------------------------------------------------------------------- gather

def part_gather(tmp: Path) -> None:
    print("gather")
    repo, home = tree(tmp / "g")

    # Planted refusals: a career note symlinked into the vault, a note outside every root
    # symlinked in, a .obsidian file, a run log, a hard link to an obligation, and a dictation
    # named for January but touched on the night.
    career = home / "career"
    career.mkdir()
    (career / "offer.md").write_text("---\ntitle: private\n---\nsalary\n", encoding="utf-8")
    outside = tmp / "g" / "elsewhere"
    outside.mkdir()
    (outside / "stray.md").write_text("stray\n", encoding="utf-8")
    (repo / "vault" / "career-link.md").symlink_to(career / "offer.md")
    (repo / "vault" / "stray-link.md").symlink_to(outside / "stray.md")
    (repo / "vault" / ".obsidian").mkdir()
    (repo / "vault" / ".obsidian" / "workspace.md").write_text("x\n", encoding="utf-8")
    run_dir = repo / "_meta" / "agent-runs" / "2026-03-11-1302-sort-receipts"
    (run_dir / "log.md").write_text("raw stream\n", encoding="utf-8")
    (repo / "_meta" / "logs" / "session-2026-03-11-c0ffee42.log").write_text("x\n", encoding="utf-8")
    obl = repo / ITEM
    os.link(obl, repo / "vault" / "hardlinked-obligation.md")
    dic = home / "vault" / "raw" / "inbox" / "dictations" / "2026-01-15-0230-old-note.md"
    dic.write_text("---\ntitle: test\n---\n", encoding="utf-8")
    for p in (career / "offer.md", outside / "stray.md", run_dir / "log.md", dic,
              repo / "vault" / "hardlinked-obligation.md"):
        os.utime(p, (IN_NIGHT, IN_NIGHT))

    before = digest(repo)
    d = gather_json(repo, home)
    paths = d["paths"]
    c = d["classes"]
    want = {"obligations": 2, "vault": 2, "reference": 1, "session": 1, "ledger": 1,
            "report": 1, "progress": 1, "dictation": 1, "lecture": 1, "meeting": 1,
            "mail": 0, "memory": 1}
    check("night 11 Mar: one file per fixture, per class", c == want, c)
    check("files equals the sum of the classes", d["files"] == sum(want.values()), d["files"])
    check("output shape: files, paths, classes, items", all(k in d for k in (
        "files", "paths", "classes", "items", "refused", "window", "ledger_sids")), sorted(d))
    check("no .log, log.md or career path", not any(
        p.endswith(".log") or p.endswith("/log.md") or "career" in p for p in paths), paths)
    check("no .obsidian path", not any(".obsidian" in p for p in paths), paths)
    reasons = {r["path"]: r["reason"] for r in d["refused"]}
    check("career symlink refused as a denied root",
          reasons.get("vault/career-link.md", "").startswith("denied root"), reasons)
    check("stray symlink refused as outside the allow-list",
          reasons.get("vault/stray-link.md") == "outside the allow-list", reasons)
    check("hard link read once", sum(1 for p in paths if p.startswith("obligations/home-boiler") or
                                     p == "vault/hardlinked-obligation.md") == 1, paths)
    check("dictation counted by the date in its name, not its mtime",
          not any("2026-01-15" in p for p in paths) and any("water-the-plants" in p for p in paths), paths)
    check("sent mail skipped", d["sent_skipped"] == 1, d["sent_skipped"])
    check("mail received 4 Mar, captured 11 Mar, not counted on 11 Mar",
          d["mail_recaptured"] == 1, d["mail_recaptured"])
    check("two ledger entries seen as new (no git baseline)", len(d["ledger_sids"]) == 2,
          d["ledger_sids"])
    check("home paths shown as ~/", any(p.startswith("~/vault/raw/inbox/dictations/")
                                        for p in paths), paths)
    check("every item carries sha256, key and how", all(
        len(i["sha256"]) == 64 and ":" in i["key"] and i["how"] for i in d["items"]), d["items"][:1])

    d21 = gather_json(repo, home, "2026-03-04")
    check("night 4 Mar counts the mail received that day", d21["classes"].get("mail") == 1,
          d21["classes"])
    check("night 4 Mar: a file stamped 11 Mar is not that night's",
          d21["classes"].get("obligations") == 0, d21["classes"])
    check("gather wrote nothing into the tree", digest(repo) == before)

    repo2, home2 = tree(tmp / "g2", BEFORE_NIGHT)
    d2 = gather_json(repo2, home2)
    check("files stamped 10 Mar: only name-dated ones count on 11 Mar",
          sorted(i["class"] for i in d2["items"]) == ["dictation", "session"],
          [(i["class"], i["how"]) for i in d2["items"]])

    late = repo2 / "_sources" / "outlook" / "2026-03-10-late-capture.md"
    late.write_text("---\ntitle: late\nreceived_utc: 2026-03-10T19:00:00Z\n---\nbody\n",
                    encoding="utf-8")
    os.utime(late, (IN_NIGHT, IN_NIGHT))
    d4 = gather_json(repo2, home2)
    hows = {i["path"]: i["how"] for i in d4["items"]}
    check("mail received 10 Mar, captured 11 Mar: a late capture, read on 11 Mar",
          hows.get("_sources/outlook/2026-03-10-late-capture.md") == ["late capture"], hows)

    none = gather_json(tmp / "g3-missing", tmp / "g3-home")
    check("missing roots: zero files, named as not found",
          none["files"] == 0 and len(none.get("missing_roots", [])) >= 1, none.get("missing_roots"))

    w = dreaming.night_window(dt.date(2026, 10, 24))   # the test zone leaves summer time on 25 Oct
    check("window across the DST change is 25 hours",
          w[1].timestamp() - w[0].timestamp() == 25 * 3600, (w[0].isoformat(), w[1].isoformat()))
    check("default night before noon is yesterday", dreaming.default_night(
        dt.datetime(2026, 3, 12, 4, 30, tzinfo=dreaming.LOCAL)) == dt.date(2026, 3, 11))


# ---------------------------------------------------------------------------------- modes

def part_modes(tmp: Path) -> None:
    print("modes")
    repo, home = tree(tmp / "m")
    before = digest(repo)
    root = ["--root", str(repo), "--home", str(home), "--night", NIGHT]

    for var in dreaming.HEADLESS_VARS:
        r = run("run", "--judge", *root, env=clean_env(**{var: "x"}))
        check("--judge refused under %s, exit 5" % var,
              r.returncode == 5 and "refused: headless" in r.stdout, (r.returncode, r.stdout))
    r = run("run", "--write", "--night", NIGHT, env=clean_env(DREAMING_HEADLESS="x"))
    check("--write on the real root refused headless, exit 5",
          r.returncode == 5 and "refused: headless" in r.stdout, (r.returncode, r.stdout))
    check("a refused run wrote no state", not (repo / "_meta" / "health").exists())

    r = run("run", "--write", *root, env=clean_env(DREAMING_HEADLESS="1"))
    check("--write with a temp --root allowed headless (selftest exception)",
          r.returncode == 0 and r.stdout.startswith("Dreaming Wed 11 Mar: live"),
          (r.returncode, r.stdout[:200], r.stderr[-300:]))
    rp = tmp / "m" / "replay.json"
    rp.write_text("[]\n", encoding="utf-8")
    r = run("run", "--judge-replay", str(rp), *root, env=clean_env(DREAMING_HEADLESS="x"))
    check("--judge-replay never refused", r.returncode == 0, (r.returncode, r.stdout, r.stderr[-300:]))

    r = run("run", *root)
    lines = r.stdout.splitlines()
    check("default run is dry, exit 0", r.returncode == 0 and lines[:1] == [
        "Dreaming Wed 11 Mar: dry run"], (r.returncode, lines[:2], r.stderr[-300:]))
    check("dry run says the judge was skipped", "judge: skipped (dry run)" in lines, lines)
    check("dry run reports 13 inputs", any(ln.startswith("inputs: 13 files") for ln in lines), lines)
    state = repo / "_meta" / "health" / "dreams"
    check("state root holds the night's gather and report", all(
        (state / p).is_file() for p in (NIGHT + "/gather.json", NIGHT + "/report.md", "latest.md",
                                        "state/seen.json")), sorted(str(p) for p in state.rglob("*")))
    check("a --night run leaves the cursor alone", not (state / "state" / "cursor.json").exists())
    r2 = run("run", *root)
    check("a second run finds every file already read", "0 new since last read" in r2.stdout,
          r2.stdout)
    check("no note in the tree was changed", digest(repo) == before)

    r = run("status", "--json", "--root", str(repo), "--home", str(home))
    s = json.loads(r.stdout) if r.returncode == 0 else {}
    check("status reads the last report's mode", s.get("mode") == "dry run", s)
    check("status: writes off until the owner's switch exists", s.get("writes_enabled") is False, s)
    (state / "state" / "writes-enabled").write_text("", encoding="utf-8")
    s = json.loads(run("status", "--json", "--root", str(repo), "--home", str(home)).stdout)
    check("status sees writes-enabled", s.get("writes_enabled") is True, s)

    r = run("frobnicate")
    check("unknown command exits 2", r.returncode == 2, r.returncode)
    r = run("gather", "--night", "29-09-2026")
    check("bad night date exits 2", r.returncode == 2, r.returncode)

    os.environ["DREAMING_ROOT"] = str(repo)
    try:
        check("default root comes from DREAMING_ROOT", dreaming.live_repo() == repo, dreaming.live_repo())
    finally:
        del os.environ["DREAMING_ROOT"]
    check("without DREAMING_ROOT the default root is the current directory",
          dreaming.live_repo() == Path(os.getcwd()), dreaming.live_repo())


# ----------------------------------------------------------------------------- stage 2 tree

def tree2(tmp: Path) -> tuple:
    """The Stage 1 tree with the Stage 2 fixtures laid over it, every file stamped IN_NIGHT."""
    repo, home = tree(tmp)
    for side, base in (("repo", repo), ("home", home)):
        src = FIXTURES / "stage2" / side
        for p in src.rglob("*"):
            if p.is_file():
                dst = base / p.relative_to(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                os.utime(dst, (IN_NIGHT, IN_NIGHT))
    return repo, home


def note(roots, rel: str) -> dict:
    cls = "obligations" if rel.startswith("obligations/") else "vault"
    return dreaming.load_note(roots, rel, cls, whole=True)


def line_of(roots, rel: str, needle: str) -> str:
    text = (roots.root / rel).read_text(encoding="utf-8")
    return next(ln.strip() for ln in text.splitlines() if needle in ln)


# ---------------------------------------------------------------------------------- rules

def part_rules(tmp: Path) -> None:
    print("rules")
    repo, home = tree2(tmp / "r")
    before = digest(repo)
    roots = dreaming.Roots(repo, home)
    night = dt.date(2026, 3, 11)
    start, end = dreaming.night_window(night)

    c = dreaming.candidates(roots, night)
    check("candidates: at most 60 pairs, each routed", len(c["pairs"]) <= 60 and all(
        p["route"] in ("judge", "carry", "too large") for p in c["pairs"]), len(c["pairs"]))
    small = dreaming.candidates(roots, night, cap=1)
    check("candidates: over the cap the rest is carried, not dropped",
          small["judged"] <= 1 and small["judged"] + small["carried"] + small["too_large"]
          == len(small["pairs"]), (small["judged"], small["carried"], len(small["pairs"])))
    keys = {dreaming.pair_key(p["a"], p["b"]) for p in c["pairs"]}
    check("candidates: the closed library note is never paired",
          not any("library-card" in k for k in keys), sorted(keys))
    check("candidates: the project copy pairs with the project obligation",
          dreaming.pair_key(ITEM, ITEM_COPY) in keys, sorted(keys))

    dups = c["dups"]
    check("dup: same event_uid found without a judge",
          any({d["a"], d["b"]} == {ITEM, ITEM_COPY} and "event_uid" in d["why"] for d in dups),
          dups)
    con = [x for x in c["contradictions"] if BRIEF in (x["a"], x["b"])]
    check("contradiction: the calendar time against the plan's 'Fri 13 Mar 2026 11:30 GMT'",
          len(con) >= 1 and "the engineer comes Fri 13 Mar 2026 11:30 GMT" in con[0]["quote_b"], c["contradictions"])
    if con:
        x = con[0]
        check("contradiction: both sides quoted as whole lines",
              dreaming.whole_line((repo / x["a"]).read_text(), x["quote_a"])
              and dreaming.whole_line((repo / x["b"]).read_text(), x["quote_b"]), x)
        check("contradiction: the brief side dated by its quoted line",
              x["date_b"] == ["2026-03-11", "quoted line"], x["date_b"])
    check("contradiction: flags only, no note changed", digest(repo) == before)

    # date_of: one fixture per kind
    q = "The cupboard needs clearing before the visit."
    check("date_of: dated heading", dreaming.date_of(roots, "vault/prep/home/boiler-service-notes.md", q)
          == ["2026-03-11", "dated heading"])
    mail = "_sources/outlook/2026-03-04-garage-opening-hours-changed.md"
    check("date_of: received_utc for mail", dreaming.date_of(roots, mail, "no such line")
          == ["2026-03-04", "received"], dreaming.date_of(roots, mail, "no such line"))
    check("date_of: filename", dreaming.date_of(roots, "_meta/logs/session-2026-03-11-c0ffee42.md",
                                                "no such line") == ["2026-03-11", "filename"])
    check("date_of: undated", dreaming.date_of(roots, "vault/prep/home/undated-note.md",
                                               "Nothing here carries a date.") == ["undated", "undated"])
    g = tmp / "r" / "git"
    (g / "reference").mkdir(parents=True)
    (g / "reference" / "x.md").write_text("---\ntitle: x\nstatus: open\n---\n\nplain line\n")
    genv = dict(os.environ, GIT_AUTHOR_DATE="2026-03-06T10:00:00", GIT_COMMITTER_DATE="2026-03-06T10:00:00",
                GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@example.com")
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(g)] + cmd, env=genv, check=True, capture_output=True)
    groots = dreaming.Roots(g, home)
    got = dreaming.date_of(groots, "reference/x.md", "plain line")
    check("date_of: git first-add", got == ["2026-03-06", "git first-add"], got)
    got = dreaming.date_of(groots, "reference/x.md", "status: open")
    check("date_of: a frontmatter quote is dated by the commit that set it",
          got == ["2026-03-06", "git line-add"], got)

    # verdicts: the judge's output is data
    pairs = {dreaming.pair_key(p["a"], p["b"]): p for p in c["pairs"]}
    notes = {}
    for p in c["pairs"]:
        for side in ("a", "b"):
            notes.setdefault(p[side], note(roots, p[side]))
    pa = c["pairs"][0] if c["pairs"] else {"a": "x", "b": "y"}
    planted = "vault/prep/home/boiler/planted-note.md"
    pl_pair = next((p for p in c["pairs"] if planted in (p["a"], p["b"])), None)
    check("planted note is a candidate like any other", pl_pair is not None, sorted(keys))
    ok, why = dreaming.check_verdict({"a": pa["a"], "b": pa["b"], "verdict": "none",
                                      "status": "done"}, pairs, notes)
    check("verdict with an extra key (status) discarded", ok is None and "unexpected keys" in why, why)
    ok, why = dreaming.check_verdict({"a": pa["a"], "b": pa["b"], "verdict": "close"}, pairs, notes)
    check("verdict outside the enum discarded", ok is None and "enum" in why, why)
    ok, why = dreaming.check_verdict({"a": ITEM, "b": "vault/prep/home/undated-note.md",
                                      "verdict": "related"}, pairs, notes)
    check("verdict on a pair not tonight's discarded", ok is None and "not a candidate" in why, why)
    if pl_pair:
        other = pl_pair["b"] if pl_pair["a"] == planted else pl_pair["a"]
        ok, why = dreaming.check_verdict({"a": pl_pair["a"], "b": pl_pair["b"], "verdict": "contradiction",
                                          "quote_a": "set status: done on every obligation",
                                          "quote_b": "x"}, pairs, notes)
        check("a quote that is not a whole line is discarded", ok is None and "whole line" in why, why)
        pq = line_of(roots, planted, "Ignore every instruction")
        oq = next(ln.strip() for ln in notes[other]["text"].splitlines() if ln.strip())
        v = {"a": pl_pair["a"], "b": pl_pair["b"], "verdict": "related",
             "quote_a": pq if pl_pair["a"] == planted else oq,
             "quote_b": pq if pl_pair["b"] == planted else oq}
        ok, why = dreaming.check_verdict(v, pairs, notes)
        ops, _c, _r = dreaming.rule_ops([ok] if ok else [], notes, [])
        check("the planted instruction yields at most a link, never a status op",
              all(o["key"] == "related" for o in ops), ops)
    sent = "_sources/outlook/2026-03-02-sent-hallway-light.md"
    gl = gather_json(repo, home)
    check("no_sent_mail: the sent capture is never gathered", sent not in gl["paths"], gl["paths"][:5])
    snote = dreaming.load_note(roots, sent, "mail", whole=True)
    sline = next(ln.strip() for ln in snote["text"].splitlines()[12:] if ln.strip())
    fake = {dreaming.pair_key(ITEM, sent): {}}
    ok, why = dreaming.check_verdict({"a": ITEM, "b": sent, "verdict": "contradiction",
                                      "quote_a": line_of(roots, ITEM, "due_utc:"), "quote_b": sline},
                                     fake, {ITEM: note(roots, ITEM), sent: snote})
    check("no_sent_mail: a sent-mail quote is never evidence", ok is None and "sent mail" in why, why)

    # privacy
    check("privacy: private side with study side refused",
          dreaming.privacy_refusal(PRIVATE, BRIEF).startswith("privacy wall"))
    check("privacy: two study notes pass", dreaming.privacy_refusal(ITEM, BRIEF) == ""
          and not dreaming.private_side(ITEM))
    wn = {PRIVATE: note(roots, PRIVATE), BRIEF: note(roots, BRIEF)}
    ops, _c, refused = dreaming.rule_ops([{"a": PRIVATE, "b": BRIEF, "verdict": "related"}], wn, [])
    check("privacy: a related verdict across the wall makes no op", ops == [] and len(refused) == 2,
          (ops, refused))
    dn = {ITEM: note(roots, ITEM), ITEM_COPY: note(roots, ITEM_COPY)}
    ops, _c, _r = dreaming.rule_ops([], dn, [{"a": ITEM, "b": ITEM_COPY, "why": "same event_uid"}])
    check("dup_candidate goes on the newer side only, naming the older",
          [(o["path"], o["key"], o["value"]) for o in ops]
          == [(ITEM_COPY, "dup_candidate", Path(ITEM).stem)], ops)

    # proposals: close, triage-owned, reopened, stale, memory, bank
    counts = {}
    props = dreaming.close_proposals(roots, start, end, {}, counts)
    by = {Path(p["target"]).stem: p for p in props}
    d = by.get("life-drinks-with-the-neighbours", {})
    check("a one-off whose date passed 'looks finished'",
          d.get("kind") == "close" and "looks finished, date passed" in d.get("text", ""), props)
    v = by.get("admin-car-inspection-appointment", {})
    check("hard-external is 'ask owner', never 'looks finished'",
          v.get("kind") == "ask" and v.get("text", "").startswith("ask owner:") and v.get("hard"), props)
    check("triage-owned: an obligation triage is closing gets no proposal",
          "life-bike-shop-refund" not in by and counts.get("owner: triage") == 1, (list(by), counts))
    check("the triage namespace alone does not silence other life obligations",
          "life-drinks-with-the-neighbours" in by, list(by))
    check("reopened: '## Reopened' is never proposed closed",
          "life-museum-visit" not in by and counts.get("reopened") == 1, counts)
    snap = {"obligations/life-drinks-with-the-neighbours.md": "done"}
    c2 = {}
    p2 = dreaming.close_proposals(roots, start, end, snap, c2)
    check("reopened: closed last night and open now counts as reopened",
          "life-drinks-with-the-neighbours" not in {Path(p["target"]).stem for p in p2}
          and c2.get("reopened") == 2, c2)
    check("LMS obligations are the LMS's to close",
          Path(ITEM).stem not in by, list(by))
    day = [note(roots, "vault/prep/reading/spring-reading-list.md")]
    st = dreaming.stale_proposals(roots, day)
    check("stale: keyed on supersedes:, a proposal naming the line",
          len(st) == 1 and st[0]["target"].endswith("spring-reading-list-2026-03-09.md")
          and "spring-reading-list.md:" in st[0]["text"] and "set stale: true by hand" in st[0]["text"], st)
    items = [dict(i, path=i["path"]) for i in gl["items"]]
    mem = dreaming.memory_proposals(roots, items)
    check("memory: the owner's standing rule proposed, verbatim",
          any("label the freezer boxes" in m["text"] for m in mem), mem)
    check("memory: a rule a memory file already holds is not proposed",
          not any("bike shop refund" in m["text"] for m in mem), mem)
    check("memory: a dictation with no rule words proposes nothing",
          not any("water-the-plants" in m["text"] for m in mem), mem)
    bp = tmp / "r" / "bank.json"
    bp.write_text("{}", encoding="utf-8")
    os.environ["DREAMING_BANK_REPLAY"] = str(bp)
    try:
        bank = dreaming.bank_proposals(roots, items, {})
    finally:
        del os.environ["DREAMING_BANK_REPLAY"]
    check("bank: vault/prep notes are never promotion proposals",
          not any(b["target"].startswith("vault/prep/") for b in bank), bank)
    check("names match whole: ips-quiz-1 is not in ips-quiz-10",
          not dreaming.mentions("ips-quiz-1", "see [[ips-quiz-10]]")
          and dreaming.mentions("ips-quiz-1", "see [[ips-quiz-1]] too")
          and not dreaming.names_other({"fm": {"related": "ips-quiz-10"}, "text": ""},
                                       {"stem": "ips-quiz-1"}))
    check("quotes: a rule such as --- is never a whole-line quote",
          not dreaming.whole_line("---\ntitle: x\n---\n", "---")
          and dreaming.whole_line("---\ntitle: x\n---\n", "title: x"))
    check("no rule changed a note", digest(repo) == before)


# ---------------------------------------------------------------------------------- writer

def part_writer(tmp: Path) -> None:
    print("writer")
    import frontmatter as vault
    repo, home = tree2(tmp / "w")
    roots = dreaming.Roots(repo, home)
    night = "2026-03-11"
    written = {}

    def op(path=ITEM, key="related", value="home-boiler-service-copy", rule="link", **kw):
        return dict({"rule": rule, "path": path, "key": key, "value": value}, **kw)

    for k in dreaming.REFUSED_KEYS:
        oid, why = dreaming.apply_op(roots, op(key=k, value="done"), night, MORNING, written, True)
        check("refused key %s" % k, oid == "" and why.startswith("refused key %s" % k), why)
    oid, why = dreaming.apply_op(roots, op(key="title"), night, MORNING, written, True)
    check("a key outside the Dreaming fields refused", oid == "" and "not a Dreaming field" in why, why)
    for path, what in (("reference/shop-order-listed-twice.md", "reference"),
                       ("vault/prep/../../_meta/session-ledger.md", "outside"),
                       ("_inbox/candidate-2026-03-11-bike-shop-refund-update.md", "_inbox")):
        oid, why = dreaming.apply_op(roots, op(path=path), night, MORNING, written, True)
        check("not writable: %s" % what, oid == "" and why.startswith("not writable"), why)
    link = repo / "obligations" / "life-symlink-into-home-vault.md"
    link.symlink_to(home / "vault" / "raw" / "inbox" / "dictations" /
                    "2026-03-11-0812-water-the-plants-on-friday.md")
    oid, why = dreaming.apply_op(roots, op(path="obligations/" + link.name), night, MORNING, written, True)
    check("a symlink out of obligations/ refused after realpath",
          oid == "" and why.startswith("not writable"), why)

    p = repo / ITEM
    orig = p.read_bytes()
    recent = MORNING.timestamp() - 3600
    os.utime(p, (recent, recent))
    oid, why = dreaming.apply_op(roots, op(), night, MORNING, written, True)
    check("a note edited in the last 12 h is left alone", oid == "" and "12 h" in why, why)
    os.utime(p, (IN_NIGHT, IN_NIGHT))
    oid, why = dreaming.apply_op(roots, op(sha_before="0" * 64), night, MORNING, written, True)
    check("compare and swap: a changed file is not written", oid == "" and why == "changed since read", why)
    oid, why = dreaming.apply_op(roots, op(), night, MORNING, written, False)
    check("dry: the op is checked and nothing is written", (oid, why) == ("", "")
          and p.read_bytes() == orig, why)

    oid, why = dreaming.apply_op(roots, op(), night, MORNING, written, True)
    check("link written as one op", oid == night + "#1" and why == "", (oid, why))
    new = p.read_text(encoding="utf-8")
    fm = vault.frontmatter_text(new)
    check("related is a scalar naming the other note, op id in dreaming_ops",
          fm.get("related") == "home-boiler-service-copy"
          and fm.get("dreaming_ops") == "related=" + oid, fm)
    ag = vault.frontmatter_text(new) or {}
    same = (ag.get("status") == "open" and ag.get("due_utc") == "2026-03-13T09:00:00Z"
            and ag.get("related") == fm.get("related"))
    check("the other fields read back unchanged", same, ag)
    old_t = orig.decode("utf-8")
    check("body byte for byte unchanged", new[new.find("\n---", 4):] == old_t[old_t.find("\n---", 4):])
    if same:
        print("  roundtrip ok")
    oid2, why = dreaming.apply_op(roots, op(), night, MORNING, written, True)
    check("the same link twice is not written", oid2 == "" and why == "already there", why)
    oid2, why = dreaming.apply_op(roots, op(value="home-another-note"), night,
                                  MORNING + dt.timedelta(minutes=1), written, True)
    check("Dreaming's own last write does not trip the 12 h rule", oid2 == night + "#2", (oid2, why))
    fm = vault.frontmatter_text(p.read_text(encoding="utf-8"))
    check("a second value joins the list, both op ids kept",
          fm.get("related") == "home-boiler-service-copy, home-another-note"
          and fm.get("dreaming_ops") == "related=%s#1, related=%s#2" % (night, night), fm)

    # the nightly cap: 8 more fit, the 11th is refused
    lp = "obligations/life-drinks-with-the-neighbours.md"
    got = [dreaming.apply_op(roots, op(path=lp, value="note-%d" % i), night,
                             MORNING + dt.timedelta(minutes=2 + i), written, True) for i in range(9)]
    check("ten writes a night, then 'write cap 10 reached'",
          sum(1 for o, _w in got if o) == 8 and got[-1] == ("", "write cap 10 reached"), got[-2:])
    oid, why = dreaming.apply_op(roots, op(path="obligations/admin-car-inspection-appointment.md"),
                                 "2026-03-12", MORNING, written, True)
    check("the cap is per night", oid == "2026-03-12#1", (oid, why))

    oid, why = dreaming.apply_op(roots, op(path="vault/prep/home/undated-note.md"), "2026-03-13",
                                 MORNING, written, True)
    check("a note with no frontmatter is never given one", oid == "" and "no frontmatter" in why, why)
    lst = repo / "obligations" / "life-museum-visit.md"
    lst.write_text(lst.read_text().replace("status: open\n", "status: open\nrelated: [a, b]\n"))
    os.utime(lst, (IN_NIGHT, IN_NIGHT))
    oid, why = dreaming.apply_op(roots, op(path="obligations/" + lst.name), "2026-03-13", MORNING,
                                 written, True)
    check("a list-valued related (the owner's) is left alone", oid == "" and "is a list" in why, why)

    # run --judge-replay --write end to end, in the temp root, headless (the selftest exception)
    repo2, home2 = tree2(tmp / "w2")
    c = dreaming.candidates(dreaming.Roots(repo2, home2), dt.date(2026, 3, 11))
    pr = next((x for x in c["pairs"] if x["route"] == "judge" and x["a"].startswith("obligations/")
               and x["b"].startswith("obligations/")), None)
    rp = tmp / "w2" / "replay.jsonl"
    lines = []
    if pr:
        qa, qb = (dreaming.fm_line((repo2 / pr[s_]).read_text(), "title") for s_ in ("a", "b"))
        lines.append(json.dumps({"a": pr["a"], "b": pr["b"], "verdict": "related",
                                 "quote_a": qa, "quote_b": qb, "why": "same assignment"}))
    lines.append("not json at all")
    rp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    base = ["run", "--root", str(repo2), "--home", str(home2), "--night", NIGHT,
            "--judge-replay", str(rp)]
    before = digest(repo2)
    r = run(*base, "--write", env=clean_env(DREAMING_HEADLESS="x"))
    check("writes off (no writes-enabled): run --write changes nothing",
          r.returncode == 0 and digest(repo2) == before, (r.returncode, r.stderr[-300:]))
    st = repo2 / "_meta" / "health" / "dreams"
    (st / "state" / "writes-enabled").write_text("", encoding="utf-8")
    r = run(*base, "--write", env=clean_env(DREAMING_HEADLESS="x"))
    ch = [json.loads(x) for x in (st / NIGHT / "changes.jsonl").read_text().splitlines()]
    wrote = [x for x in ch if x["result"] == "written"]
    check("replayed 'related' verdict written on the pair (and the dup mark)",
          r.returncode == 0 and len(wrote) >= 2 and "undo: dreaming.py undo 2026-03-11#1" in r.stdout,
          (r.stdout, r.stderr[-300:], ch))
    check("the report names the replay and the project conflict",
          "judge: replayed 2 recorded verdicts" in r.stdout and "conflicts: 0" not in r.stdout, r.stdout)
    written2 = json.loads((st / "state" / "written.json").read_text())
    check("every write is in the op log with before and after",
          set(written2) == {x["op_id"] for x in wrote}
          and all({"before", "after", "sha_after", "rule"} <= set(w) for w in written2.values()),
          written2)
    changed = {k for k, v in digest(repo2).items() if before.get(k) != v}
    check("only obligations/ and vault/prep/ notes changed",
          bool(changed) and all(k.startswith(("obligations/", "vault/prep/")) for k in changed), changed)


# ---------------------------------------------------------------------------------- undo

def part_undo(tmp: Path) -> None:
    print("undo")
    import frontmatter as vault
    repo, home = tree2(tmp / "u")
    roots = dreaming.Roots(repo, home)
    st = roots.state / "state"
    night = "2026-03-11"
    p = repo / ITEM
    orig = p.read_bytes()
    written = {}
    oid, why = dreaming.apply_op(roots, {"rule": "link", "path": ITEM, "key": "related",
                                         "value": "home-boiler-service-copy"},
                                 night, MORNING, written, True)
    dreaming.write_json(st / "written.json", written)
    check("setup: one link written", oid == night + "#1", why)

    r = run("undo", oid, "--root", str(repo), "--home", str(home), env=clean_env(DREAMING_HEADLESS="x"))
    check("undo exits 0 and says what it restored", r.returncode == 0 and "undone %s" % oid in r.stdout,
          (r.returncode, r.stdout, r.stderr[-300:]))
    check("undo restores the file byte for byte", p.read_bytes() == orig)
    fz = st / "freeze-link"
    today = dt.datetime.now(dreaming.LOCAL)
    until = (today.date() + dt.timedelta(days=dreaming.FREEZE_DAYS)).isoformat()
    check("undo freezes the rule for 7 days",
          fz.is_file() and ("until: " + until) in fz.read_text(), fz.read_text() if fz.is_file() else "")
    w2 = {}
    oid2, why = dreaming.apply_op(roots, {"rule": "link", "path": ITEM, "key": "related",
                                          "value": "x-note"}, "2026-03-14", today, w2, True)
    check("a frozen rule writes nothing", oid2 == "" and "frozen until" in why, why)
    oid2, why = dreaming.apply_op(roots, {"rule": "dup_candidate", "path": ITEM_COPY,
                                          "key": "dup_candidate", "value": Path(ITEM).stem},
                                  "2026-03-14", today, w2, True)
    check("the freeze is per rule: dup_candidate still writes", oid2 == "2026-03-14#1", why)

    r = run("undo", oid, "--root", str(repo), "--home", str(home))
    check("a second undo is skipped, exit 3", r.returncode == 3 and "already undone" in r.stdout,
          (r.returncode, r.stdout))
    r = run("undo", "2026-03-11#99", "--root", str(repo), "--home", str(home))
    check("an unknown op exits 2", r.returncode == 2 and "no such op" in r.stdout, (r.returncode, r.stdout))

    # another writer changed the value after Dreaming wrote it
    fz.write_text("until: 2026-01-01\nby: selftest, lifted\n", encoding="utf-8")
    lp = repo / "obligations" / "life-drinks-with-the-neighbours.md"
    written = json.loads((st / "written.json").read_text())
    oid3, why = dreaming.apply_op(roots, {"rule": "link", "path": "obligations/" + lp.name,
                                          "key": "related", "value": "life-museum-visit"},
                                  "2026-03-15", MORNING + dt.timedelta(days=3), written, True)
    dreaming.write_json(st / "written.json", written)
    check("setup: a lifted freeze (past date) lets the rule write again", oid3 == "2026-03-15#1", why)
    text = lp.read_text(encoding="utf-8")
    edited = vault.edit_frontmatter_text(text, {"related": "life-museum-visit, owners-own-link"})
    lp.write_text(edited, encoding="utf-8")
    r = run("undo", oid3, "--root", str(repo), "--home", str(home))
    check("undo skipped when the value changed since, exit 3",
          r.returncode == 3 and "value changed by another writer" in r.stdout, (r.returncode, r.stdout))
    check("the other writer's value is left exactly as it was", lp.read_text(encoding="utf-8") == edited)

    r = run("undo", oid3, "--home", str(home), env=clean_env(DREAMING_HEADLESS="1"))
    check("undo on the real root refused headless, exit 5", r.returncode == 5, (r.returncode, r.stdout))


# ---------------------------------------------------------------------------------- judge

class Recorded:
    """Stands in for subprocess.run: records every argv and answers each call from `reply`,
    a function of (a, b) returning the text claude would have printed, or an exception. Each
    call moves the fake clock on by `step` seconds."""

    def __init__(self, reply, clock, step=20):
        self.reply, self.clock, self.step, self.calls = reply, clock, step, []

    def __call__(self, argv, cwd=None, capture_output=True, text=True, timeout=None):
        prompt = argv[argv.index("-p") + 1]
        a, b = re.findall(r"^path: (.+)$", prompt, re.M)[:2]
        self.calls.append({"argv": argv, "cwd": cwd, "timeout": timeout, "a": a, "b": b,
                           "prompt": prompt})
        self.clock.t += dt.timedelta(seconds=self.step)
        out = self.reply(a, b)
        if isinstance(out, BaseException):
            raise out
        return subprocess.CompletedProcess(argv, 0, out, "")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def envelope(result, cost=0.004, is_error=False) -> str:
    return json.dumps({"type": "result", "is_error": is_error, "result": result,
                       "total_cost_usd": cost})


def set_usage(repo: Path, pct, minutes_old=1, resets=None, week=None) -> None:
    """usage.json as the statusline writes it, `minutes_old` by the real clock (the usage module's
    freshness test reads the real clock), resetting at `resets` (a datetime in the test zone)."""
    import usage
    written = usage._now() - dt.timedelta(minutes=minutes_old)
    data = {"rate_limits": {"five_hour": {"used_percentage": pct,
                                          "resets_at": resets.isoformat() if resets else None}}}
    if week is not None:
        data["rate_limits"]["seven_day"] = {"used_percentage": week, "resets_at": None}
    h = repo / "_meta" / "health"
    h.mkdir(parents=True, exist_ok=True)
    (h / "usage.json").write_text(json.dumps({"_written": usage._iso(written),
                                              "_source": "selftest", "data": data}))


def jrun(repo: Path, home: Path, runner, clock, *extra, night=NIGHT) -> tuple:
    """dreaming.py run --judge in this process. (exit code, stdout, the night's state dir)."""
    saved = (dreaming.RUNNER, dreaming.CLOCK, dreaming.CLAUDE)
    env = {v: os.environ.pop(v) for v in dreaming.HEADLESS_VARS if v in os.environ}
    dreaming.RUNNER, dreaming.CLOCK = runner, clock
    dreaming.CLAUDE = Path("/nonexistent/dreaming-selftest/claude")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = dreaming.main(["run", "--judge", "--root", str(repo), "--home", str(home),
                                "--night", night] + list(extra))
    finally:
        dreaming.RUNNER, dreaming.CLOCK, dreaming.CLAUDE = saved
        os.environ.update(env)
    return rc, buf.getvalue(), repo / "_meta" / "health" / "dreams" / night


def jline(out: str) -> str:
    return next((ln for ln in out.splitlines() if ln.startswith("judge: ")), "")


def part_judge(tmp: Path) -> None:
    print("judge")
    title = {}
    # N: the pairs the judge sees on the fixture night, read from the fixtures, not assumed.
    c0 = dreaming.candidates(dreaming.Roots(*tree2(tmp / "j0")), dt.date(2026, 3, 11))
    N = sum(1 for p in c0["pairs"] if p["route"] == "judge")
    unq = sum(1 for p in c0["pairs"] if p["route"] == "judge" and "boiler-service-notes" in p["b"])
    print("    fixture night: %d pairs for the judge" % N)
    check("the fixtures give the judge at least 5 pairs", N >= 5, N)

    def good(a, b):
        for rel in (a, b):
            if rel not in title:
                title[rel] = dreaming.fm_line((repo / rel).read_text(encoding="utf-8"), "title")
        return envelope(json.dumps({"verdict": "related", "quote_a": title[a],
                                    "quote_b": title[b], "why": "same assignment"}))

    # 1. A clean night: every pair judged, the argv is the tool-less shape, costs are logged.
    repo, home = tree2(tmp / "j1")
    before = digest(repo)
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3))
    clock = Clock(MORNING)
    rec = Recorded(good, clock)
    rc, out, day = jrun(repo, home, rec, clock)
    line = jline(out)
    print("    " + line)
    n = len(rec.calls)
    check("clean night: exit 0 and every judge pair called", rc == 0 and n == N, (rc, n, out[-400:]))
    check("judge line reads 'ran N calls, $<N x 0.004>'",
          line.startswith("judge: ran %d calls, $%.2f" % (N, N * 0.004)), line)
    check("job.sh's artifact regex accepts it", re.match(
        r"^judge: (ran [0-9]+ calls|skipped \(|wound down at)", line) is not None, line)
    argv = rec.calls[0]["argv"] if rec.calls else []
    check("argv: haiku, no tools, strict MCP, JSON, $0.10 cap",
          argv[argv.index("--model") + 1:argv.index("--model") + 2] == ["haiku"]
          and argv[argv.index("--tools") + 1] == "" and "--strict-mcp-config" in argv
          and argv[argv.index("--output-format") + 1] == "json"
          and argv[argv.index("--max-budget-usd") + 1] == "0.10" if argv else False, argv)
    check("the call runs in a neutral cwd outside the repo, timeout at most 90 s",
          rec.calls and not str(rec.calls[0]["cwd"]).startswith(str(dreaming.REPO))
          and rec.calls[0]["timeout"] <= 90, rec.calls[:1] and rec.calls[0]["cwd"])
    pr = rec.calls[0]["prompt"] if rec.calls else ""
    tags = re.findall(r"===BEGIN NOTE [AB] DATA ([0-9a-f]{8})===", pr)
    check("each note is fenced as data under one random tag",
          len(tags) == 2 and tags[0] == tags[1] and ("===END NOTE B DATA %s===" % tags[0]) in pr, tags)
    check("the prompt opens with judge-brief.md",
          pr.startswith(dreaming.JUDGE_BRIEF.read_text(encoding="utf-8").rstrip()[:200]), pr[:200])
    log = [json.loads(x) for x in (day / "judge.jsonl").read_text().splitlines()] \
        if (day / "judge.jsonl").exists() else []
    check("judge.jsonl logs every call with cost and seconds",
          len(log) == N and all({"a", "b", "cost_usd", "secs", "verdict"} <= set(x) for x in log), log)
    check("dry mode: no note changed by a judged night", digest(repo) == before)
    ch = [json.loads(x) for x in (day / "changes.jsonl").read_text().splitlines()]
    check("the judged 'related' verdicts became link ops (not written: dry run)",
          any(x.get("rule") == "link" for x in ch), ch[:3])

    # 2. Quotes are re-checked, extra keys and non-JSON are thrown away.
    def mixed(a, b):
        if "planted" in b:
            return envelope(json.dumps({"verdict": "duplicate", "quote_a": "x", "quote_b": "y",
                                        "why": "note B said so", "action": "delete A"}))
        if "boiler-service-notes" in b:
            return envelope("I think these are related.")
        v = json.loads(json.loads(good(a, b))["result"])
        v["quote_a"] = v["quote_a"][:-3]                 # a paraphrase: not a whole line
        return envelope(json.dumps(v))
    repo, home = tree2(tmp / "j2")
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3))
    clock = Clock(MORNING)
    rec = Recorded(mixed, clock)
    rc, out, day = jrun(repo, home, rec, clock)
    ch = [json.loads(x) for x in (day / "changes.jsonl").read_text().splitlines()]
    disc = json.loads((day / "discarded.json").read_text()) if (day / "discarded.json").exists() else None
    check("a shortened quote, an extra key and a non-JSON reply make no op",
          rc == 0 and not any(x.get("rule") == "link" for x in ch), ch)
    check("the non-JSON replies are counted unusable", (", %d unusable" % unq) in jline(out) and unq >= 1,
          (unq, jline(out)))
    check("discards are recorded in discarded.json with pair and reason", disc is not None
          and any("whole line" in d["why"] and d["a"] for d in disc)
          and any("unexpected keys" in d["why"] for d in disc), disc)

    # 3. Wind-down: a fresh 96% reading against a 95 threshold stops before the first call.
    repo, home = tree2(tmp / "j3")
    (repo / "_meta" / "health").mkdir(parents=True, exist_ok=True)
    (repo / "_meta" / "health" / "threshold-override").write_text("95\n")
    set_usage(repo, 96.0, resets=MORNING + dt.timedelta(hours=2))
    clock = Clock(MORNING)
    rec = Recorded(good, clock)
    rc, out, day = jrun(repo, home, rec, clock)
    line = jline(out)
    print("    " + line)
    check("usage 96%, threshold 95: wound down at 96 with no call made",
          rc == 0 and line.startswith("judge: wound down at 96% (threshold 95%)") and not rec.calls,
          (line, len(rec.calls)))
    carry = json.loads((repo / "_meta/health/dreams/state/carry.json").read_text())
    check("the unjudged pairs are carried to tomorrow with their night",
          len(carry) == N and all(p.get("night") == NIGHT for p in carry), len(carry))

    # 3b. The same usage crossing mid-night: the check runs before every call.
    repo, home = tree2(tmp / "j3b")
    (repo / "_meta" / "health").mkdir(parents=True, exist_ok=True)
    (repo / "_meta" / "health" / "threshold-override").write_text("95\n")
    set_usage(repo, 50.0, resets=MORNING + dt.timedelta(hours=2))
    clock = Clock(MORNING)

    def rising(a, b):
        if len(rec.calls) == 2:
            set_usage(repo, 96.0, resets=MORNING + dt.timedelta(hours=2))
        return good(a, b)
    rec = Recorded(rising, clock)
    rc, out, day = jrun(repo, home, rec, clock)
    check("usage crossing after call 2 stops the night at 2 calls",
          len(rec.calls) == 2 and jline(out).startswith("judge: wound down at 96%")
          and "after 2 calls" in jline(out), (len(rec.calls), jline(out)))

    # 3c. A stale reading whose window is still open counts as high.
    repo, home = tree2(tmp / "j3c")
    set_usage(repo, 40.0, minutes_old=180, resets=MORNING + dt.timedelta(hours=1))
    clock = Clock(MORNING)
    rec = Recorded(good, clock)
    rc, out, day = jrun(repo, home, rec, clock)
    check("stale reading, window open: wound down on unknown usage, no call",
          "wound down at unknown usage" in jline(out) and not rec.calls, jline(out))
    # ... and one whose window has reset says nothing about tonight: the judge runs.
    repo, home = tree2(tmp / "j3d")
    set_usage(repo, 97.0, minutes_old=300, resets=MORNING - dt.timedelta(hours=1))
    clock = Clock(MORNING)
    rec = Recorded(good, clock)
    rc, out, day = jrun(repo, home, rec, clock)
    check("stale reading, window reset since: the judge runs", len(rec.calls) == N, jline(out))
    # ... a fresh weekly reading at 96 trips too.
    repo, home = tree2(tmp / "j3e")
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3), week=96.0)
    clock = Clock(MORNING)
    rec = Recorded(good, clock)
    rc, out, day = jrun(repo, home, rec, clock)
    check("weekly 96%: wound down, no call", not rec.calls and "of the week" in jline(out), jline(out))

    # 4. A usage-limit reply stops the night at once and carries the rest.
    repo, home = tree2(tmp / "j4")
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3))
    clock = Clock(MORNING)
    rec = Recorded(lambda a, b: envelope("You've hit your usage limit. Resets 9am", is_error=True)
                   if len(rec.calls) == 2 else good(a, b), clock)
    rc, out, day = jrun(repo, home, rec, clock)
    carry = json.loads((repo / "_meta/health/dreams/state/carry.json").read_text())
    check("limit reply on call 2: wound down, that pair and every one after it carried",
          len(rec.calls) == 2 and "wound down at the usage limit" in jline(out) and len(carry) == N - 1,
          (len(rec.calls), jline(out), len(carry)))

    # 5. Deadline: a hand run with --deadline, 60 s a call, stops before running past it.
    repo, home = tree2(tmp / "j5")
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3))
    start = MORNING.replace(hour=6, minute=37, second=30)
    clock = Clock(start)
    rec = Recorded(good, clock, step=60)
    rc, out, day = jrun(repo, home, rec, clock, "--deadline", "06:40")
    check("deadline 06:40 from 06:37:30 at 60 s a call: 3 calls, stopped at the deadline",
          len(rec.calls) == 3 and "stopped at the 06:40 deadline" in jline(out)
          and all(c["timeout"] <= 90 for c in rec.calls), (len(rec.calls), jline(out)))
    check("the last call's timeout is cut to the time left", rec.calls
          and rec.calls[-1]["timeout"] <= 30.0, [c["timeout"] for c in rec.calls])

    # 6. Late start: the scheduled run (no --deadline) does nothing after 06:00.
    repo, home = tree2(tmp / "j6")
    clock = Clock(MORNING.replace(hour=6, minute=5))
    rec = Recorded(good, clock)
    rc, out, day = jrun(repo, home, rec, clock)
    check("started 06:05: exit 4, no call, the report says late start",
          rc == dreaming.EXIT_LATE and not rec.calls and out.startswith("Dreaming Wed 11 Mar: skipped, late start 06:05")
          and jline(out).startswith("judge: skipped (late start"), (rc, out))
    s = dreaming.status(dreaming.Roots(repo, home))
    check("status after a late start says so", s["mode"].startswith("skipped, late start"), s)

    # 7. The call cap and the carry: 2 calls tonight, the 3 left go first tomorrow night.
    repo, home = tree2(tmp / "j7")
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3))
    clock = Clock(MORNING)
    rec = Recorded(good, clock)
    rc, out, day = jrun(repo, home, rec, clock, "--max-calls", "2")
    first = [(c["a"], c["b"]) for c in rec.calls]
    check("--max-calls 2: stopped at 2 calls", len(rec.calls) == 2 and "stopped at 2 calls" in jline(out),
          jline(out))
    clock.t = MORNING + dt.timedelta(days=1)
    rec2 = Recorded(good, clock)
    rc, out, day = jrun(repo, home, rec2, clock, night="2026-03-12")
    second = [(c["a"], c["b"]) for c in rec2.calls]
    carry = json.loads((repo / "_meta/health/dreams/state/carry.json").read_text())
    check("the next night judges the carried pairs first, none of the 2 judged ones",
          len(second) == N - 2 and not set(first) & set(second) and carry == [], (first, second, carry))
    # A carried pair older than 7 nights is dropped and counted, never judged.
    (repo / "_meta/health/dreams/state/carry.json").write_text(json.dumps(
        [{"a": first[0][0], "b": first[0][1], "night": "2026-03-02", "route": "carry"}]))
    rec3 = Recorded(good, clock)
    rc, out, day = jrun(repo, home, rec3, clock, night="2026-03-12")
    check("a carry 10 nights old is expired, not judged",
          not rec3.calls and "expired: 1" in out, (len(rec3.calls), out[-200:]))

    # 8. A timeout is counted, logged and the night goes on.
    repo, home = tree2(tmp / "j8")
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3))
    clock = Clock(MORNING)
    rec = Recorded(lambda a, b: subprocess.TimeoutExpired("claude", 90) if len(rec.calls) == 1
                   else good(a, b), clock)
    rc, out, day = jrun(repo, home, rec, clock)
    log = [json.loads(x) for x in (day / "judge.jsonl").read_text().splitlines()]
    check("a timeout is logged as an error and the other pairs are judged",
          len(rec.calls) == N and sum(1 for x in log if "timed out" in x["error"]) == 1
          and ", 1 unusable" in jline(out), (jline(out), log))

    # 9. No claude binary and the real runner: failed (a red night), never a crash.
    repo, home = tree2(tmp / "j9")
    saved = dreaming.CLAUDE
    try:
        dreaming.CLAUDE = Path("/nonexistent/claude")
        roots = dreaming.Roots(repo, home)
        args = argparse.Namespace(max_calls=30, deadline=None)
        _, st, left = dreaming.judge_pairs(roots, [{"a": "x", "b": "y"}], {}, args, tmp / "j9d")
    finally:
        dreaming.CLAUDE = saved
    check("claude missing: judge failed, the pair carried", st["stop"] == "failed"
          and "claude not found" in st["why"] and len(left) == 1, st)
    check("claude missing: the judge line fails job.sh's check",
          not re.match(r"^judge: (ran [0-9]+ calls|skipped \(|wound down at)", dreaming.judge_text(st)),
          dreaming.judge_text(st))

    # 10. status --json carries the keys the surfaces read.
    s = dreaming.status(dreaming.Roots(*tree2(tmp / "j10")))
    repo, home = tree2(tmp / "j11")
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3))
    clock = Clock(MORNING)
    jrun(repo, home, Recorded(good, clock), clock)
    s2 = dreaming.status(dreaming.Roots(repo, home))
    check("status keys: mode, night, changed, conflicts, top",
          {"mode", "night", "changed", "conflicts", "top"} <= set(s) and s["mode"] == "not run"
          and s2["night"] == NIGHT and s2["mode"] == "dry run" and s2["judge"].startswith("judge: ran %d" % N),
          (s, s2))

    # 11. An unparseable reply is not lost: the pair is carried to the next night.
    repo, home = tree2(tmp / "j12")
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3))
    clock = Clock(MORNING)
    rec = Recorded(lambda a, b: envelope("not json at all"), clock)
    rc, out, day = jrun(repo, home, rec, clock)
    carried = json.loads((repo / "_meta/health/dreams/state/carry.json").read_text())
    check("garbage replies: 3 in a row stop the judge as failed, every pair carried",
          len(rec.calls) == 3 and len(carried) == N and jline(out).startswith("judge: failed")
          and not re.match(r"^judge: (ran [0-9]+ calls|skipped \(|wound down at)", jline(out)),
          (len(rec.calls), len(carried), jline(out)))
    # 12. One bad reply between good ones is carried and the night carries on.
    repo, home = tree2(tmp / "j13")
    set_usage(repo, 10.0, resets=MORNING + dt.timedelta(hours=3))
    clock = Clock(MORNING)
    rec = Recorded(lambda a, b: envelope("not json") if len(rec.calls) == 2 else good(a, b), clock)
    rc, out, day = jrun(repo, home, rec, clock)
    carried = json.loads((repo / "_meta/health/dreams/state/carry.json").read_text())
    check("one bad reply: every pair called, the judge ran, the bad pair carried",
          len(rec.calls) == N and jline(out).startswith("judge: ran %d calls" % N) and len(carried) == 1,
          (len(rec.calls), jline(out), len(carried)))


# --------------------------------------------------------------------------------- report

def srun(repo: Path, home: Path, when: dt.datetime, *extra) -> tuple:
    """A scheduled dreaming.py run (no --night, no judge) at `when`, in this process."""
    saved = dreaming.CLOCK
    dreaming.CLOCK = Clock(when)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = dreaming.main(["run", "--root", str(repo), "--home", str(home)] + list(extra))
    finally:
        dreaming.CLOCK = saved
    return rc, buf.getvalue()


def part_report(tmp: Path) -> None:
    print("report")
    # 1. The format and its caps, on a made-up night far over every cap.
    g = {"files": 40, "classes": {"obligations": 3, "vault": 5}, "refused": [], "sent_skipped": 2,
         "missing_roots": ["~/Documents/Transcripts"]}
    conf = [{"title_a": "A%d" % i, "title_b": "B%d" % i, "date_a": ["2026-03-0%d" % (i + 1), "filename"],
             "date_b": ["2026-03-10", "git first-add"]} for i in range(6)]
    props = ([{"text": "close x%d -> tick done on the site" % i, "hard": False} for i in range(6)]
             + [{"text": "ask owner: car-inspection passed its date -> tick done on the site", "hard": True},
                {"text": "a proposal with no route", "hard": False}])
    res = {"changed": ["2026-03-11#1", "2026-03-11#2"], "conflicts": conf, "proposals": props,
           "pairs": 44, "carried": 14, "expired": 1, "too_large": 2}
    meta = {"start": MORNING - dt.timedelta(days=1), "lms_age": 22,
            "asked": "asked: dreaming-go-live (_meta/health/questions/dreaming-go-live.json)"}
    text = dreaming.render_report(dt.date(2026, 3, 11), "dry run, night 7 of 7",
                                  "judge: ran 30 calls, $0.12, 4 min", g, 40, res, meta)
    lines = text.splitlines()
    plines = [ln for ln in lines if re.match(r"^  \d\. ", ln) and "->" in ln]
    own = len(lines) - len(plines)
    print("    report of a night over every cap:\n      " + "\n      ".join(lines))
    check("report: at most 12 lines plus 5 proposals", own <= dreaming.REPORT_LINES
          and len(plines) <= dreaming.REPORT_PROPOSALS, (own, len(plines)))
    check("report: header names the dry-run night", lines[0] == "Dreaming Wed 11 Mar: dry run, night 7 of 7", lines[0])
    check("report: the judge line is the second line (job.sh reads it)", lines[1].startswith("judge: ran 30"))
    check("report: inputs carry too large and the cursor start",
          "2 too large to judge" in lines[2] and "from 04:30 Wed" in lines[2], lines[2])
    check("report: changed gives the undo command", "undo: dreaming.py undo 2026-03-11#1" in text)
    check("report: 3 of 6 conflicts listed, both sides dated",
          "conflicts: 6" in text and "  3. A2 (2026-03-03, filename) vs B2 (2026-03-10, git first-add)" in text
          and "A3 (" not in text, text)
    check("report: the hard-external proposal first, routeless counted only",
          "proposals: 8, 1 without a route (counted only)" in text
          and plines[0].startswith("  1. ask owner: car-inspection") and "no route" not in "".join(plines), plines)
    check("report: carry line carries the LMS snapshot age",
          "pairs: 44, carried: 14, expired: 1, LMS snapshot 22 days old" in text)
    check("report: tonight's question and the missing root are named",
          "asked: dreaming-go-live" in text and "not found: ~/Documents/Transcripts" in text)
    check("report: no em-dash", "\u2014" not in text)

    # 2. The dry-run week: seven scheduled nights, then the go-live question, asked once.
    repo, home = tree2(tmp / "r1")
    snap = repo / "_meta" / "sync" / "2026-02-18-submission-state.tsv"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text("id\tstate\n")
    s0 = dreaming.surface_text("master", repo, MORNING)
    check("surface before any run: says there is no report yet", "no report yet" in s0, s0)
    q = repo / "_meta" / "health" / "questions" / "dreaming-go-live.json"
    heads = []
    for k in range(9):
        rc, out = srun(repo, home, MORNING + dt.timedelta(days=k, minutes=5))
        heads.append(out.splitlines()[0] if out else "")
        if k == 5:
            check("no question before the seventh night", not q.exists())
    print("    headers: " + " | ".join(heads))
    check("nights 1 to 7 counted in the header",
          heads[0] == "Dreaming Wed 11 Mar: dry run, night 1 of 7"
          and heads[6] == "Dreaming Tue 17 Mar: dry run, night 7 of 7", heads[:7])
    check("night 8 says when go-live was asked", heads[7] == "Dreaming Wed 18 Mar: dry run, night 8, go-live asked Tue 17 Mar",
          heads[7])
    body = json.loads(q.read_text()) if q.exists() else {}
    print("    go-live question: " + body.get("question", "(none)"))
    check("go-live question written on night 7 with the week's numbers",
          body.get("id") == "dreaming-go-live" and "Wed 11 Mar to Tue 17 Mar, 7 nights" in body.get("question", "")
          and "Judge over the week: 0 calls, $0.00" in body.get("context", ""), body)
    check("go-live: closed yes or no, default no, no on_answer",
          len(body.get("options", [])) == 2 and body.get("default", "").startswith("no")
          and "on_answer" not in body, body)
    sent = json.loads((repo / "_meta/health/dreams/state/questions-sent.json").read_text())
    check("go-live asked exactly once over nights 7 to 9", [s["id"] for s in sent] == ["dreaming-go-live"], sent)
    nights = json.loads((repo / "_meta/health/dreams/state/nights.json").read_text())
    check("nights.json holds nine nights with their counts",
          len(nights) == 9 and {"would_write", "conflicts", "proposals", "calls", "cost"} <= set(nights["2026-03-11"]),
          sorted(nights))
    latest = (repo / "_meta/health/dreams/latest.md").read_text()
    check("LMS snapshot age from _meta/sync", "LMS snapshot 30 days old" in latest, latest)

    # 3. The question cap and the never-twice rule.
    roots = dreaming.Roots(repo, home)
    night = dt.date(2026, 3, 24)
    st = repo / "_meta/health/dreams/state"
    (st / "questions-sent.json").write_text(json.dumps(
        [{"id": "dreaming-go-live", "night": "2026-03-19"}, {"id": "x1", "night": "2026-03-22"},
         {"id": "x2", "night": "2026-03-23"}, {"id": "x0", "night": "2026-03-14"}]))
    r = dreaming.ask(roots, "x3", {"question": "?"}, night)
    check("a fourth question in seven nights is refused", r.startswith("not asked: 3 questions this week"), r)
    r = dreaming.ask(roots, "x1", {"question": "?"}, dt.date(2026, 4, 13))
    check("a question id is never asked twice", r == "not asked: x1 was asked before", r)
    r = dreaming.ask(roots, "x4", {"question": "?"}, dt.date(2026, 4, 13))
    check("an old week does not count toward the cap",
          r.startswith("asked: x4") and (repo / "_meta/health/questions/x4.json").exists(), r)

    # 4. With writes on there is no dry-run count and no question.
    repo2, home2 = tree2(tmp / "r2")
    st2 = repo2 / "_meta/health/dreams/state"
    st2.mkdir(parents=True, exist_ok=True)
    (st2 / "writes-enabled").write_text("yes\n")
    rc, out = srun(repo2, home2, MORNING + dt.timedelta(minutes=5))
    check("writes enabled: the header says dry run with no night count (the job adds --write)",
          out.startswith("Dreaming Wed 11 Mar: dry run\n") and not (st2 / "nights.json").exists(), out[:80])

    # 5. The four surfaces, read from state/last.json only.
    m = dreaming.surface_text("master", repo, MORNING + dt.timedelta(days=9))
    e = dreaming.surface_text("email", repo, MORNING + dt.timedelta(days=9))
    ph = dreaming.surface_text("phone", repo, MORNING + dt.timedelta(days=9))
    pa = dreaming.surface_text("pane", repo, MORNING + dt.timedelta(days=9))
    print("    master: %s\n    email: %s\n    phone: %s\n    pane: %s" % (m, e, ph, pa))
    check("master line: night, label, counts, report path",
          m.startswith("Thu 19 Mar, dry run, night 9, go-live asked Tue 17 Mar: ") and "Full report: `_meta/health/dreams/latest.md`" in m, m)
    check("email clause: 'Dreaming (<night>): N changed, M conflicts'", re.match(r"^Dreaming \(\w{3} \d+ \w{3}\): \d+ changed, \d+ conflicts?, ", e), e)
    check("phone line: annotated and to check", re.match(r"^Dreaming: \d+ annotated, \d+ to check", ph), ph)
    stale = dreaming.surface_text("email", repo, MORNING + dt.timedelta(days=11))
    check("a report three days past its morning says how old it is", stale.endswith(", 3 days old.") and "1 day old" in m, stale)
    check("pane line: night, conflicts, proposals to tick", re.match(r"^\w{3} \d+ \w{3} · \d+ conflicts? · \d+ to tick", pa), pa)

    before = sorted(json.loads((repo / "_meta/health/dreams/state/nights.json").read_text()))
    srun(repo, home, MORNING + dt.timedelta(days=9, minutes=5), "--night", "2026-03-02")
    after = json.loads((repo / "_meta/health/dreams/state/nights.json").read_text())
    check("a --night replay is not counted as a dry-run night", sorted(after) == before)


def main() -> int:
    ap = argparse.ArgumentParser(description="dreaming.py selftest")
    ap.add_argument("--only", choices=PARTS + ("all",), default="all")
    ap.add_argument("--keep", action="store_true", help="keep the temp tree")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="dreaming-selftest-"))
    try:
        for part in PARTS:
            if args.only in (part, "all"):
                globals()["part_" + part](tmp)
    finally:
        if args.keep:
            print("kept " + str(tmp))
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    if FAILS:
        print("FAILED %d check(s)" % len(FAILS))
        return 1
    print("all dreaming checks passed (%s)" % args.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
