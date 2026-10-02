#!/usr/bin/env python3
"""Read the Claude usage snapshot that a Claude Code status line writes. Stdlib only.

    import usage
    usage.USAGE = Path("<root>/_meta/health/usage.json")
    u = usage.usage_now()   # {pct, resets_at, age_s, source, fresh, week_pct, week_resets_at}

The file is JSON: {"_written": "<ISO time>", "_source": "statusline", "data": <the status
line's JSON>}. The five-hour percentage is looked for under several field names, so it
tolerates small changes in the status line's shape. A reading older than USAGE_STALE_S is
not fresh. Nothing here raises: a missing or corrupt file reads as "no reading".

Part of dreaming (MIT licence), trimmed from the agent manager in the Life Tracker system.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

def local_zone():
    """$DREAMING_TZ, else $TZ, else the Mac's own zone (from /etc/localtime), else UTC."""
    name = os.environ.get("DREAMING_TZ") or os.environ.get("TZ") or ""
    if not name:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            name = link.split("zoneinfo/", 1)[1]
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or "UTC")
    except Exception:
        return dt.timezone.utc


P = local_zone()

USAGE = Path(os.environ.get("DREAMING_USAGE_FILE", "usage.json"))
USAGE_STALE_S = 45 * 60          # usage older than this cannot justify a wind-down
WEEKLY_WINDDOWN = 95.0           # a weekly reading at or over this stops new judge calls
WEEKLY_STALE_S = 6 * 3600        # a weekly reading older than this refuses nothing
WEEKLY_NO_NEW_RUNS = 92.0        # no new Opus or Sonnet work above this; haiku still may
LIMIT_RE = re.compile(r"hit your (?:session|usage) limit|usage limit reached|out of usage credits"
                      r"|cc_cli_limit_message|rate.?limit(?:ed)?\b.*(?:reset|try again)", re.I)


def _now() -> dt.datetime:
    return dt.datetime.now(P)


def _iso(d: dt.datetime | None) -> str:
    return d.astimezone(P).isoformat(timespec="seconds") if d else ""


def _parse(s: str) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return (d.replace(tzinfo=P) if d.tzinfo is None else d).astimezone(P)


def _dig(d, *paths):
    """First value found at any of several dotted paths."""
    for path in paths:
        cur = d
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return None


def usage_now() -> dict:
    """{pct, resets_at, age_s, source, fresh, week_pct, week_resets_at}. pct is the five-hour
    window percentage or None."""
    if not USAGE.exists():
        return {"pct": None, "resets_at": None, "age_s": None, "source": "none", "fresh": False}
    try:
        raw = json.loads(USAGE.read_text())
    except ValueError:
        return {"pct": None, "resets_at": None, "age_s": None, "source": "corrupt", "fresh": False}
    written = _parse(raw.get("_written", ""))
    age = (_now() - written).total_seconds() if written else None
    data = raw.get("data", raw)
    pct = _dig(data, "rate_limits.five_hour.used_percentage", "rate_limits.five_hour.utilization",
               "rateLimits.fiveHour.usedPercentage", "five_hour.used_percentage", "pct", "used_percentage")
    resets = _dig(data, "rate_limits.five_hour.resets_at", "rateLimits.fiveHour.resetsAt",
                  "five_hour.resets_at", "resets_at")
    if isinstance(resets, (int, float)):
        resets_dt = dt.datetime.fromtimestamp(resets if resets < 1e12 else resets / 1000, P)
    else:
        resets_dt = _parse(str(resets)) if resets else None
    try:
        pct = float(pct) if pct is not None else None
        if pct is not None and pct <= 1.0 and "utilization" in json.dumps(data):
            pct *= 100
    except (TypeError, ValueError):
        pct = None
    week = _dig(data, "rate_limits.seven_day.used_percentage")
    week_reset = _dig(data, "rate_limits.seven_day.resets_at")
    try:
        week = float(week) if week is not None else None
    except (TypeError, ValueError):
        week = None
    week_reset_dt = (dt.datetime.fromtimestamp(week_reset, P) if isinstance(week_reset, (int, float))
                     else (_parse(str(week_reset)) if week_reset else None))
    return {"pct": pct, "resets_at": resets_dt, "age_s": age, "source": raw.get("_source", "statusline"),
            "fresh": age is not None and age < USAGE_STALE_S and pct is not None,
            "week_pct": week, "week_resets_at": week_reset_dt}


def weekly_ceiling_hit(u: dict, model: str) -> bool:
    """True when the weekly no-new-work ceiling should refuse a call on `model`. A reading counts
    when it is fresh or at most WEEKLY_STALE_S old, and its window has not already reset. Haiku
    is exempt."""
    pct = u.get("week_pct")
    if pct is None or pct < WEEKLY_NO_NEW_RUNS:
        return False
    age = u.get("age_s")
    if not (u.get("fresh") or (age is not None and age <= WEEKLY_STALE_S)):
        return False
    when = u.get("week_resets_at")
    if isinstance(when, dt.datetime) and when <= _now():
        return False
    return not (model or "").lower().startswith(("haiku", "claude-haiku"))
