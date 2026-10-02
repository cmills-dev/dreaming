#!/usr/bin/env python3
"""Frontmatter read and write for Markdown notes. Stdlib only, Python 3.9.

    import frontmatter
    frontmatter.frontmatter_text(text)                    # dict of str or list[str]
    frontmatter.edit_frontmatter_text(text, {"k": "v"})   # new text, body byte for byte

A small YAML subset: `key: value`, quoted values (folded when they run over a line),
`[a, b]` and `- item` lists, and `|` or `>` blocks folded to one line. Nothing here raises:
a header that never closes reads as no frontmatter. Part of dreaming (MIT licence).
"""

from __future__ import annotations

import re
from pathlib import Path

_FM = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_BLOCK_SCALAR = {"|", ">", "|-", ">-", "|+", ">+"}
_LIST_ITEM = re.compile(r"^\s*-\s+(.*)$")


def _read_list(lines: list[str], i: int) -> tuple[list[str] | None, int]:
    """Consume `- item` lines starting at index i. None when the next line is not one."""
    items: list[str] = []
    while i < len(lines):
        if lines[i].strip() == "---":
            break
        m = _LIST_ITEM.match(lines[i])
        if not m:
            break
        value = m.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        items.append(value)
        i += 1
    return (items or None), i


def _close_at(s: str, quote: str, start: int = 0) -> int:
    """Index of the quote that closes a quoted value, or -1. Only a double-quoted YAML
    scalar has backslash escapes, and `\\"` inside one is a quote in the value, not the
    end of it: counting it as the end would fold every later key into the value."""
    if quote != '"':
        return s.find(quote, start)
    i = start
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return i
        i += 1
    return -1


def _ends_quoted(piece: str, quote: str) -> bool:
    """Does this line end the quoted value: a closing quote last, and not an escaped one."""
    if not piece.endswith(quote):
        return False
    if quote != '"':
        return True
    run = len(piece) - 1 - len(piece[:-1].rstrip("\\"))
    return run % 2 == 0


def _unescape(value: str, quote: str) -> str:
    """The two escapes this vault's hand-written values use, `\\"` and `\\\\`."""
    return re.sub(r'\\(["\\])', r"\1", value) if quote == '"' else value


def _read_quoted(lines: list[str], i: int, quote: str) -> tuple[str, int, bool]:
    """Fold a quoted value that did not close on its own line.

    Folded to single spaces, the way a YAML flow scalar folds, so that every value this
    module hands out is still one line. The alternative is a newline inside a title,
    which every consumer here would then have to strip.
    """
    parts = [lines[i][lines[i].index(quote) + 1:]]
    j, closed = i + 1, False
    while j < len(lines):
        if lines[j].strip() == "---":
            break
        piece = lines[j].rstrip()
        if _ends_quoted(piece, quote):
            parts.append(piece[:-1])
            closed = True
            j += 1
            break
        parts.append(piece)
        j += 1
    return _unescape(" ".join(" ".join(parts).split()), quote), j, closed


def frontmatter_text(text: str, path: Path | None = None) -> dict:
    """The frontmatter of `text` as a dict of str or list[str]. {} when there is none."""
    def warn(line: int, reason: str) -> None:
        return None          # the standalone reader records nothing

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}          # no frontmatter is not a parse failure, it is a plain file

    data: dict = {}
    i, n = 1, len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "---":
            return data
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = _FM.match(line)
        if not m:
            warn(i + 1, "not a key, a list item or a continuation")
            i += 1
            continue
        key, raw = m.group(1), m.group(2).strip()

        if raw in _BLOCK_SCALAR:
            block, i = _read_block(lines, i + 1)
            data[key] = block
            continue
        if not raw:
            items, i2 = _read_list(lines, i + 1)
            data[key] = items if items is not None else ""
            i = i2 if items is not None else i + 1
            continue
        if raw.startswith("[") and raw.endswith("]"):
            data[key] = [x.strip().strip("\"'") for x in raw[1:-1].split(",") if x.strip()]
            i += 1
            continue
        quote = raw[0] if raw[0] in {'"', "'"} else ""
        if quote:
            # The value closes on this line when its closing quote is followed by nothing
            # or by a `# comment` (legal YAML). Find the closing quote first, then look at
            # what follows it: splitting on " #" before that would cut `"a # b"` in half,
            # and testing endswith(quote) alone let `"a" # note` fall into the multi-line
            # path and swallow every key after it.
            end = _close_at(raw, quote, 1)
            after = raw[end + 1:].strip() if end > 0 else None
            if end > 0 and (not after or after.startswith("#")):
                data[key] = _unescape(raw[1:end], quote)
                i += 1
                continue
            start = i
            value, i, closed = _read_quoted(lines, i, quote)
            data[key] = value
            if not closed:
                warn(start + 1, f"quoted value for `{key}` never closes")
            continue
        data[key] = raw.split(" #", 1)[0].rstrip()
        i += 1

    # No closing fence. Everything above came out of the body, so none of it is
    # frontmatter and returning it would be worse than returning nothing.
    warn(n, "frontmatter has no closing `---`")
    return {}


def _read_block(lines: list[str], i: int) -> tuple[str, int]:
    """A `|` or `>` block scalar: the indented lines under it, folded to one line."""
    parts: list[str] = []
    while i < len(lines) and lines[i].strip() != "---":
        if lines[i].strip() and not lines[i].startswith((" ", "\t")):
            break
        parts.append(lines[i].strip())
        i += 1
    return " ".join(" ".join(parts).split()), i


def _fm_value(value) -> str:
    value = " ".join(str(value).split())
    if value == "" or re.search(r":\s|\s#|^[\"'\[{>|*&!%@`-]", value) or value.endswith(":"):
        return '"' + value.replace('"', "'") + '"'
    return value


def edit_frontmatter_text(text: str, set_fields: dict | None = None, drop: tuple = ()) -> str | None:
    """`text` with top-level keys set, added or removed in its frontmatter, the body byte for
    byte. None when there is no frontmatter or it never closes. The one frontmatter writer.

    The lines are grouped into one block per top-level key, the key line plus every
    continuation line frontmatter_text() folds into its value (a quote over several lines, a
    `>` block, a list). Dropping or replacing only the key line would leave those orphaned."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    head = text[4:end].split("\n")
    blocks: list = []
    i = 0
    while i < len(head):
        m = _FM.match(head[i])
        j = _value_end(head, i, m.group(2).strip()) if m else i + 1
        blocks.append((m.group(1) if m else None, head[i:j]))
        i = j
    blocks = [b for b in blocks if b[0] not in drop]
    for key, value in (set_fields or {}).items():
        line = f"{key}: {_fm_value(value)}"
        for n, (k, _) in enumerate(blocks):
            if k == key:
                blocks[n] = (key, [line])
                break
        else:
            blocks.append((key, [line]))
    head = [ln for _, lines in blocks for ln in lines]
    return "---\n" + "\n".join(head) + text[end:]


def _value_end(head: list, i: int, raw: str) -> int:
    """The index after the last line of the value that starts on head[i], by the fold rules
    above: a quote runs to its closing quote, a `|`/`>` block or an empty value takes the
    indented and `- item` lines under it, and a plain value takes indented lines."""
    j = i + 1
    quote = raw[:1] if raw[:1] in {'"', "'"} else ""
    if quote:
        close = _close_at(raw, quote, 1)
        after = raw[close + 1:].strip() if close > 0 else None
        if close > 0 and (not after or after.startswith("#")):
            return j
        while j < len(head):
            j += 1
            if _ends_quoted(head[j - 1].rstrip(), quote):
                break
        return j
    while j < len(head) and (head[j].startswith((" ", "\t")) or (not raw and _LIST_ITEM.match(head[j]))):
        j += 1
    return j
