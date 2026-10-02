#!/usr/bin/env python3
"""dreaming.py - the overnight pass that consolidates the day's writing, the way sleep
consolidates memory. Stdlib only, Python 3.9 or later.

    python3 dreaming.py gather --night 2026-03-11 --json   # what the night read
    python3 dreaming.py run                                # dry run: gather, report
    python3 dreaming.py run --judge [--write]              # the scheduled job only, see MODES
    python3 dreaming.py status [--json]
    python3 dreaming.py candidates --night 2026-03-11 [--json]  # read only
    python3 dreaming.py run --judge-replay FILE [--write]       # recorded verdicts
    python3 dreaming.py undo 2026-03-12#3                       # one op back

How to set it up and read it is README.md. Here: the gatherer, the modes and the state root,
the candidate pairs, the rules, the proposals, the one writer and undo, the judge, and the
report, the dry-run week with its go-live question, and the one-line reader for dashboards.

WHERE IT READS. The notes root is `--root`, else $DREAMING_ROOT, else the current directory.
The home for the outside roots is `--home`, else $DREAMING_HOME, else ~. The layout under
both is described in README.md; a folder that does not exist is simply read as empty.

WHAT A NIGHT MAY CHANGE. Two frontmatter fields only, `related:` (a link, each side naming the
other) and `dup_candidate:` (on the newer of two open duplicates), as comma-separated scalars,
with the op ids in a sibling `dreaming_ops:`. Only real paths under obligations/ and
vault/prep/, never status, duplicate_of, done_at or stale, never a note edited by someone else
in the last 12 hours, at most 10 writes a night, compare and swap on sha256, one atomic write.
Everything else is a flag (contradictions, quoted both sides with `(<date>, <kind>)`) or a
proposal (close, stale, memory, bank) for the owner. `undo NIGHT#N` restores a field only if it
still holds what Dreaming wrote, and then freezes that rule for 7 days (state/freeze-<rule>).

THE NIGHT. `--night D` is the day D being consolidated: everything written from D 04:30 to
D+1 04:30, local time ($DREAMING_TZ, else the machine's zone), which is what the run at 04:30 on
D+1 would see. "Written" means a commit in that window touched the file (tracked files), or the
file's mtime falls in it, or, for session logs and dictations, the date or date-time in the
filename does. A mail counts on the night of its `received_utc`, or when captured in the window
up to three days late; older mail captured again is counted as `mail_recaptured` and not read.
For a past night the mtime of an untracked file that was edited again later has moved on, so a
replay of an old night undercounts untracked files; git history does not have that gap. `run`
without `--night` reads from the cursor (the start of the last run) to now, or the last 24 hours
on a first run.

THE ALLOW-LIST. Only these roots are read, and every file is checked again after
`os.path.realpath`, so a symlink cannot smuggle in anything else:
  root   obligations/*.md, vault/**/*.md (no .obsidian), reference/**/*.md,
         _meta/logs/session-*.md, _meta/session-ledger.md, _meta/agent-runs/_reports/*.md,
         _meta/agent-runs/*/progress.md, _inbox/candidate-dictation-*.md (read only),
         _sources/transcripts/lectures/*.txt, _sources/outlook/*.md (received only: a
         `direction: sent` capture is never evidence of anything)
  home   vault/raw/inbox/dictations/*.md, Documents/Transcripts/**/*.md (meeting
         transcripts; both folders configurable), and every Claude Code memory folder,
         ~/.claude/projects/*/memory
  never  *.log, agent-runs/*/log.md, state.json, any folder named in DREAMING_DENIED_NAMES
         (default career) under home or beside the root, _sources/files,
         _sources/media, anything whose real path lands outside the roots above
A file is keyed on (st_dev, st_ino) plus its sha256, so a hard link or a symlink seen twice is
read once. state/seen.json records each file's sha256, but v1 only counts the unchanged ones
for the report: `run` still reads the whole window.

MODES. The default is dry: no judge call and no write to any note. `--judge` (a live
`claude -p`) and `--write` (the note writer) are refused with exit 5 when DREAMING_HEADLESS,
or any marker listed in DREAMING_HEADLESS_VARS, is set, because an agent runner sets one for every
managed run and no automated build may make a live judge call or touch a real note. The one
exception is `--write` with `--root` inside the system temp dir, for selftests. `--judge-replay
FILE` (recorded verdicts) is never refused. launchd sets none of those variables, so the 04:30
job is the only thing that can run live. The state file `state/writes-enabled` is the owner's
switch: dreaming.sh passes `--write` only when it exists.

STATE. Everything Dreaming keeps is under `<root>/_meta/health/dreams/` (or `--state`, or
$DREAMING_STATE): `state/` for cursor, seen, ledger snapshot and the op log, and `<night>/` for
that night's files, plus `latest.md`. Files there are overwritten, never removed.

Exit codes: 0 ok, 2 bad arguments or no such op, 3 undo skipped, 4 late start,
5 refused: headless.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import atomicio  # noqa: E402  - the one atomic write
import frontmatter  # noqa: E402  - the frontmatter reader and the one frontmatter writer
import ledger  # noqa: E402  - the session ledger reader
import usage  # noqa: E402  - the Claude usage snapshot reader

LOCAL = usage.P                   # the zone a night is measured in, see usage.local_zone

REPO = HERE                       # this checkout: the tool itself, never the notes
HOME = Path(os.environ.get("DREAMING_HOME") or Path.home())
NIGHT_START = dt.time(4, 30)          # the run's slot: a night runs 04:30 to 04:30
# Set by an agent runner for every managed run; live calls and writes are refused under any of
# them. DREAMING_HEADLESS always counts; add your runner's own markers to DREAMING_HEADLESS_VARS
# (comma-separated), for example STEWARD_RUN_ID for the Steward runner.
HEADLESS_VARS = ("DREAMING_HEADLESS",) + tuple(
    v.strip() for v in os.environ.get("DREAMING_HEADLESS_VARS", "").split(",") if v.strip())
DENIED_NAMES = tuple(n.strip() for n in os.environ.get("DREAMING_DENIED_NAMES", "career").split(",")
                     if n.strip())    # folders never read or written, wherever they sit
EXIT_OK, EXIT_ARGS, EXIT_UNDO_SKIPPED, EXIT_LATE, EXIT_HEADLESS = 0, 2, 3, 4, 5

# Every input class, in the order the report counts them. README.md says what each can yield
# in v1.
CLASSES = ("obligations", "vault", "reference", "session", "ledger", "report", "progress",
           "dictation", "lecture", "meeting", "mail", "memory")
EXCLUDED_NAMES = ("log.md", "state.json")
EXCLUDED_SUFFIXES = (".log",)
SKIP_DIRS = {".obsidian", ".git", "node_modules", "__pycache__", ".trash"}
FM_LINES = 60                          # frontmatter is read from the first lines only
MAIL_LATE_DAYS = 3                     # a mail captured this late still counts, see gather
NAME_DT = re.compile(r"(\d{4}-\d{2}-\d{2})(?:-(\d{2})(\d{2}))?")


# ---------------------------------------------------------------------------------- roots

class Roots:
    """Where one run reads and keeps state. The configured notes root and home by default. The selftest
    passes a temp tree for both, so no real note is read or written by a test."""

    def __init__(self, root: Path | None = None, home: Path | None = None,
                 state: Path | None = None):
        self.root = Path(root or live_repo()).resolve()
        self.home = Path(home or HOME).resolve()
        # `--state DIR` (dreaming.sh --dry-run) keeps a rehearsal's cursor, seen list and report
        # away from the real night's, so a hand test never moves what 04:30 will read.
        env = os.environ.get("DREAMING_STATE")
        self.state = (Path(state).resolve() if state else Path(env).resolve() if env
                      else self.root / "_meta" / "health" / "dreams")
        self._allowed = self._denied = None      # real paths, resolved once per run

    def sources(self) -> list:
        """(class, base directory, glob pattern, recursive) for every allowed input."""
        r, h = self.root, self.home
        return [
            ("obligations", r / "obligations", "*.md", False),
            ("vault", r / "vault", "*.md", True),
            ("reference", r / "reference", "*.md", True),
            ("session", r / "_meta" / "logs", "session-*.md", False),
            ("ledger", r / "_meta", "session-ledger.md", False),
            ("report", r / "_meta" / "agent-runs" / "_reports", "*.md", False),
            ("progress", r / "_meta" / "agent-runs", "*/progress.md", False),
            ("dictation", r / "_inbox", "candidate-dictation-*.md", False),
            ("dictation", h / os.environ.get("DREAMING_DICTATIONS_DIR", "vault/raw/inbox/dictations"),
             "*.md", False),
            ("lecture", r / "_sources" / "transcripts" / "lectures", "*.txt", False),
            ("meeting", h / os.environ.get("DREAMING_MEETINGS_DIR", "Documents/Transcripts"),
             "*.md", True),
            ("mail", r / "_sources" / "outlook", "*.md", False),
        ] + [("memory", m, "*.md", False) for m in memory_dirs(h)]

    def allowed_bases(self) -> list:
        """Real paths a gathered file may resolve into (a symlinked memory folder is followed
        once here, so its target counts as the memory root, not as a stray path)."""
        if self._allowed is None:
            self._allowed = [Path(os.path.realpath(b)) for _c, b, _g, _r in self.sources()]
        return self._allowed

    def denied_bases(self) -> list:
        if self._denied is None:
            r, h = self.root, self.home
            names = [b for n in DENIED_NAMES for b in (h / n, r.parent / n)]
            self._denied = [Path(os.path.realpath(p)) for p in names + [
                r / "_sources" / "files", r / "_sources" / "media"]]
        return self._denied


def memory_dirs(home: Path) -> list:
    """Claude Code memory folders: $DREAMING_MEMORY_DIRS (paths separated by ':', relative to
    home or absolute), else every ~/.claude/projects/*/memory folder."""
    env = os.environ.get("DREAMING_MEMORY_DIRS")
    if env:
        return [home / d if not os.path.isabs(d) else Path(d) for d in env.split(":") if d]
    return sorted(p for p in (home / ".claude" / "projects").glob("*/memory") if p.is_dir())


def live_repo() -> Path:
    """The notes root when no --root is given: $DREAMING_ROOT, else the current directory."""
    return Path(os.environ.get("DREAMING_ROOT") or os.getcwd())


def _under(p: Path, base: Path) -> bool:
    """p is base or inside it. String prefixes on real paths: Path.relative_to was measured
    at over a third of a large gather."""
    p, base = str(p), str(base)
    return p == base or p.startswith(base.rstrip(os.sep) + os.sep)


def refusal(path: Path, roots: Roots) -> str:
    """Why a path may not be read, or "" when it may. Checked on the real path."""
    name = path.name
    if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES):
        return "excluded name"
    real = Path(os.path.realpath(path))
    if real.name in EXCLUDED_NAMES or real.name.endswith(EXCLUDED_SUFFIXES):
        return "excluded name"
    for d in roots.denied_bases():
        if _under(real, d):
            return "denied root " + _show(d, roots)
    hit = next((n for n in DENIED_NAMES if n in real.parts), "")
    if hit:
        return "denied root " + hit
    if not any(_under(real, b) for b in roots.allowed_bases()):
        return "outside the allow-list"
    if ".obsidian" in real.parts:
        return "excluded name"
    return ""


def _show(p: Path, roots: Roots) -> str:
    """Repo files as repo-relative paths, home files as ~/..., for reports and JSON."""
    p = Path(p)
    for base, pre in ((roots.root, ""), (roots.home, "~/")):
        try:
            return pre + str(p.relative_to(base))
        except ValueError:
            continue
    return str(p)


# ---------------------------------------------------------------------------------- night

def night_window(night: dt.date) -> tuple:
    start = dt.datetime.combine(night, NIGHT_START, tzinfo=LOCAL)
    return start, start + dt.timedelta(days=1)


def default_night(now: dt.datetime) -> dt.date:
    """The day a run at `now` consolidates: yesterday before noon, else today."""
    local = now.astimezone(LOCAL)
    return local.date() - dt.timedelta(days=1) if local.hour < 12 else local.date()


def _name_time(name: str) -> dt.datetime | None:
    """The date (and HHMM, if present) in a filename, as a local datetime."""
    m = NAME_DT.search(name)
    if not m:
        return None
    try:
        d = dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    if m.group(2):
        t = dt.time(int(m.group(2)) % 24, int(m.group(3)) % 60)
    else:
        t = dt.time(12, 0)            # a bare date means that day, whatever the hour
    return dt.datetime.combine(d, t, tzinfo=LOCAL)


def _git_lines(root: Path, *args) -> set | None:
    try:
        out = subprocess.run(["git", "-C", str(root)] + list(args),
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


def git_tracked(root: Path) -> tuple:
    """(tracked paths, paths modified against HEAD), both empty when root is not a repo. A
    tracked file counts by its commits, and by mtime only while it has uncommitted edits: a
    fresh checkout or a merge stamps every file it writes, which says nothing about the day."""
    tracked = _git_lines(root, "ls-files") or set()
    dirty = _git_lines(root, "diff", "--name-only", "HEAD") or set()
    return tracked, dirty


def _iso(s: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=LOCAL)


def git_changed(root: Path, start: dt.datetime, end: dt.datetime) -> set:
    """Repo-relative paths a commit in [start, end) touched. Empty when root is not a repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--since=" + start.isoformat(),
             "--until=" + end.isoformat(), "--name-only", "--format=", "--no-renames"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if out.returncode != 0:
        return set()
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


# ---------------------------------------------------------------------------------- files

def frontmatter_value(text: str, key: str) -> str:
    lines = text.splitlines()[:FM_LINES]
    if not lines or lines[0].strip() != "---":
        return ""
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = re.match(r"%s:\s*(.*)$" % re.escape(key), ln)
        if m:
            return m.group(1).strip().strip("\"'")
    return ""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _walk(base: Path, pattern: str, recursive: bool) -> list:
    if not base.is_dir():
        return []
    if not recursive:
        return sorted(p for p in base.glob(pattern) if p.is_file())
    found = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if p.match(pattern) and p.is_file():
                found.append(p)
    return found


def gather(roots: Roots, start: dt.datetime, end: dt.datetime) -> dict:
    """Every allowed file written in [start, end), each once, with why it counted."""
    changed = git_changed(roots.root, start, end)
    tracked, dirty = git_tracked(roots.root)
    t0, t1 = start.timestamp(), end.timestamp()
    items, refused, missing = [], [], []
    seen_real, seen_key = set(), set()
    counts = {c: 0 for c in CLASSES}
    sent_skipped = recaptured = 0
    for cls, base, pattern, recursive in roots.sources():
        if not base.is_dir():
            if cls in ("meeting", "dictation", "memory"):
                missing.append(_show(base, roots))
            continue
        for p in _walk(base, pattern, recursive):
            why = refusal(p, roots)
            if why:
                refused.append({"path": _show(p, roots), "reason": why})
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            rel = _show(p, roots)
            how = []
            if rel in changed:
                how.append("git")
            if t0 <= st.st_mtime < t1 and (rel not in tracked or rel in dirty):
                how.append("mtime")
            if cls == "session":
                nt = _name_time(p.name)
                if nt is not None and nt.date() == start.date():
                    how.append("name")
            elif cls == "dictation":
                # A dictation's name carries when it was spoken, which is the fact: a file
                # rewritten later (or a test file named for another date) is not that day's.
                nt = _name_time(p.name)
                if nt is not None:
                    how = ["name"] if start <= nt < end else []
            elif cls == "mail":
                # A mail belongs to the night it arrived, whenever it was captured. Old mail
                # captured again (a folder re-synced, say) is a capture, not the day's writing. A mail captured in the window that arrived up to
                # MAIL_LATE_DAYS before it is a late capture, read so a live run misses nothing.
                try:
                    with open(p, "rb") as fh:
                        head = fh.read(4000).decode("utf-8", "replace")
                except OSError:
                    continue
                rt = _iso(frontmatter_value(head, "received_utc"))
                if frontmatter_value(head, "direction").lower() == "sent":
                    # Never evidence of anything: counted as skipped, never read.
                    if how or (rt is not None and start <= rt < end):
                        sent_skipped += 1
                    continue
                if rt is not None:
                    if start <= rt < end:
                        how = ["received"]
                    elif how and start - dt.timedelta(days=MAIL_LATE_DAYS) <= rt < start:
                        how = ["late capture"]
                    elif how:
                        recaptured += 1
                        how = []
            if not how:
                continue
            real = os.path.realpath(p)
            key = "%d:%d" % (st.st_dev, st.st_ino)
            if real in seen_real or key in seen_key:
                continue
            data = p.read_bytes()
            seen_real.add(real)
            seen_key.add(key)
            counts[cls] += 1
            items.append({"path": rel, "class": cls, "size": len(data), "sha256": sha256(data),
                          "key": key, "mtime": int(st.st_mtime), "how": how})
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "root": str(roots.root),
        "files": len(items),
        "bytes": sum(i["size"] for i in items),
        "classes": counts,
        "paths": [i["path"] for i in items],
        "items": items,
        "refused": refused,
        "sent_skipped": sent_skipped,
        "mail_recaptured": recaptured,
        "missing_roots": missing,
        "ledger_sids": ledger_changes(roots, start, end),
    }


def _git_text_at(root: Path, rel: str, when: dt.datetime) -> str | None:
    """A tracked file's text at the last commit before `when`, or None."""
    try:
        rev = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%H",
                              "--before=" + when.isoformat(), "--", rel],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        if not rev:
            return None
        out = subprocess.run(["git", "-C", str(root), "show", "%s:%s" % (rev, rel)],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def ledger_entries(text: str) -> dict:
    """sid -> sha256 of the entry, parsed by the ledger reader."""
    return {e["sid"]: sha256(e["text"].encode("utf-8")) for e in ledger.load_entries(text)}


def ledger_changes(roots: Roots, start: dt.datetime, end: dt.datetime,
                   before: dict | None = None) -> list:
    """Ledger entries that are new or rewritten in the window. Entries are rewritten in place,
    so the file's mtime says nothing: this diffs entry by entry. The baseline is the
    ledger at the window's start from git, else `before` (the state snapshot), else nothing
    (every entry is new)."""
    rel = "_meta/session-ledger.md"
    path = roots.root / rel
    now = dt.datetime.now(LOCAL)
    if end < now - dt.timedelta(minutes=5):
        after_text = _git_text_at(roots.root, rel, end)
    else:
        after_text = None
    if after_text is None:
        try:
            after_text = path.read_text(encoding="utf-8")
        except OSError:
            return []
    try:
        after = ledger_entries(after_text)
        if before is None:
            base_text = _git_text_at(roots.root, rel, start)
            before = ledger_entries(base_text) if base_text is not None else {}
    except Exception:
        return []
    return sorted(sid for sid, h in after.items() if before.get(sid) != h)


# ----------------------------------------------------------------------------- candidates
#
# Pairs are found by title tokens, never by embeddings, and only
# day notes of the classes that can yield a link, a mark or a flag are paired: obligations,
# vault notes, reference and received mail. The other day classes are "read, no output in v1",
# apart from dictations and memory, which feed proposals.

PAIR_CLASSES = ("obligations", "vault", "reference", "mail")
JUDGE_CAP = 30                         # judge calls a night
PAIR_BYTES = 60 * 1024                 # two files over this are not judged: read whole or not at all
CARRY_NIGHTS = 7                       # a carried pair older than this is dropped and counted
DF_CAP = 10                            # a token in this many corpus titles says nothing
HEAD_BYTES = 6000                      # enough for any frontmatter and first heading
DUE_SLACK_S = 60                       # two instants this close are the same deadline
VERDICTS = ("related", "duplicate", "contradiction", "none")
VERDICT_KEYS = {"a", "b", "verdict", "quote_a", "quote_b", "why"}
CLOSED = {"done", "not-applicable", "stale", "expired"}
STOP = set("""
about after again against also among another before being below between both cannot could
does doing down during each even every from further have having here into just like made make
many more most much must need needs next only other over same should some such than that
their them then there these they this those through under until very want were what when where
which while will with within without would your yours prep note notes draft final page
""".split())
DATE_IN_TITLE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[-T ]?\d{2}:?\d{2})?\b")


def fmv(fm: dict, key: str) -> str:
    v = fm.get(key, "")
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v or "").strip()


def title_of(text: str, fm: dict, stem: str) -> str:
    t = fmv(fm, "title")
    if t:
        return t
    for ln in text.splitlines()[:80]:
        if ln.startswith("# "):
            return ln[2:].strip()
    return stem


def title_tokens(title: str) -> set:
    t = DATE_IN_TITLE.sub(" ", title.lower())
    return {w for w in re.findall(r"[a-z0-9]+", t)
            if len(w) >= 4 and w not in STOP and not re.fullmatch(r"(19|20)\d\d", w)}


def namespace_of(rel: str) -> str:
    """The filename prefix for an obligation, with `co-<org>`, `todo-<x>` and `event-<x>` as
    two parts; the course folder for vault/prep; the first folder elsewhere in the vault."""
    parts = rel.split("/")
    if parts[0] in ("obligations", "reference"):
        bits = DATE_IN_TITLE.sub("", Path(parts[-1]).stem).strip("-").split("-")
        if bits[0] in ("co", "todo", "event") and len(bits) > 1:
            return bits[0] + "-" + bits[1]
        return bits[0]
    if parts[0] == "vault":
        if len(parts) > 3 and parts[1] == "prep":
            return parts[2]
        return parts[1] if len(parts) > 2 else "vault"
    return parts[0]


def read_head(p: Path) -> str:
    try:
        with open(p, "rb") as fh:
            return fh.read(HEAD_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""


def read_text(p: Path) -> str:
    try:
        return p.read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def parse_fm(text: str) -> dict:
    try:
        return frontmatter.frontmatter_text(text) or {}
    except Exception:
        return {}


def load_note(roots: Roots, rel: str, cls: str, whole: bool = False) -> dict:
    p = (roots.home / rel[2:]) if rel.startswith("~/") else roots.root / rel
    text = read_text(p) if whole else read_head(p)
    fm = parse_fm(text)
    stem = p.stem
    title = title_of(text, fm, stem)
    return {"rel": rel, "path": p, "cls": cls, "text": text, "whole": whole, "fm": fm,
            "stem": stem, "title": title, "ns": namespace_of(rel),
            "tokens": title_tokens(title), "status": fmv(fm, "status").lower(),
            "size": p.stat().st_size if p.exists() else 0}


def corpus(roots: Roots) -> dict:
    """rel -> note head, for obligations/*.md and vault/prep/**/*.md, the notes a day note is
    paired against."""
    out = {}
    for cls, base, pattern, rec in (("obligations", roots.root / "obligations", "*.md", False),
                                    ("vault", roots.root / "vault" / "prep", "*.md", True)):
        for p in _walk(base, pattern, rec):
            if refusal(p, roots):
                continue
            rel = _show(p, roots)
            out[rel] = load_note(roots, rel, cls)
    return out


def mentions(stem: str, text: str) -> bool:
    """`stem` appears as a whole name, so quiz-1 does not match quiz-10."""
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(stem), text) is not None


def names_other(n: dict, other: dict, body: bool = False) -> bool:
    """n already points at `other` in a Dreaming or owner field (or, with body, anywhere)."""
    for k in ("related", "dup_candidate", "duplicate_of", "supersedes"):
        if other["stem"] in [x.strip() for x in fmv(n["fm"], k).split(",")]:
            return True
    return body and mentions(other["stem"], n["text"])


def pair_key(a: str, b: str) -> str:
    return " | ".join(sorted((a, b)))


def is_closed(n: dict) -> bool:
    return n["status"] in CLOSED or fmv(n["fm"], "stale").lower() == "true"


def make_pairs(day: list, corp: dict) -> tuple:
    """(pairs, skipped counts). A pair fires on the same namespace plus 2 shared tokens, or on
    2 shared tokens with a Jaccard of 0.5 or more."""
    df = {}
    for n in corp.values():
        for t in n["tokens"]:
            df[t] = df.get(t, 0) + 1
    common = {t for t, c in df.items() if c >= DF_CAP}
    pool = dict(corp)
    for n in day:
        pool.setdefault(n["rel"], n)
    skipped = {"same folder": 0, "already linked": 0, "closed against the other": 0,
               "both closed": 0}
    pairs, seen = [], set()
    for d in day:
        dt_ = d["tokens"] - common
        if len(dt_) < 2:
            continue
        for c in pool.values():
            if c["rel"] == d["rel"]:
                continue
            key = pair_key(d["rel"], c["rel"])
            if key in seen:
                continue
            shared = dt_ & (c["tokens"] - common)
            if len(shared) < 2:
                continue
            union = dt_ | (c["tokens"] - common)
            jac = len(shared) / float(len(union))
            if not (d["ns"] == c["ns"] or jac >= 0.5):
                continue
            seen.add(key)
            if Path(d["rel"]).parent == Path(c["rel"]).parent and d["cls"] != "obligations":
                skipped["same folder"] += 1          # a folder already links its files
                continue
            if names_other(d, c) or names_other(c, d):
                skipped["already linked"] += 1
                continue
            if (is_closed(d) and names_other(d, c, body=True)) or \
                    (is_closed(c) and names_other(c, d, body=True)):
                skipped["closed against the other"] += 1
                continue
            if is_closed(d) and is_closed(c):
                skipped["both closed"] += 1
                continue
            a, b = sorted((d, c), key=lambda n: n["rel"])
            hard = any(fmv(n["fm"], "class") == "hard-external" for n in (a, b))
            pairs.append({"a": a["rel"], "b": b["rel"], "shared": sorted(shared),
                          "jaccard": round(jac, 2), "hard": hard,
                          "bytes": a["size"] + b["size"]})
    pairs.sort(key=lambda p: (not p["hard"], -len(p["shared"]), p["a"], p["b"]))
    return pairs, skipped


def day_notes(roots: Roots, g: dict) -> list:
    return [load_note(roots, i["path"], i["class"]) for i in g["items"]
            if i["class"] in PAIR_CLASSES]


def candidates(roots: Roots, night: dt.date, g: dict | None = None, cap: int = JUDGE_CAP) -> dict:
    """Read-only: the night's pairs, which of them the judge would see, the deterministic
    duplicates and contradictions."""
    start, end = night_window(night)
    if g is None:
        g = gather(roots, start, end)
    corp = corpus(roots)
    day = day_notes(roots, g)
    pairs, skipped = make_pairs(day, corp)
    judged, carry, large = [], [], 0
    for p in pairs:
        if p["bytes"] > PAIR_BYTES:
            large += 1
            p["route"] = "too large"
        elif len(judged) < cap:
            judged.append(p)
            p["route"] = "judge"
        else:
            carry.append(p)
            p["route"] = "carry"
    dayset = {n["rel"] for n in day}
    dups = det_duplicates(corp, dayset)
    conflicts = det_contradictions(roots, corp, dayset)
    return {"night": night.isoformat(), "pairs": pairs, "judged": len(judged),
            "carried": len(carry), "too_large": large, "skipped": skipped,
            "dups": dups, "contradictions": conflicts, "day_notes": len(day),
            "corpus": len(corp), "window": g["window"]}


# ---------------------------------------------------------------------- deterministic rules

ISO_DT = re.compile(r"\b(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::\d{2})?(Z|[+-]\d{2}:?\d{2})?")
HUMAN_DT = re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|"
                      r"Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(?:(\d{4})\s+)?(?:at\s+)?(\d{1,2}):(\d{2})"
                      r"\s*(CEST|CET|BST|GMT|UTC|SGT)?")
HUMAN_D = re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*"
                     r"(?:\s+(\d{4}))?\b")
MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
TZ_NAMES = {"CEST": 2, "CET": 1, "BST": 1, "GMT": 0, "UTC": 0, "SGT": 8}


def instants_in(line: str, year: int) -> list:
    """Every date-time with a time of day in a line, as UTC datetimes. A time with no zone is
    local time ($DREAMING_TZ)."""
    out = []
    for m in ISO_DT.finditer(line):
        z = m.group(4)
        s = "%sT%s:%s" % (m.group(1), m.group(2), m.group(3))
        t = _iso(s + (("+00:00" if z == "Z" else z[:3] + ":" + z[-2:]) if z else ""))
        if t is not None:
            out.append(t.astimezone(dt.timezone.utc))
    for m in HUMAN_DT.finditer(line):
        try:
            y = int(m.group(3) or year)
            local = dt.datetime(y, MONTHS[m.group(2)[:3]], int(m.group(1)), int(m.group(4)),
                                int(m.group(5)))
        except ValueError:
            continue
        tz = m.group(6)
        tzinfo = dt.timezone(dt.timedelta(hours=TZ_NAMES[tz])) if tz else LOCAL
        out.append(local.replace(tzinfo=tzinfo).astimezone(dt.timezone.utc))
    return out


def fm_line(text: str, key: str) -> str:
    for ln in text.splitlines()[:FM_LINES]:
        if ln.startswith(key + ":"):
            return ln.strip()
    return ""


def det_duplicates(corp: dict, dayset: set) -> list:
    """Open obligations that are the same item: same assignment_id, or same event_uid,
    or due_utc within 60 s with the same normalised title and namespace. At least one side
    must be a day note. A side already closed or marked is left alone."""
    obl = [n for n in corp.values() if n["cls"] == "obligations"]
    out, seen = [], set()
    for i, a in enumerate(obl):
        for b in obl[i + 1:]:
            if a["rel"] not in dayset and b["rel"] not in dayset:
                continue
            why = ""
            for k in ("assignment_id", "event_uid"):
                va, vb = fmv(a["fm"], k), fmv(b["fm"], k)
                if va and va == vb:
                    why = "same %s %s" % (k, va)
                    break
            if not why and a["ns"] == b["ns"] and a["tokens"] and a["tokens"] == b["tokens"]:
                ta, tb = _iso(fmv(a["fm"], "due_utc")), _iso(fmv(b["fm"], "due_utc"))
                if ta and tb and abs((ta - tb).total_seconds()) <= DUE_SLACK_S:
                    why = "same title and due_utc"
            if not why:
                continue
            key = pair_key(a["rel"], b["rel"])
            if key in seen:
                continue
            seen.add(key)
            if is_closed(a) or is_closed(b) or names_other(a, b) or names_other(b, a):
                continue
            out.append({"a": a["rel"], "b": b["rel"], "why": why})
    return out


def det_contradictions(roots: Roots, corp: dict, dayset: set) -> list:
    """Dates that disagree, found without a judge:
    two open obligations with the same assignment_id or event_uid and different due_utc, and
    an obligation against a vault/prep or reference note naming the same assignment with a
    different time (a note says one time, the calendar feed another).
    At least one side must be a day note. Contradictions are flags only."""
    obl = [n for n in corp.values() if n["cls"] == "obligations" and not is_closed(n)]
    out = []
    for i, a in enumerate(obl):
        for b in obl[i + 1:]:
            if a["rel"] not in dayset and b["rel"] not in dayset:
                continue
            for k in ("assignment_id", "event_uid"):
                va = fmv(a["fm"], k)
                if not va or va != fmv(b["fm"], k):
                    continue
                ta, tb = _iso(fmv(a["fm"], "due_utc")), _iso(fmv(b["fm"], "due_utc"))
                if ta and tb and abs((ta - tb).total_seconds()) > DUE_SLACK_S:
                    out.append(_conflict(roots, a, b, fm_line(a["text"], "due_utc"),
                                         fm_line(b["text"], "due_utc"),
                                         "same %s, different due_utc" % k))
                break
    # obligation against notes
    notes = [n for n in corp.values() if n["cls"] == "vault"]
    ref_base = roots.root / "reference"
    for p in _walk(ref_base, "*.md", True):
        rel = _show(p, roots)
        if rel in dayset:
            notes.append(load_note(roots, rel, "reference"))
    for o in obl:
        due = _iso(fmv(o["fm"], "due_utc"))
        aid = fmv(o["fm"], "assignment_id")
        if due is None:
            continue
        for n in notes:
            if o["rel"] not in dayset and n["rel"] not in dayset:
                continue
            by_id = aid and fmv(n["fm"], "assignment_id") == aid
            if not by_id and not mentions(o["stem"], n["text"]):
                continue
            if not n["whole"]:
                n.update(load_note(roots, n["rel"], n["cls"], whole=True))
            hit = _disagreeing_line(n, o, due, by_id)
            if hit:
                out.append(_conflict(roots, o, n, fm_line(o["text"], "due_utc"), hit,
                                     "note gives a different time for the same assignment"))
    return out


def _disagreeing_line(n: dict, o: dict, due: dt.datetime, by_id: bool) -> str:
    """The first body line of n about o's deadline whose time differs from due by over 60 s."""
    lines = n["text"].splitlines()
    body_from = 0
    if lines and lines[0].strip() == "---":
        for j, ln in enumerate(lines[1:], 1):
            if ln.strip() == "---":
                body_from = j + 1
                break
    for ln in lines[body_from:]:
        about = (o["stem"] in ln) or (by_id and re.search(r"\bdue\b", ln, re.I))
        if not about:
            continue
        ts = instants_in(ln, due.year)
        if ts and all(abs((t - due).total_seconds()) > DUE_SLACK_S for t in ts):
            return ln.strip()
    return ""


def _conflict(roots: Roots, a: dict, b: dict, qa: str, qb: str, why: str) -> dict:
    return {"a": a["rel"], "b": b["rel"], "quote_a": qa, "quote_b": qb, "why": why,
            "date_a": date_of(roots, a["rel"], qa), "date_b": date_of(roots, b["rel"], qb),
            "title_a": a["title"], "title_b": b["title"]}


def date_of(roots: Roots, rel: str, quote: str = "", text: str | None = None) -> list:
    """[date, kind] for one side of a conflict, in order: a date in the quoted line, or
    in the nearest heading above it; received_utc for mail; the date in the filename; git
    first-add; else undated. The report prints `(<date>, <kind>)` per side."""
    p = (roots.home / rel[2:]) if rel.startswith("~/") else roots.root / rel
    if text is None:
        text = read_text(p)
    year = dt.date.today().year
    y = NAME_DT.search(Path(rel).name)
    if y:
        year = int(y.group(1)[:4])

    def a_date(s: str):
        m = re.search(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", s)
        if m:
            return m.group(1)
        m = HUMAN_D.search(s)
        if m:
            try:
                return dt.date(int(m.group(3) or year), MONTHS[m.group(2)[:3]],
                               int(m.group(1))).isoformat()
            except ValueError:
                return None
        return None

    fm_block = text.split("\n---", 1)[0] if text.startswith("---\n") else ""
    fm_quote = bool(quote and re.match(r"[A-Za-z_]+:\s", quote)) and \
        any(ln.strip() == quote.strip() for ln in fm_block.splitlines())
    if quote and fm_quote and not rel.startswith("~/"):
        # a frontmatter value is a fact, not a dated statement: date it by the commit that
        # added this exact line (the day the value was set), when git has it
        out = _git_lines(roots.root, "log", "-S" + quote.strip(), "--format=%ad", "--date=short",
                         "--", rel)
        if out:
            return [sorted(out)[-1], "git line-add"]
        if y is None:
            try:                        # the value is not committed yet: say so
                m = dt.datetime.fromtimestamp(p.stat().st_mtime, LOCAL).date().isoformat()
                return [m, "uncommitted, mtime"]
            except OSError:
                pass
    if quote and not fm_quote:
        d = a_date(quote)
        if d:
            return [d, "quoted line"]
        lines = text.splitlines()
        at = next((i for i, ln in enumerate(lines) if ln.strip() == quote.strip()), None)
        if at is not None:
            for ln in reversed(lines[:at]):
                if ln.startswith("#"):
                    d = a_date(ln)
                    if d:
                        return [d, "dated heading"]
                    break
    rt = _iso(frontmatter_value(text, "received_utc"))
    if rt is not None:
        return [rt.astimezone(LOCAL).date().isoformat(), "received"]
    if y:
        return [y.group(1), "filename"]
    if not rel.startswith("~/"):
        out = _git_lines(roots.root, "log", "--diff-filter=A", "--format=%ad", "--date=short",
                         "--", rel)
        if out:
            return [sorted(out)[0], "git first-add"]
    return ["undated", "undated"]


# ---------------------------------------------------------------------- verdicts (judge)

def whole_line(text: str, quote) -> bool:
    """The quote is one whole line of the file, stripped (quotes are re-checked)."""
    if not isinstance(quote, str) or not quote.strip():
        return False
    q = quote.strip()
    if not any(c.isalnum() for c in q):
        return False  # a rule such as `---` is a whole line of every note and proves nothing
    return any(ln.strip() == q for ln in text.splitlines())


def check_verdict(v, pairs: dict, notes: dict) -> tuple:
    """(verdict, "") when it may be used, else (None, why). The judge's output is data: only
    the enum and two quotes that are whole lines of their files survive. A verdict whose
    pair is not tonight's, or that carries any other key, is discarded."""
    if not isinstance(v, dict):
        return None, "not an object"
    extra = set(v) - VERDICT_KEYS
    if extra:
        return None, "unexpected keys %s" % ",".join(sorted(extra))
    if v.get("verdict") not in VERDICTS:
        return None, "verdict %r not in the enum" % (v.get("verdict"),)
    key = pair_key(str(v.get("a", "")), str(v.get("b", "")))
    if key not in pairs:
        return None, "not a candidate pair"
    if v["verdict"] == "none":
        return v, ""
    for side, q in (("a", "quote_a"), ("b", "quote_b")):
        n = notes[v[side]]
        if frontmatter_value(n["text"], "direction").lower() == "sent":
            return None, "sent mail is never evidence"
        if not whole_line(n["text"], v.get(q)):
            return None, "%s is not a whole line of %s" % (q, v[side])
    return v, ""


# ---------------------------------------------------------------------- privacy

COURSEWORK = ("vault/prep/", "vault/exams/", "vault/revision/")


def private_side(rel: str) -> bool:
    name = Path(rel).name
    return (rel.startswith(("obligations/co-", "obligations/life-", "obligations/todo-job-",
                            "_sources/outlook/", "~/.claude/"))
            or any("/%s/" % n in "/" + rel or name.startswith(n) for n in DENIED_NAMES))


def coursework_side(rel: str) -> bool:
    return rel.startswith(COURSEWORK)


def privacy_refusal(a: str, b: str) -> str:
    if (private_side(a) and coursework_side(b)) or (private_side(b) and coursework_side(a)):
        return "privacy wall: private side with coursework side"
    return ""


def private_terms() -> list:
    """Lower-case terms that must never be written into a note's frontmatter: one per line in
    the file $DREAMING_PRIVATE_TERMS (# comments allowed). None set means no terms."""
    path = os.environ.get("DREAMING_PRIVATE_TERMS")
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ["\x00unreadable"]      # fail closed: an unreadable list blocks every write
    return [ln.strip().lower() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def leaks(text: str) -> bool:
    """True when the text names a private term (the privacy wall on written values)."""
    terms = private_terms()
    if terms == ["\x00unreadable"]:
        return True
    low = text.lower()
    return any(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(term), low) for term in terms)


# ---------------------------------------------------------------------- proposals

REOPEN_TEXT = re.compile(r"^(## Reopened\b|- Reopened )", re.M)
MONEY_APP = re.compile(r"\b(money|pay|payment|paid|fee|fees|refund|deposit|invoice|rent|"
                       r"application|apply|applied)\b|[£€$]", re.I)
ONCE = re.compile(r"\b(trip|flight|fly|train|travel|visit|dinner|lunch|drinks|party|"
                  r"event|meeting|call|appointment|concert|match|ceremony)\b", re.I)
RULE_WORDS = re.compile(r"\b(from now on|always|never|remember that|i prefer|don't ever|"
                        r"do not ever)\b", re.I)


def reopened(n: dict, snapshot: dict) -> bool:
    """A reopened item: the transition diff first (closed last night, open now), then the three text forms."""
    was = snapshot.get(n["rel"], "")
    if was in CLOSED and n["status"] not in CLOSED:
        return True
    return bool(REOPEN_TEXT.search(n["text"]) or fmv(n["fm"], "reopened"))


def triage_owned(roots: Roots, end: dt.datetime) -> list:
    """(namespace, title tokens) for every mail candidate whose triage verdict edits an
    existing obligation and is not applied yet, or was judged in the last 48 h. Those
    obligations are triage's to close: Dreaming proposes nothing for them."""
    out = []
    for p in _walk(roots.root / "_inbox", "candidate-*.md", False):
        head = read_head(p)
        verdict = frontmatter_value(head, "verdict")
        if verdict not in ("close-existing", "update-existing"):
            continue
        status = frontmatter_value(head, "status")
        judged = _iso(frontmatter_value(head, "judged_at"))
        applied = status.startswith("triaged-") or status in ("promoted", "done", "applied")
        recent = judged is not None and end - dt.timedelta(hours=48) <= judged <= end
        if applied and not recent:
            continue
        out.append((frontmatter_value(head, "judge_namespace"),
                    title_tokens(frontmatter_value(head, "title"))))
    return out


def due_of(n: dict) -> dt.datetime | None:
    t = _iso(fmv(n["fm"], "due_utc"))
    if t is not None:
        return t
    d = fmv(n["fm"], "due")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return dt.datetime.combine(dt.date.fromisoformat(d), dt.time(23, 59), tzinfo=LOCAL)
    return None


def close_proposals(roots: Roots, start: dt.datetime, end: dt.datetime, snapshot: dict,
                    counts: dict) -> list:
    """Open obligations whose date passed during the night, after the owners
    in the ownership table have been left their share."""
    triage = triage_owned(roots, end)
    out = []
    for p in _walk(roots.root / "obligations", "*.md", False):
        rel = _show(p, roots)
        n = load_note(roots, rel, "obligations", whole=True)
        if n["status"] not in ("open", "unanswered", ""):
            continue
        due = due_of(n)
        if due is None or not (start <= due < end):
            continue
        if fmv(n["fm"], "assignment_id") or fmv(n["fm"], "source") in ("lms", "ics"):
            counts["owner: lms"] = counts.get("owner: lms", 0) + 1
            continue
        if n["stem"].startswith("event-") or fmv(n["fm"], "event_uid"):
            counts["owner: event clock"] = counts.get("owner: event clock", 0) + 1
            continue
        # a namespace alone is too wide ("life" names dozens), so it needs one shared word too
        if any(len(tok & n["tokens"]) >= 2 or (ns and ns in (n["ns"], fmv(n["fm"], "namespace"))
                                                and tok & n["tokens"]) for ns, tok in triage):
            counts["owner: triage"] = counts.get("owner: triage", 0) + 1
            continue
        if reopened(n, snapshot):
            counts["reopened"] = counts.get("reopened", 0) + 1
            continue
        when = due.astimezone(LOCAL).strftime("%a %-d %b %H:%M")
        hard = fmv(n["fm"], "class") == "hard-external"
        ask = hard or n["ns"].startswith("co-") or bool(
            MONEY_APP.search(n["title"] + " " + fmv(n["fm"], "category")))
        if ask:
            text = "ask owner: %s passed its date (%s), is it finished? -> tick done on the site" % (
                n["stem"], when)
        elif ONCE.search(n["title"]):
            text = 'close %s "looks finished, date passed" (%s) -> tick done on the site' % (
                n["stem"], when)
        else:
            counts["date passed, not a one-off"] = counts.get("date passed, not a one-off", 0) + 1
            continue
        out.append({"kind": "ask" if ask else "close", "target": rel, "text": text,
                    "hard": hard, "op": "close"})
    return out


def stale_proposals(roots: Roots, day: list) -> list:
    """Stale proposals, keyed only on `supersedes:` on the newer note. A proposal, never a write, and the
    `-STALE` rename is for the owner's interactive session."""
    out = []
    for n in day:
        text = n["text"] if n["whole"] else read_text(n["path"])
        line = fm_line(text, "supersedes")
        if not line:
            continue
        m = re.match(r"supersedes:\s*[\"']?([^\s\"'(]+)", line)
        if not m:
            continue
        name = m.group(1)
        cand = [n["path"].parent / name, n["path"].parent / (name + ".md"),
                roots.root / "obligations" / name, roots.root / "obligations" / (name + ".md")]
        old = next((c for c in cand if c.is_file()), None)
        if old is None:
            continue
        orel = _show(old, roots)
        ofm = parse_fm(read_head(old))
        if fmv(ofm, "stale").lower() == "true" or fmv(ofm, "status") == "stale":
            continue
        lno = next(i for i, ln in enumerate(text.splitlines(), 1) if ln.strip() == line)
        out.append({"kind": "stale", "target": orel, "hard": False, "op": "stale",
                    "text": 'stale %s: %s:%d "%s" -> set stale: true by hand'
                            % (orel, Path(n["rel"]).name, lno, _clip(line, 90))})
    return out


def memory_proposals(roots: Roots, items: list) -> list:
    """Memory proposals: only the owner's own spoken words, from the verbatim block of a dictation, that state a
    standing rule, and only when no memory file already shares two of its words."""
    mem_words = []
    for _c, base, pat, _r in roots.sources():
        if _c == "memory":
            for p in _walk(base, pat, False):
                mem_words.append(title_tokens(p.stem.replace("-", " ") + " " +
                                              frontmatter_value(read_head(p), "description")))
    out = []
    for i in items:
        if i["class"] != "dictation" or not i["path"].startswith("~/"):
            continue
        text = read_text(roots.home / i["path"][2:])
        said = re.split(r"^## What was said, verbatim\s*$", text, flags=re.M)
        if len(said) < 2:
            continue
        for ln in said[1].splitlines():
            if not ln.startswith(">"):
                if ln.startswith("## "):
                    break
                continue
            q = ln.lstrip("> ").strip()
            if not RULE_WORDS.search(q):
                continue
            words = title_tokens(q)
            if any(len(words & mw) >= 2 for mw in mem_words):
                continue
            out.append({"kind": "memory", "target": i["path"], "hard": False, "op": "memory",
                        "text": 'memory: %s "%s" -> add it in a session (memory rules)'
                                % (Path(i["path"]).name, _clip(q, 100))})
    return out


def bank_ask(title: str) -> tuple:
    """(live, page titles) from a knowledge bank, read only. $DREAMING_BANK_CMD is a command
    that takes the title as its last argument and prints JSON, {"pages": [{"title": ...}]}.
    With none set the bank is "not live" and no bank proposal is made. DREAMING_BANK_REPLAY
    (a JSON file {title: [page titles]}) stands in for the bank in the selftest."""
    replay = os.environ.get("DREAMING_BANK_REPLAY")
    if replay:
        data = read_json(Path(replay), {})
        return True, list(data.get(title, []))
    cmd = os.environ.get("DREAMING_BANK_CMD")
    if not cmd:
        return False, []
    try:
        import shlex
        out = subprocess.run(shlex.split(cmd) + [title], capture_output=True, text=True, timeout=60)
        res = json.loads(out.stdout) if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False, []
    if not isinstance(res, dict):
        return False, []
    return True, [p.get("title", "") for p in res.get("pages", []) if isinstance(p, dict)]


def bank_proposals(roots: Roots, items: list, counts: dict) -> list:
    """A day note (memory, or a vault note outside vault/prep) with no bank page about its
    subject: no returned page shares two title words with it. "About" is decided on the
    titles. Proposal only."""
    out, seen = [], set()
    for i in items:
        rel = i["path"]
        if i["class"] == "memory":
            if Path(rel).name == "MEMORY.md":
                continue
        elif not (i["class"] == "vault" and not rel.startswith("vault/prep/")):
            continue
        n = load_note(roots, rel, i["class"])
        if n["stem"] in seen or len(n["tokens"]) < 2:
            continue
        seen.add(n["stem"])
        live, titles = bank_ask(n["title"])
        if not live:
            counts["bank not live"] = counts.get("bank not live", 0) + 1
            continue
        if any(len(title_tokens(t) & n["tokens"]) >= 2 for t in titles):
            continue
        out.append({"kind": "bank", "target": rel, "hard": False, "op": "bank",
                    "text": "bank: %s has no bank page -> add one to the bank" % n["stem"]})
    return out


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 3] + "..."


# ---------------------------------------------------------------------- rules to ops

def rule_ops(verdicts: list, notes: dict, dups: list) -> tuple:
    """(ops, conflicts, refused) from checked verdicts and deterministic duplicates. An op is
    one field on one file: `related` for a link (each writable side names the other) and
    `dup_candidate` on the newer open side of a duplicate. Nothing else is ever an op."""
    ops, conflicts, refused = [], [], []

    def add(rule, target, other, key):
        t, o = notes[target], notes[other]
        why = privacy_refusal(t["rel"], o["rel"])
        if why:
            refused.append({"rule": rule, "path": t["rel"], "reason": why})
            return
        if names_other(t, o):
            return
        ops.append({"rule": rule, "path": t["rel"], "key": key, "value": o["stem"],
                    "pair": [t["rel"], o["rel"]]})

    for v in verdicts:
        a, b = v["a"], v["b"]
        if v["verdict"] == "related":
            add("link", a, b, "related")
            add("link", b, a, "related")
        elif v["verdict"] == "duplicate":
            na, nb = notes[a], notes[b]
            if is_closed(na) or is_closed(nb) or names_other(na, nb) or names_other(nb, na):
                continue
            if na["cls"] != nb["cls"]:
                continue                     # an obligation and a note are related, not the same
            newer, older = sorted((na, nb), key=lambda n: (fmv(n["fm"], "captured"), n["rel"]))[::-1]
            add("dup_candidate", newer["rel"], older["rel"], "dup_candidate")
        elif v["verdict"] == "contradiction":
            conflicts.append({"a": a, "b": b, "quote_a": v["quote_a"].strip(),
                              "quote_b": v["quote_b"].strip(), "why": v.get("why", ""),
                              "title_a": notes[a]["title"], "title_b": notes[b]["title"]})
    for d in dups:
        na, nb = notes[d["a"]], notes[d["b"]]
        newer, older = sorted((na, nb), key=lambda n: (fmv(n["fm"], "captured"), n["rel"]))[::-1]
        add("dup_candidate", newer["rel"], older["rel"], "dup_candidate")
    return ops, conflicts, refused


# ---------------------------------------------------------------------- writer

WRITABLE = ("obligations", "vault/prep")
WRITE_KEYS = ("related", "dup_candidate")
REFUSED_KEYS = ("status", "duplicate_of", "done_at", "stale")
OPS_KEY = "dreaming_ops"
MAX_WRITES = 10
RECENT_S = 12 * 3600
FREEZE_DAYS = 7


def denied_write_bases(roots: Roots) -> list:
    r, h = roots.root, roots.home
    return [Path(os.path.realpath(p)) for p in (
        r / "_meta" / "goals", h / ".claude", h / "vault", r / "_inbox", r / "_sources",
        r / "reference") + tuple(b for n in DENIED_NAMES for b in (h / n, r.parent / n))]


def write_refusal(roots: Roots, path: Path) -> str:
    """Why Dreaming may not write this file, or "": real paths under obligations/ or
    vault/prep/ only, checked after realpath so a symlink cannot lead anywhere else."""
    real = Path(os.path.realpath(path))
    for d in denied_write_bases(roots):
        if _under(real, d):
            return "not writable: denied " + _show(d, roots)
    hit = next((n for n in DENIED_NAMES if n in real.parts), "")
    if hit:
        return "not writable: denied " + hit
    if not any(_under(real, Path(os.path.realpath(roots.root / w))) for w in WRITABLE):
        return "not writable: outside obligations/ and vault/prep/"
    if real.suffix != ".md":
        return "not writable: not a markdown note"
    return ""


def frozen(roots: Roots, rule: str, today: dt.date) -> str:
    """A rule is frozen for FREEZE_DAYS after one of its ops is undone. The file holds
    `until: YYYY-MM-DD`; a lifted freeze is a past date, never a removed file."""
    p = roots.state / "state" / ("freeze-" + rule)
    try:
        m = re.search(r"until:\s*(\d{4}-\d{2}-\d{2})", p.read_text(encoding="utf-8"))
    except OSError:
        return ""
    if m and dt.date.fromisoformat(m.group(1)) >= today:
        return m.group(1)
    return ""


def _list_add(current: str, item: str) -> str:
    parts = [x.strip() for x in current.split(",") if x.strip()]
    if item not in parts:
        parts.append(item)
    return ", ".join(parts)


def _list_drop(current: str, item: str) -> str:
    return ", ".join(x.strip() for x in current.split(",") if x.strip() and x.strip() != item)


def apply_op(roots: Roots, op: dict, night: str, now: dt.datetime, written: dict,
             write: bool) -> tuple:
    """(op id or "", refusal). One field on one file, as one atomic write, only after every
    check below. With write False nothing is written and the refusal is still computed."""
    key = op.get("key", "")
    if key in REFUSED_KEYS:
        return "", "refused key %s" % key
    if key not in WRITE_KEYS:
        return "", "refused key %s (not a Dreaming field)" % key
    path = roots.root / op["path"] if not op["path"].startswith("~/") else roots.home / op["path"][2:]
    why = write_refusal(roots, path)
    if why:
        return "", why
    until = frozen(roots, op["rule"], now.date())
    if until:
        return "", "rule %s frozen until %s" % (op["rule"], until)
    tonight = [w for w in written.values() if w.get("night") == night]
    if len(tonight) >= MAX_WRITES:
        return "", "write cap %d reached" % MAX_WRITES
    try:
        data = path.read_bytes()
        st = path.stat()
    except OSError as e:
        return "", "unreadable: %s" % e.__class__.__name__
    sha = sha256(data)
    if op.get("sha_before") and op["sha_before"] != sha:
        return "", "changed since read"
    last_ours = next((w["sha_after"] for w in sorted(written.values(), key=lambda w: w["at"],
                                                      reverse=True) if w["path"] == op["path"]), "")
    if now.timestamp() - st.st_mtime < RECENT_S and sha != last_ours:
        return "", "edited in the last 12 h by someone else"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "", "not UTF-8"
    fm = parse_fm(text)
    if not fm:
        return "", "no frontmatter (never added)"
    raw = fm.get(key, "")
    if isinstance(raw, (list, dict)) or str(raw).lstrip().startswith("["):
        return "", "%s is a list, not a Dreaming scalar" % key
    before = str(raw or "")
    after = _list_add(before, op["value"])
    if after == before:
        return "", "already there"
    if leaks(op["value"]) or leaks(after):
        return "", "privacy wall: the value names the personal pipeline"
    seq = 1 + max([int(k.split("#")[1]) for k in written if k.startswith(night + "#")] or [0])
    op_id = "%s#%d" % (night, seq)
    ops_before = fmv(fm, OPS_KEY)
    ops_after = _list_add(ops_before, "%s=%s" % (key, op_id))
    new = frontmatter.edit_frontmatter_text(text, {key: after, OPS_KEY: ops_after})
    if new is None:
        return "", "frontmatter did not parse"
    end_old, end_new = text.find("\n---", 4), new.find("\n---", 4)
    if new[end_new:] != text[end_old:]:
        return "", "body would change"
    back = parse_fm(new)
    if fmv(back, key) != after or fmv(back, "status") != fmv(fm, "status"):
        return "", "round trip failed"
    if not write:
        return "", ""
    # compare and swap: the file must still be the bytes read above
    if sha256(path.read_bytes()) != sha:
        return "", "changed since read"
    atomicio.write_text(path, new)
    written[op_id] = {"path": op["path"], "key": key, "before": before, "after": after,
                      "ops_before": ops_before, "ops_after": ops_after, "sha_before": sha,
                      "sha_after": sha256(new.encode("utf-8")), "night": night,
                      "rule": op["rule"], "value": op["value"], "at": now.isoformat()}
    return op_id, ""


def undo(roots: Roots, op_id: str, now: dt.datetime) -> tuple:
    """(exit code, message). Restores one field only if it still holds exactly what Dreaming
    wrote; anything else is another writer's and is left alone. The rule is then
    frozen for FREEZE_DAYS."""
    st = roots.state / "state"
    written = read_json(st / "written.json", {})
    undone = read_json(st / "undone.json", {})
    w = written.get(op_id)
    if w is None:
        return EXIT_ARGS, "no such op %s" % op_id
    if op_id in undone:
        return EXIT_UNDO_SKIPPED, "undo skipped: %s was already undone on %s" % (
            op_id, undone[op_id].get("at", "?"))
    path = roots.root / w["path"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return EXIT_UNDO_SKIPPED, "undo skipped: %s is unreadable" % w["path"]
    fm = parse_fm(text)
    tag = "%s=%s" % (w["key"], op_id)
    if fmv(fm, w["key"]) != w["after"] or tag not in [x.strip() for x in fmv(fm, OPS_KEY).split(",")]:
        return EXIT_UNDO_SKIPPED, "undo skipped: value changed by another writer"
    ops_left = _list_drop(fmv(fm, OPS_KEY), tag)
    sets, drop = {}, []
    if w["before"]:
        sets[w["key"]] = w["before"]
    else:
        drop.append(w["key"])
    if ops_left:
        sets[OPS_KEY] = ops_left
    else:
        drop.append(OPS_KEY)
    new = frontmatter.edit_frontmatter_text(text, sets, drop=tuple(drop))
    if new is None:
        return EXIT_UNDO_SKIPPED, "undo skipped: frontmatter did not parse"
    if sha256(path.read_bytes()) != sha256(text.encode("utf-8")):
        return EXIT_UNDO_SKIPPED, "undo skipped: value changed by another writer"
    atomicio.write_text(path, new)
    undone[op_id] = {"at": now.isoformat(), "sha_after": sha256(new.encode("utf-8"))}
    write_json(st / "undone.json", undone)
    until = (now.date() + dt.timedelta(days=FREEZE_DAYS)).isoformat()
    st.mkdir(parents=True, exist_ok=True)
    atomicio.write_text(st / ("freeze-" + w["rule"]), "until: %s\nby: undo %s\n" % (until, op_id))
    return EXIT_OK, "undone %s: %s %s restored; rule %s frozen until %s" % (
        op_id, w["path"], w["key"], w["rule"], until)


# ---------------------------------------------------------------------------------- state

def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomicio.write_text(path, json.dumps(data, indent=1, sort_keys=True) + "\n")


def headless() -> str:
    return next((v for v in HEADLESS_VARS if os.environ.get(v)), "")


def under_tmp(p: Path) -> bool:
    real = Path(os.path.realpath(p))
    for t in {tempfile.gettempdir(), "/tmp", "/private/tmp", "/var/folders", "/private/var/folders"}:
        if _under(real, Path(os.path.realpath(t))):
            return True
    return False


def resolve_mode(args, roots: Roots) -> tuple:
    """(mode, refusal). mode is "dry run" or "live"; refusal is "" or the reason to exit 5."""
    var = headless()
    if var and args.judge:
        return "", "refused: headless (%s set): --judge makes a live claude call" % var
    if var and args.write and not (args.root and under_tmp(roots.root)):
        return "", "refused: headless (%s set): --write outside a temp --root" % var
    return ("live" if args.write else "dry run"), ""


# ---------------------------------------------------------------------- the judge
#
# One tool-less `claude -p` per candidate pair: no tools, no MCP, a neutral cwd outside the
# notes, JSON output, a per-call dollar cap. The prompt is judge-brief.md plus the two notes, each fenced as
# data under a random tag. Nothing the judge says is trusted: check_verdict keeps only the enum
# and two quotes that are whole lines of their files.

def _claude_path() -> Path:
    """$DREAMING_CLAUDE, else `claude` on PATH, else ~/.local/bin/claude (the installer's spot)."""
    env = os.environ.get("DREAMING_CLAUDE")
    if env:
        return Path(env).expanduser()
    found = shutil.which("claude")
    return Path(found) if found else Path.home() / ".local" / "bin" / "claude"


CLAUDE = _claude_path()
JUDGE_MODEL = os.environ.get("DREAMING_MODEL", "haiku")
JUDGE_BRIEF = Path(os.environ.get("DREAMING_JUDGE_BRIEF") or REPO / "judge-brief.md")
NEUTRAL_CWD = Path(tempfile.gettempdir()) / "dreaming-judge-cwd"
CALL_TIMEOUT_S = 90
CALL_BUDGET_USD = 0.10
NIGHT_BUDGET_USD = 1.50
MIN_CALL_S = 15                        # never start a call with less than this before the deadline
MAX_ERRORS_IN_ROW = 3                  # then the judge is failing (logged out, broken), not one bad reply
DEADLINE = dt.time(6, 40)              # judge stops, so the report lands by 06:45
LATE_START = dt.time(6, 0)             # a scheduled run starting after this does nothing
RUNNER = subprocess.run                # the selftest swaps in recorded outputs
CLOCK = None                           # the selftest's fake clock: a function returning local now


def now_local() -> dt.datetime:
    return CLOCK() if CLOCK else dt.datetime.now(LOCAL)


def threshold(roots: Roots) -> float:
    """The five-hour wind-down point. `_meta/health/threshold-override` wins when readable (the
    owner's flat number), then `_meta/health/threshold` (the agent runner's own), then 90."""
    h = roots.root / "_meta" / "health"
    for name in ("threshold-override", "threshold"):
        try:
            return float((h / name).read_text().strip())
        except (OSError, ValueError):
            continue
    return 90.0


def usage_trip(roots: Roots) -> str:
    """"" when a judge call may start, else the wind-down reason, which follows "wound down at".

    usage.usage_now() on $DREAMING_USAGE_FILE, else <root>/_meta/health/usage.json. A fresh
    five-hour reading at the threshold trips, and so does a fresh weekly one at
    usage.WEEKLY_WINDDOWN (95) or the weekly no-new-work ceiling (weekly_ceiling_hit, which
    exempts haiku). A stale reading trips while its window is still open (unknown usage in a
    live window is treated as high). A stale reading whose window has reset says nothing about
    the new window: status line readings stop when interactive sessions do, so at 04:30 the
    reading is nearly always hours old, and tripping on it would mean the judge never runs. Then
    the call goes ahead and a limit message in any reply stops the night (usage.LIMIT_RE)."""
    usage.USAGE = Path(os.environ.get("DREAMING_USAGE_FILE")
                       or roots.root / "_meta" / "health" / "usage.json")
    u = usage.usage_now()
    now = now_local()
    thr = threshold(roots)
    pct = u.get("pct")
    if u.get("fresh"):
        if pct is not None and pct >= thr:
            return "%.0f%% (threshold %.0f%%)" % (pct, thr)
        wk = u.get("week_pct")
        if wk is not None and (wk >= usage.WEEKLY_WINDDOWN or usage.weekly_ceiling_hit(u, JUDGE_MODEL)):
            return "%.0f%% of the week (weekly wind-down %.0f%%)" % (wk, usage.WEEKLY_WINDDOWN)
        return ""
    reset = u.get("resets_at")
    if isinstance(reset, dt.datetime) and reset <= now:
        return ""
    if pct is None:
        return "unknown usage (no reading in _meta/health/usage.json)"
    age = u.get("age_s") or 0
    return "unknown usage (the last reading, %.0f%%, is %.0f min old and its window is still open)" \
        % (pct, age / 60.0)


def judge_prompt(brief: str, a: dict, b: dict) -> str:
    tag = os.urandom(4).hex()
    parts = [brief.rstrip(), ""]
    for side, n in (("A", a), ("B", b)):
        parts += ["===BEGIN NOTE %s DATA %s===" % (side, tag), "path: " + n["rel"], "",
                  n["text"].rstrip(), "===END NOTE %s DATA %s===" % (side, tag), ""]
    parts.append('Reply with one JSON object and nothing else: {"verdict": "related|duplicate|'
                 'contradiction|none", "quote_a": "<one whole line of note A>", "quote_b": '
                 '"<one whole line of note B>", "why": "<one short sentence>"}')
    return "\n".join(parts)


def call_judge(prompt: str, timeout: float) -> dict:
    """One no-tools turn. Returns the parsed reply plus `_cost_usd` and `_secs`, or a dict with
    `_error` (and `_limit` when the reply was a usage-limit message)."""
    NEUTRAL_CWD.mkdir(parents=True, exist_ok=True)
    argv = [str(CLAUDE), "-p", prompt, "--model", JUDGE_MODEL, "--tools", "",
            "--output-format", "json", "--strict-mcp-config",
            "--max-budget-usd", "%.2f" % CALL_BUDGET_USD]
    t0 = dt.datetime.now().timestamp()
    out = {"_cost_usd": 0.0, "_secs": 0.0}
    try:
        proc = RUNNER(argv, cwd=str(NEUTRAL_CWD), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return dict(out, _error="timed out after %.0fs" % timeout, _secs=round(timeout, 1))
    except OSError as exc:
        return dict(out, _error="could not start claude: %s" % exc)
    out["_secs"] = round(dt.datetime.now().timestamp() - t0, 1)
    raw = (proc.stdout or "") + (proc.stderr or "")
    try:
        env = json.loads(proc.stdout)
        if not isinstance(env, dict):
            raise ValueError("envelope is not an object")
    except ValueError:
        return dict(out, _error="no JSON envelope (rc=%s): %s" % (proc.returncode, raw[:200]),
                    _limit=bool(usage.LIMIT_RE.search(raw)))
    out["_cost_usd"] = float(env.get("total_cost_usd") or 0.0)
    result = str(env.get("result") or "")
    if env.get("is_error"):
        return dict(out, _error="claude -p reported an error: %s" % result[:200],
                    _limit=bool(usage.LIMIT_RE.search(result)))
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.strip(), flags=re.M).strip()
    try:
        v = json.loads(text)
    except ValueError:
        return dict(out, _error="reply was not JSON: %r" % text[:200])
    if not isinstance(v, dict):
        return dict(out, _error="reply was not an object: %r" % text[:200])
    v.update(out)
    return v


def deadline_at(args, now: dt.datetime) -> dt.datetime:
    hm = DEADLINE
    if args.deadline:
        h, m = (int(x) for x in args.deadline.split(":"))
        hm = dt.time(h, m)
    return now.replace(hour=hm.hour, minute=hm.minute, second=0, microsecond=0)


def late_start(args, now: dt.datetime) -> str:
    """"" or why a live run must not start. Without --deadline this is the 04:30 slot: after
    06:00 it does nothing, so a run begun late never runs into the morning's other jobs.
    A hand run sets --deadline; it must be ahead."""
    if not args.judge:
        return ""
    if not args.deadline and now.time() > LATE_START:
        return "late start %s, after %s" % (now.strftime("%H:%M"), LATE_START.strftime("%H:%M"))
    if deadline_at(args, now) - now < dt.timedelta(seconds=MIN_CALL_S):
        return "late start %s, deadline %s already passed" % (
            now.strftime("%H:%M"), deadline_at(args, now).strftime("%H:%M"))
    return ""


def judge_pairs(roots: Roots, todo: list, notes: dict, args, day: Path) -> tuple:
    """(replies, stats, left). Judges pairs in order until one of: the list ends, --max-calls,
    the night's budget, the deadline, a usage wind-down, a limit message. Every call is logged
    to <night>/judge.jsonl with its cost and seconds. `left` is what was not judged, to carry."""
    try:
        brief = JUDGE_BRIEF.read_text(encoding="utf-8")
    except OSError:
        # "failed", not "skipped": a lost brief or binary must turn the night red
        return [], {"calls": 0, "cost": 0.0, "secs": 0.0, "stop": "failed",
                    "why": "no judge brief at %s" % JUDGE_BRIEF}, list(todo)
    if not CLAUDE.exists() and RUNNER is subprocess.run:
        return [], {"calls": 0, "cost": 0.0, "secs": 0.0, "stop": "failed",
                    "why": "claude not found at %s" % CLAUDE}, list(todo)
    t0 = now_local()
    until = deadline_at(args, t0)
    replies, log, retry = [], [], []
    st = {"calls": 0, "cost": 0.0, "secs": 0.0, "stop": "", "why": "", "errors": 0}
    i, in_row = 0, 0
    for i, p in enumerate(todo):
        now = now_local()
        if st["calls"] >= args.max_calls:
            st["stop"], st["why"] = "stopped", "at %d calls" % args.max_calls
            break
        if st["cost"] + CALL_BUDGET_USD > NIGHT_BUDGET_USD + 1e-9:
            st["stop"], st["why"] = "stopped", "at the night's $%.2f budget" % NIGHT_BUDGET_USD
            break
        left = (until - now).total_seconds()
        if left < MIN_CALL_S:
            st["stop"], st["why"] = "stopped", "at the %s deadline" % until.strftime("%H:%M")
            break
        trip = usage_trip(roots)
        if trip:
            st["stop"], st["why"] = "wound down", trip
            break
        a, b = notes[p["a"]], notes[p["b"]]
        v = call_judge(judge_prompt(brief, a, b), min(CALL_TIMEOUT_S, left))
        st["calls"] += 1
        st["cost"] += v.get("_cost_usd", 0.0)
        st["secs"] += v.get("_secs", 0.0)
        log.append({"a": p["a"], "b": p["b"], "cost_usd": v.get("_cost_usd", 0.0),
                    "secs": v.get("_secs", 0.0), "verdict": v.get("verdict", ""),
                    "error": v.get("_error", "")})
        if v.get("_limit"):
            st["stop"], st["why"] = "wound down", "the usage limit (claude said so)"
            break                        # i stays on this pair, so it is carried, not lost
        if "_error" in v:
            st["errors"] += 1
            in_row += 1
            retry.append(p)              # carried, so a bad reply does not lose the pair
            if in_row >= MAX_ERRORS_IN_ROW:
                # "Not logged in" and the like: stop, and fail the wrapper's check
                st["stop"], st["why"] = "failed", "%d unusable replies in a row, last: %s" % (
                    in_row, v["_error"][:120])
                i += 1
                break
            continue
        in_row = 0
        reply = {k: x for k, x in v.items() if not k.startswith("_")}
        reply.update(a=p["a"], b=p["b"])
        replies.append(reply)
    else:
        i = len(todo)
    atomicio.write_text(day / "judge.jsonl", "".join(json.dumps(x, sort_keys=True) + "\n" for x in log))
    st["cost"] = round(st["cost"], 4)
    return replies, st, list(todo[i:]) + retry


def judge_text(st: dict) -> str:
    """The judge line. A job wrapper's artifact check can read it: '^judge: (ran N calls|skipped (|wound
    down at)'. "judge: failed" matches none of them, so a judge that cannot answer at all
    turns the night red instead of reading as a good one."""
    if st["stop"] == "skipped":
        return "judge: skipped (%s)" % st["why"]
    done = "%d calls, $%.2f, %d min" % (st["calls"], st["cost"], round(st["secs"] / 60.0))
    if st.get("errors"):
        done += ", %d unusable" % st["errors"]
    if st["stop"] == "wound down":
        return "judge: wound down at %s after %s" % (st["why"], done)
    if st["stop"] == "stopped":
        return "judge: ran %s, stopped %s" % (done, st["why"])
    if st["stop"] == "failed":
        return "judge: failed after %s, %s" % (done, st["why"])
    return "judge: ran " + done


def judge_line(args, replayed: int = 0, stats: dict | None = None) -> str:
    if args.judge_replay:
        return "judge: replayed %d recorded verdicts (%s)" % (replayed, Path(args.judge_replay).name)
    if args.judge and stats:
        return judge_text(stats)
    return "judge: skipped (dry run)"


def cmd_run(args) -> int:
    roots = Roots(args.root, args.home, getattr(args, "state", None))
    mode, why = resolve_mode(args, roots)
    if why:
        print(why)
        return EXIT_HEADLESS
    now = now_local()
    late = late_start(args, now)
    if late:
        night = args.night or default_night(now)
        report = "Dreaming %s: skipped, %s\njudge: skipped (%s)\n" % (
            night.strftime("%a %-d %b"), late, late)
        (roots.state / night.isoformat()).mkdir(parents=True, exist_ok=True)
        atomicio.write_text(roots.state / night.isoformat() / "report.md", report)
        atomicio.write_text(roots.state / "latest.md", report)
        write_json(roots.state / "state" / "last.json", {
            "night": night.isoformat(), "mode": "skipped, " + late, "changed": 0,
            "conflicts": 0, "top": "", "judge": "judge: skipped (%s)" % late,
            "at": now.isoformat()})
        print(report, end="")
        return EXIT_LATE
    st = roots.state / "state"
    cursor = read_json(st / "cursor.json", {})
    if args.night:
        night = args.night
        start, end = night_window(night)
    else:
        night = default_night(now)
        start = now - dt.timedelta(hours=24)
        if cursor.get("last_start"):
            try:
                start = dt.datetime.fromisoformat(cursor["last_start"])
            except ValueError:
                pass
        end = now
    g = gather(roots, start, end)
    seen = read_json(st / "seen.json", {})
    fresh = [i for i in g["items"] if seen.get(i["key"]) != i["sha256"]]
    g["unchanged"] = g["files"] - len(fresh)
    day = roots.state / night.isoformat()
    write_json(day / "gather.json", g)
    res = consolidate(roots, night, g, args, mode, now, start, end)
    # Seen list and cursor move only once consolidate has returned, so a crash in the judge or
    # the write loop leaves tonight's window to be read again rather than lost.
    for i in fresh:
        seen[i["key"]] = i["sha256"]
    write_json(st / "seen.json", seen)
    if not args.night:
        write_json(st / "cursor.json", {"last_start": now.isoformat(), "night": night.isoformat()})
    jline = judge_line(args, res["replayed"], res.get("judge"))
    enabled = (st / "writes-enabled").exists()
    label, asked = mode, ""
    if mode == "live" and not enabled:
        label = "live, but no writes: state/writes-enabled is absent"
    if not args.night and not enabled:
        # A scheduled night (no --night) counts toward the dry-run week; a replay never does.
        k, week = count_night(roots, night, res)
        label = "dry run, night %d of %d" % (k, DRY_WEEK) if k <= DRY_WEEK else "dry run, night %d" % k
        if k >= DRY_WEEK:
            asked = go_live(roots, night, week)
            sent = next((q for q in read_json(st / "questions-sent.json", [])
                         if isinstance(q, dict) and q.get("id") == GO_LIVE_ID), None)
            if sent and not asked.startswith("asked"):
                label += ", go-live asked %s" % _day(sent.get("night", ""))
    meta = {"start": start, "lms_age": lms_age(roots, now.date()), "asked": asked}
    report = render_report(night, label, jline, g, len(fresh), res, meta)
    atomicio.write_text(day / "report.md", report)
    atomicio.write_text(roots.state / "latest.md", report)
    write_json(st / "last.json", {
        "night": night.isoformat(), "mode": mode, "label": label, "changed": len(res["changed"]),
        "conflicts": len(res["conflicts"]), "proposals": len(res["proposals"]),
        "top": top_item(res), "judge": jline, "at": now.isoformat()})
    print(report, end="")
    return EXIT_OK


# ------------------------------------------------------ the dry-run week and its one question
#
# Writes stay off until the owner creates state/writes-enabled. Every scheduled night without it is a
# dry-run night, counted in state/nights.json with what it would have done. On the seventh the
# go-live question is written once, carrying the week's numbers. Silence is not
# consent: its default is no. Question files are capped at three a week and none is asked
# twice, keyed in state/questions-sent.json.

DRY_WEEK = 7
GO_LIVE_ID = "dreaming-go-live"
QUESTION_CAP = 3                       # question files a week, all kinds together


def _day(iso: str) -> str:
    try:
        return dt.date.fromisoformat(iso).strftime("%a %-d %b")
    except (TypeError, ValueError):
        return iso or "?"


def count_night(roots: Roots, night: dt.date, res: dict) -> tuple:
    """Record tonight in state/nights.json (a rerun of the same night overwrites its row) and
    return (k, the last DRY_WEEK rows), k being tonight's place among the recorded nights."""
    path = roots.state / "state" / "nights.json"
    nights = read_json(path, {})
    if not isinstance(nights, dict):
        nights = {}
    j = res.get("judge") or {}
    nights[night.isoformat()] = {
        "would_write": res.get("ops", 0), "conflicts": len(res["conflicts"]),
        "proposals": len(res["proposals"]), "pairs": res.get("pairs", 0),
        "calls": j.get("calls", 0), "cost": round(float(j.get("cost", 0) or 0), 4),
        "discarded": len(res.get("discarded") or [])}
    write_json(path, nights)
    order = sorted(nights)
    week = [dict(nights[n], night=n) for n in order[-DRY_WEEK:]]
    return order.index(night.isoformat()) + 1, week


def ask(roots: Roots, qid: str, body: dict, night: dt.date) -> str:
    """Write one question file under _meta/health/questions/, or say why not. Never a file
    that exists, never an id asked before, never a fourth in seven nights."""
    st = roots.state / "state"
    sent = [q for q in read_json(st / "questions-sent.json", []) if isinstance(q, dict)]
    if any(q.get("id") == qid for q in sent):
        return "not asked: %s was asked before" % qid
    recent = 0
    for q in sent:
        try:
            recent += (night - dt.date.fromisoformat(q.get("night", ""))).days < 7
        except (TypeError, ValueError):
            pass
    if recent >= QUESTION_CAP:
        return "not asked: %d questions this week already (cap %d)" % (recent, QUESTION_CAP)
    path = roots.root / "_meta" / "health" / "questions" / (qid + ".json")
    if path.exists():
        return "not asked: %s already exists" % _show(path, roots)
    write_json(path, dict(body, id=qid))
    sent.append({"id": qid, "night": night.isoformat(), "path": _show(path, roots)})
    write_json(st / "questions-sent.json", sent)
    return "asked: %s (%s)" % (qid, _show(path, roots))


def go_live(roots: Roots, night: dt.date, week: list) -> str:
    """The go-live question, with the dry-run week's numbers. Closed yes or no, no on_answer."""
    tot = {k: sum(w.get(k, 0) for w in week) for k in
           ("would_write", "conflicts", "proposals", "calls", "discarded")}
    cost = sum(float(w.get("cost", 0) or 0) for w in week)
    first, last = week[0]["night"], week[-1]["night"]
    no = "no: keep Dreaming a report; ask me again only when I say so"
    body = {
        "created": now_local().isoformat(timespec="seconds"),
        "question": ("Dreaming's dry-run week (%s to %s, %d nights): it would have made %s "
                     "(related: or dup_candidate:), flagged %s and made %s (closes, stale "
                     "marks, memory and bank). Switch the field writes on?"
                     % (_day(first), _day(last), len(week), _n(tot["would_write"], "field write"),
                        _n(tot["conflicts"], "conflict"), _n(tot["proposals"], "proposal"))),
        "options": ["yes: switch on related: and dup_candidate: writes in obligations/ and "
                    "vault/prep/, at most 10 a night, each undoable", no],
        "default": no,
        "context": ("Judge over the week: %d calls, $%.2f, %d replies thrown away as unusable. "
                    "Nothing else changes with a yes: closes, stale marks, memory and bank "
                    "stay proposals. The nightly reports are in _meta/health/dreams/<night>/"
                    "report.md, and state/nights.json has each night's counts."
                    % (tot["calls"], cost, tot["discarded"])),
        "details": ("Written by dreaming.py on the dry-run week's seventh night. A yes is "
                    "carried out by creating the empty file <state>/state/writes-enabled. "
                    "No answer means no."),
        "source": "dreaming.py, night %s" % night.isoformat(),
    }
    return ask(roots, GO_LIVE_ID, body, night)


def lms_age(roots: Roots, today: dt.date) -> int | None:
    """Days since the newest course-site (LMS) submission-state snapshot, a file named
    `YYYY-MM-DD-submission-state.tsv` in <root>/_meta/sync/, or None when there is none.
    Optional: it only adds the snapshot's age to the report's last line."""
    files = sorted((roots.root / "_meta" / "sync").glob("*-submission-state.tsv"))
    if not files:
        return None
    try:
        return (today - dt.date.fromisoformat(files[-1].name[:10])).days
    except ValueError:
        return None


def top_item(res: dict) -> str:
    """The one item a dashboard line carries: the first conflict, else the
    first proposal (hard-external first), else the first change."""
    if res["conflicts"]:
        c = res["conflicts"][0]
        return "conflict: %s vs %s" % (_clip(c["title_a"], 50), _clip(c["title_b"], 50))
    props = sorted(res["proposals"], key=lambda p: not p["hard"])
    if props:
        return "proposal: " + _clip(props[0]["text"], 100)
    if res["changed"]:
        return "changed: " + res["changed"][0]
    return ""


def consolidate(roots: Roots, night: dt.date, g: dict, args, mode: str, now: dt.datetime,
                start: dt.datetime, end: dt.datetime) -> dict:
    """Candidates, the rules and, in live mode, the writer. Verdicts come from `--judge-replay
    FILE` (a JSON list, or JSON lines, of {a, b, verdict, quote_a, quote_b, why}), or from
    the live judge with --judge. Every op, written or not, goes to <night>/changes.jsonl."""
    st = roots.state / "state"
    day = roots.state / night.isoformat()
    cand = candidates(roots, night, g)
    write_json(day / "candidates.json", {k: v for k, v in cand.items() if k != "window"})
    carry = [dict(p, night=p.get("night", night.isoformat()))
             for p in cand["pairs"] if p["route"] == "carry"]
    todo = [p for p in cand["pairs"] if p["route"] == "judge"]
    expired, jstats = 0, None
    if args.judge:
        # Last nights' carry goes first, then tonight's; a carried pair older than 7 nights, or
        # whose file is gone, is dropped and counted.
        old, have = [], {pair_key(p["a"], p["b"]) for p in cand["pairs"]}
        for p in read_json(st / "carry.json", []):
            try:
                age = (night - dt.date.fromisoformat(p.get("night", ""))).days
            except (TypeError, ValueError):
                age = CARRY_NIGHTS + 1
            if age > CARRY_NIGHTS or not all((roots.root / p[s]).is_file() for s in ("a", "b")):
                expired += 1
            elif pair_key(p["a"], p["b"]) not in have:
                old.append(p)
                have.add(pair_key(p["a"], p["b"]))
        todo = old + todo
        carry = [p for p in carry if pair_key(p["a"], p["b"]) not in
                 {pair_key(q["a"], q["b"]) for q in old}]
    pairs = {pair_key(p["a"], p["b"]): p for p in todo}
    notes = {}
    cls_of = {i["path"]: i["class"] for i in g["items"]}
    for p in todo + cand["pairs"] + cand["dups"]:
        for side in ("a", "b"):
            rel = p[side]
            if rel not in notes:
                # the real class, so a mail and a prep note never pass the same-class dup test
                cls = cls_of.get(rel) or ("obligations" if rel.startswith("obligations/") else
                                          "reference" if rel.startswith("reference/") else
                                          "mail" if rel.startswith("_sources/outlook/") else "vault")
                notes[rel] = load_note(roots, rel, cls, whole=True)
    if args.judge:
        raw, jstats, left = judge_pairs(roots, todo, notes, args, day)
        carry = [dict(p, night=p.get("night", night.isoformat())) for p in left] + carry
    else:
        raw = load_verdicts(Path(args.judge_replay)) if args.judge_replay else []
    write_json(st / "carry.json", carry)
    verdicts, discarded = [], []
    for v in raw:
        ok, why = check_verdict(v, pairs, notes)
        if ok:
            verdicts.append(ok)
        else:
            d = v if isinstance(v, dict) else {}
            discarded.append({"a": str(d.get("a", "")), "b": str(d.get("b", "")), "why": why})
    # What the judge said that was thrown away, and why: the dry-run week's evidence.
    write_json(day / "discarded.json", discarded)
    ops, conflicts, refused = rule_ops(verdicts, notes, cand["dups"])
    conflicts = cand["contradictions"] + [
        dict(c, date_a=date_of(roots, c["a"], c["quote_a"]), date_b=date_of(roots, c["b"], c["quote_b"]))
        for c in conflicts]
    written = read_json(st / "written.json", {})
    counts = {}
    snapshot = read_json(st / "status-snapshot.json", {})
    proposals = (close_proposals(roots, start, end, snapshot, counts)
                 + stale_proposals(roots, [load_note(roots, i["path"], i["class"], whole=True)
                                           for i in g["items"] if i["class"] in PAIR_CLASSES])
                 + memory_proposals(roots, g["items"])
                 + bank_proposals(roots, g["items"], counts))
    changed, log = [], []
    enabled = (st / "writes-enabled").exists()
    for op in ops:
        op_id, why = apply_op(roots, op, night.isoformat(), now, written,
                              write=(mode == "live" and enabled))
        if op_id:
            changed.append(op_id)
            write_json(st / "written.json", written)   # after each write, so a crash keeps undo
        log.append(dict(op, op_id=op_id, result=why or ("written" if op_id else "not written: dry run")))
    for r in refused:
        log.append(dict(r, op_id="", result=r["reason"]))
    if changed:
        write_json(st / "written.json", written)
    atomicio.write_text(day / "changes.jsonl", "".join(json.dumps(x, sort_keys=True) + "\n"
                                                       for x in log))
    write_json(day / "conflicts.json", conflicts)
    write_json(day / "proposals.json", proposals)
    snap = {}
    for p in _walk(roots.root / "obligations", "*.md", False):
        snap[_show(p, roots)] = frontmatter_value(read_head(p), "status")
    write_json(st / "status-snapshot.json", snap)
    return {"replayed": len(raw), "discarded": discarded, "ops": len(ops), "changed": changed,
            "conflicts": conflicts, "proposals": proposals, "counts": counts,
            "carried": len(carry), "too_large": cand["too_large"], "pairs": len(cand["pairs"]),
            "judge": jstats, "expired": expired}


def load_verdicts(path: Path) -> list:
    text = read_text(path).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except ValueError:
        out = []
        for ln in text.splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                out.append(ln)            # kept, so check_verdict discards and counts it
        return out


REPORT_LINES = 12                      # the report's own lines, proposals not counted
REPORT_PROPOSALS = 5


def render_report(night: dt.date, mode: str, jline: str, g: dict, fresh: int,
                  res: dict | None = None, meta: dict | None = None) -> str:
    """The dream report: at most 12 lines plus 5 proposals. Header, judge,
    inputs, changes with the undo command, up to 3 conflicts quoted by title and dated, up to 5
    proposals hard-external first each ending with its route, the carry and the LMS snapshot
    age, the question asked tonight if any, and any root that was not found."""
    res = res or {"changed": [], "conflicts": [], "proposals": [], "pairs": 0, "carried": 0}
    meta = meta or {}
    per = ", ".join("%s %d" % (c, n) for c, n in g["classes"].items() if n)
    ch = res["changed"]
    extra = ""
    if res.get("too_large"):
        extra += ", %d too large to judge" % res["too_large"]
    if meta.get("start"):
        extra += ", from %s" % meta["start"].astimezone(LOCAL).strftime("%H:%M %a")
    lines = [
        "Dreaming %s: %s" % (night.strftime("%a %-d %b"), mode),
        jline,
        "inputs: %d files (%s), %d new since last read, %d refused, %d sent mail skipped%s"
        % (g["files"], per or "none", fresh, len(g["refused"]), g["sent_skipped"], extra),
        "changed: %d%s" % (len(ch), (" (undo: dreaming.py undo %s)" % ch[0]) if ch else ""),
        "conflicts: %d" % len(res["conflicts"]),
    ]
    for i, c in enumerate(res["conflicts"][:3], 1):
        lines.append("  %d. %s (%s, %s) vs %s (%s, %s)" % (
            i, _clip(c["title_a"], 50), c["date_a"][0], c["date_a"][1],
            _clip(c["title_b"], 50), c["date_b"][0], c["date_b"][1]))
    # Only a proposal with a route (" -> ") is listed; one without is counted.
    routed = [p for p in res["proposals"] if " -> " in p["text"]]
    props = sorted(routed, key=lambda p: not p["hard"])[:REPORT_PROPOSALS]
    unrouted = len(res["proposals"]) - len(routed)
    lines.append("proposals: %d%s" % (len(res["proposals"]),
                                      (", %d without a route (counted only)" % unrouted) if unrouted else ""))
    for i, p in enumerate(props, 1):
        lines.append("  %d. %s" % (i, _clip(p["text"], 160)))
    tail = "pairs: %d, carried: %d, expired: %d" % (res["pairs"], res["carried"], res.get("expired", 0))
    if "lms_age" in meta:
        age = meta["lms_age"]
        tail += (", LMS snapshot %d days old" % age) if age is not None else ""
    lines.append(tail)
    if meta.get("asked"):
        lines.append(meta["asked"])
    if g["missing_roots"]:
        lines.append(_clip("not found: " + ", ".join(g["missing_roots"]), 200))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------- the four surfaces
#
# A status page, a terminal pane, a morning email and a phone page can each carry one line,
# read from state/last.json only: no git, no gather, so a surface costs a file read and can
# never fail because Dreaming did. Wrap the call in try/except all the same.

def surface(root: Path | None = None, now: dt.datetime | None = None) -> dict:
    """{ran, night, label, changed, conflicts, proposals, top, age_days, report} for the
    surfaces. `root` defaults to $DREAMING_ROOT or the current directory."""
    base = Path(root or live_repo()) / "_meta" / "health" / "dreams"
    last = read_json(base / "state" / "last.json", {})
    if not isinstance(last, dict) or not last.get("night"):
        return {"ran": False, "report": ""}
    now = now or now_local()
    try:
        # a report is fresh on the morning after its night; each day beyond is a day old
        age = (now.astimezone(LOCAL).date() - dt.date.fromisoformat(last["night"])).days - 1
    except ValueError:
        age = None
    return {"ran": True, "night": last["night"], "label": last.get("label") or last.get("mode", ""),
            "changed": int(last.get("changed", 0) or 0), "conflicts": int(last.get("conflicts", 0) or 0),
            "proposals": int(last.get("proposals", 0) or 0), "top": last.get("top", ""),
            "age_days": age, "report": "_meta/health/dreams/latest.md"}


def _n(k: int, word: str) -> str:
    return "%d %s%s" % (k, word, "" if k == 1 else "s")


def _stale(s: dict) -> str:
    a = s.get("age_days")
    return (", %s old" % _n(a, "day")) if isinstance(a, int) and a > 0 else ""


def surface_text(kind: str, root: Path | None = None, now: dt.datetime | None = None) -> str:
    """One line for `kind`: master | pane | email | phone."""
    s = surface(root, now)
    if not s["ran"]:
        return {"email": "Dreaming: no report yet.", "phone": "Dreaming: no report yet."}.get(
            kind, "no report yet (the 04:30 job has not run)")
    counts = "%d changed, %s, %s" % (s["changed"], _n(s["conflicts"], "conflict"),
                                      _n(s["proposals"], "proposal"))
    top = (", top: " + s["top"]) if s["top"] else ""
    if kind == "email":
        return "Dreaming (%s): %s%s%s." % (_day(s["night"]), counts, top, _stale(s))
    if kind == "phone":
        return "Dreaming: %d annotated, %d to check%s." % (
            s["changed"], s["conflicts"] + s["proposals"], _stale(s))
    if kind == "pane":
        return "%s · %s · %d to tick%s%s" % (
            _day(s["night"]), _n(s["conflicts"], "conflict"), s["proposals"], _stale(s),
            (" · " + _clip(s["top"], 60)) if s["top"] else "")
    return "%s, %s: %s%s%s. Full report: `%s`." % (
        _day(s["night"]), s["label"], counts, top, _stale(s), s["report"])


def cmd_gather(args) -> int:
    roots = Roots(args.root, args.home)
    night = args.night or default_night(dt.datetime.now(LOCAL))
    start, end = night_window(night)
    g = gather(roots, start, end)
    g["night"] = night.isoformat()
    if args.json:
        json.dump(g, sys.stdout, indent=1)
        print()
    else:
        print("night %s: %d files, %d bytes" % (night, g["files"], g["bytes"]))
        for c, n in g["classes"].items():
            print("  %-12s %d" % (c, n))
        print("  refused %d, sent mail skipped %d, ledger entries changed %d"
              % (len(g["refused"]), g["sent_skipped"], len(g["ledger_sids"])))
    return EXIT_OK


def cmd_candidates(args) -> int:
    roots = Roots(args.root, args.home)
    night = args.night or default_night(dt.datetime.now(LOCAL))
    c = candidates(roots, night)
    if args.json:
        json.dump({k: v for k, v in c.items() if k != "window"}, sys.stdout, indent=1)
        print()
    else:
        print("night %s: %d day notes against %d, %d pairs (%d judged, %d carried, %d too large)"
              % (c["night"], c["day_notes"], c["corpus"], len(c["pairs"]), c["judged"],
                 c["carried"], c["too_large"]))
        for p in c["pairs"]:
            print("  %-5s %s | %s  [%s]" % (p["route"][:5], p["a"], p["b"], " ".join(p["shared"])))
        print("skipped: " + ", ".join("%s %d" % kv for kv in c["skipped"].items()))
        for d in c["dups"]:
            print("  duplicate: %s | %s (%s)" % (d["a"], d["b"], d["why"]))
        for x in c["contradictions"]:
            print("  conflict: %s (%s) vs %s (%s): %s" % (x["a"], x["date_a"][0], x["b"],
                                                          x["date_b"][0], x["why"]))
    return EXIT_OK


def cmd_undo(args) -> int:
    roots = Roots(args.root, args.home)
    var = headless()
    if var and not (args.root and under_tmp(roots.root)):
        print("refused: headless (%s set): undo writes a note outside a temp --root" % var)
        return EXIT_HEADLESS
    code, msg = undo(roots, args.op_id, dt.datetime.now(LOCAL))
    print(msg)
    return code


def status(roots: Roots) -> dict:
    """What the surfaces read: mode, night, changed, conflicts, top, from the last
    run's state/last.json, plus the carry and the write switch."""
    latest = roots.state / "latest.md"
    try:
        text = latest.read_text(encoding="utf-8")
    except OSError:
        text = ""
    last = read_json(roots.state / "state" / "last.json", {})
    if not isinstance(last, dict):
        last = {}
    carry = read_json(roots.state / "state" / "carry.json", [])
    return {
        "mode": last.get("mode", "not run"),
        "night": last.get("night", ""),
        "changed": int(last.get("changed", 0) or 0),
        "conflicts": int(last.get("conflicts", 0) or 0),
        "top": last.get("top", ""),
        "judge": last.get("judge", ""),
        "at": last.get("at", ""),
        "carry": len(carry) if isinstance(carry, list) else 0,
        "writes_enabled": (roots.state / "state" / "writes-enabled").exists(),
        "report": str(latest) if text else "",
    }


def cmd_status(args) -> int:
    s = status(Roots(args.root, args.home, args.state))
    if args.json:
        json.dump(s, sys.stdout, indent=1)
        print()
    else:
        print("dreaming: %s, night %s, changed %d, conflicts %d, carry %d, writes %s"
              % (s["mode"], s["night"] or "none", s["changed"], s["conflicts"], s["carry"],
                 "enabled" if s["writes_enabled"] else "off"))
        if s["top"]:
            print("top: " + s["top"])
    return EXIT_OK


def _date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError("not a YYYY-MM-DD date: %r" % s)


def _hhmm(s: str) -> str:
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", s or "")
    if not m:
        raise argparse.ArgumentTypeError("not an HH:MM time: %r" % s)
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dreaming.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--root", type=Path, help="notes root (default: $DREAMING_ROOT or the current directory)")
        p.add_argument("--home", type=Path, help="home for the outside roots (default: $DREAMING_HOME or ~)")

    r = sub.add_parser("run")
    common(r)
    r.add_argument("--night", type=_date)
    g = r.add_mutually_exclusive_group()
    g.add_argument("--judge", action="store_true")
    g.add_argument("--judge-replay", metavar="FILE")
    r.add_argument("--write", action="store_true")
    r.add_argument("--deadline", metavar="HH:MM", type=_hhmm,
                   help="judge stops here (default 06:40); a hand run with --judge needs it")
    r.add_argument("--max-calls", type=int, default=JUDGE_CAP)
    r.add_argument("--state", type=Path, help="state dir (default <root>/_meta/health/dreams)")
    ga = sub.add_parser("gather")
    common(ga)
    ga.add_argument("--night", type=_date)
    ga.add_argument("--json", action="store_true")
    s = sub.add_parser("status")
    common(s)
    s.add_argument("--json", action="store_true")
    s.add_argument("--state", type=Path)
    c = sub.add_parser("candidates")
    common(c)
    c.add_argument("--night", type=_date)
    c.add_argument("--json", action="store_true")
    u = sub.add_parser("undo")
    common(u)
    u.add_argument("op_id", metavar="NIGHT#N")
    args = ap.parse_args(argv)
    if args.cmd == "candidates":
        return cmd_candidates(args)
    if args.cmd == "undo":
        return cmd_undo(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "gather":
        return cmd_gather(args)
    if args.cmd == "status":
        return cmd_status(args)
    ap.print_help()
    return EXIT_ARGS


if __name__ == "__main__":
    sys.exit(main())
