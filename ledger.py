#!/usr/bin/env python3
"""Read a session ledger: one entry per Claude Code session, fenced by HTML comments.

    <!-- session: <id> -->
    ## <id>, <heading>
    ...
    <!-- end: <id> -->

Entries are rewritten in place, so Dreaming diffs them entry by entry rather than trusting
the file's mtime. Part of dreaming (MIT licence).
"""

from __future__ import annotations

import re

ENTRY = re.compile(r"<!-- session: (\S+) -->\n.*?<!-- end: \1 -->\n?", re.S)


def load_entries(text: str) -> list:
    out = []
    for m in ENTRY.finditer(text or ""):
        body = m.group(0)
        heading = next((ln[3:] for ln in body.splitlines() if ln.startswith("## ")), m.group(1)[:8])
        out.append({"sid": m.group(1), "start": m.start(), "end": m.end(), "text": body,
                    "heading": heading})
    return out
