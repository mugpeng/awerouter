"""Format auto-detection — port of rtk pipe_cmd.rs auto_detect_filter
(via 9router autodetect.js). Detection order is part of the design: more
specific formats (git, build tools) win over generic file lists, which win
over generic truncation.
"""

from __future__ import annotations

import re

from awerouter.rtk.constants import (
    DETECT_WINDOW,
    READ_NUMBERED_MIN_HIT_RATIO,
    SMART_TRUNCATE_MIN_LINES,
)
from awerouter.rtk.filters import (
    READ_NUMBERED_LINE_RE,
    SEARCH_LIST_HEADER_RE,
    build_output,
    dedup_log,
    find,
    git_diff,
    git_log,
    git_status,
    grep,
    ls,
    read_numbered,
    search_list,
    smart_truncate,
    tree,
)

_RE_GIT_LOG = re.compile(r"^[*|/\\ ]*commit [0-9a-f]{7,40}$", re.MULTILINE)
_RE_GIT_DIFF = re.compile(r"^diff --git ", re.MULTILINE)
_RE_GIT_DIFF_HUNK = re.compile(r"^@@ ", re.MULTILINE)
_RE_GIT_STATUS = re.compile(
    r"^On branch |^nothing to commit|^Changes (not |to be )|^Untracked files:", re.MULTILINE
)
_RE_PORCELAIN = re.compile(r"^[ MADRCU?!][ MADRCU?!] \S", re.MULTILINE)
_RE_BUILD_OUTPUT = re.compile(
    r"^(npm (warn|error|ERR!)|yarn (warn|error)|\s*Compiling\s+\S+|\s*Downloading\s+\S+"
    r"|added \d+ package|\[ERROR\]|BUILD (SUCCESS|FAILED)|\s*Finished\s+"
    r"|Successfully (installed|built)|ERROR:)",
    re.IGNORECASE | re.MULTILINE,
)
_RE_TREE_GLYPH = re.compile(r"[├└]──|│  ")
_RE_LS_ROW = re.compile(r"^[-dlbcps][rwx-]{9}", re.MULTILINE)
_RE_LS_TOTAL = re.compile(r"^total \d+$", re.MULTILINE)
_RE_DRIVE_LETTER = re.compile(r"^[A-Za-z]:[\\/]")
_RE_LINE_NUM = re.compile(r"^\d+$")


def detect_filter(text: str):
    """Pick a filter for this tool output, or None to leave it untouched."""
    head = text[:DETECT_WINDOW]

    if _RE_GIT_LOG.search(head):
        return git_log
    if _RE_GIT_DIFF.search(head) or _RE_GIT_DIFF_HUNK.search(head):
        return git_diff
    if _RE_GIT_STATUS.search(head):
        return git_status

    # build output BEFORE the porcelain check: cargo's "Compiling" lines would
    # otherwise be misread as git-status porcelain
    if _RE_BUILD_OUTPUT.search(head):
        return build_output

    if _is_mostly_porcelain(head):
        return git_status

    lines = head.split("\n")
    non_empty = [l for l in lines if l.strip()]

    # Rust grep rule: any of the first 5 non-empty lines is "file:number:content"
    if any(_is_grep_line(l) for l in non_empty[:5]):
        return grep

    # Rust find rule: ALL non-empty lines path-like (no ':'), >= 3 lines
    if len(non_empty) >= 3 and all(_is_path_like(l) for l in non_empty):
        return find

    if _RE_TREE_GLYPH.search(head):
        return tree

    # ls -la: "total N" header, or >= 3 rows starting with a perms string
    if _RE_LS_TOTAL.search(head) or len(_RE_LS_ROW.findall(head)) >= 3:
        return ls

    if SEARCH_LIST_HEADER_RE.match(head):  # header must be the first line
        return search_list

    # line-numbered file dumps ("  N|content") — only when many lines match.
    # Line count is over the FULL text: the 1024-char window holds at most a
    # few dozen lines, so gating on window lines made this branch unreachable.
    if len(text.split("\n")) >= SMART_TRUNCATE_MIN_LINES and _is_line_numbered(lines):
        return read_numbered

    # generic multi-line noise with duplicates
    if len(non_empty) >= 5:
        return dedup_log

    # last resort: big blob with no structure
    if len(text.split("\n")) >= SMART_TRUNCATE_MIN_LINES:
        return smart_truncate

    return None


def _is_grep_line(line: str) -> bool:
    # Rust: splitn(3, ':') -> parts.len() == 3 && parts[1] parses as usize
    first = line.find(":")
    if first == -1:
        return False
    second = line.find(":", first + 1)
    if second == -1:
        return False
    return _RE_LINE_NUM.fullmatch(line[first + 1:second]) is not None


def _is_path_like(line: str) -> bool:
    t = line.strip()
    if not t:
        return False
    # a drive-letter prefix marks a Windows absolute path ("C:\a\b"), so the
    # whole line counts as path-like; a trailing ":10" (grep suffix) is tolerated
    if _RE_DRIVE_LETTER.match(t):
        return True
    if ":" in t:
        return False
    return t.startswith(".") or t.startswith("/") or "/" in t


def _is_mostly_porcelain(head: str) -> bool:
    lines = [l for l in head.split("\n") if l.strip()]
    if len(lines) < 3:
        return False
    hits = sum(1 for l in lines if _RE_PORCELAIN.match(l))
    return hits / len(lines) >= 0.6


def _is_line_numbered(lines: list) -> bool:
    hits = 0
    non_empty = 0
    for l in lines[:100]:
        if not l:  # only fully empty lines are skipped, mirroring upstream
            continue
        non_empty += 1
        if READ_NUMBERED_LINE_RE.match(l):
            hits += 1
    if non_empty < 5:
        return False
    return hits / non_empty >= READ_NUMBERED_MIN_HIT_RATIO
