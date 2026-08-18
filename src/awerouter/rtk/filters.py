"""The 12 RTK compression filters — faithful Python port of 9router's JS port
(open-sse/rtk/filters/) of the rtk Rust filters. Every function is a pure
text -> text transform; upstream file references kept per filter.

Filters that cannot confidently compress return their input unchanged.
"""

from __future__ import annotations

import re

from awerouter.rtk.constants import (
    DEDUP_LINE_MAX,
    FIND_PER_DIR_MAX,
    FIND_TOTAL_DIR_MAX,
    GIT_DIFF_HUNK_MAX_LINES,
    GIT_LOG_MAX_LINES,
    GREP_PER_FILE_MAX,
    LS_EXT_SUMMARY_TOP,
    LS_NOISE_DIRS,
    SEARCH_LIST_PER_DIR_MAX,
    SEARCH_LIST_TOTAL_DIR_MAX,
    SMART_TRUNCATE_HEAD,
    SMART_TRUNCATE_MIN_LINES,
    SMART_TRUNCATE_TAIL,
    STATUS_MAX_FILES,
    STATUS_MAX_UNTRACKED,
    TREE_MAX_LINES,
)


# ---------------------------------------------------------------------------
# git-diff — port of Rust git::compact_diff (src/cmds/git/git.rs L325-413)
# ---------------------------------------------------------------------------

def git_diff(diff: str, max_lines: int = 500) -> str:
    result: list[str] = []
    current_file = ""
    added = 0
    removed = 0
    in_hunk = False
    hunk_shown = 0
    hunk_skipped = 0
    was_truncated = False

    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            if hunk_skipped > 0:
                result.append(f"  ... ({hunk_skipped} lines truncated)")
                was_truncated = True
                hunk_skipped = 0
            if current_file and (added > 0 or removed > 0):
                result.append(f"  +{added} -{removed}")
            parts = line.split(" b/")
            current_file = " b/".join(parts[1:]) if len(parts) > 1 else "unknown"
            result.append(f"\n{current_file}")
            added = 0
            removed = 0
            in_hunk = False
            hunk_shown = 0
        elif line.startswith("@@"):
            if hunk_skipped > 0:
                result.append(f"  ... ({hunk_skipped} lines truncated)")
                was_truncated = True
                hunk_skipped = 0
            in_hunk = True
            hunk_shown = 0
            result.append(f"  {line}")
        elif in_hunk:
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
                if hunk_shown < GIT_DIFF_HUNK_MAX_LINES:
                    result.append(f"  {line}")
                    hunk_shown += 1
                else:
                    hunk_skipped += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
                if hunk_shown < GIT_DIFF_HUNK_MAX_LINES:
                    result.append(f"  {line}")
                    hunk_shown += 1
                else:
                    hunk_skipped += 1
            elif hunk_shown < GIT_DIFF_HUNK_MAX_LINES and not line.startswith("\\"):
                # context lines only survive once the hunk has content
                if hunk_shown > 0:
                    result.append(f"  {line}")
                    hunk_shown += 1

        if len(result) >= max_lines:
            result.append("\n... (more changes truncated)")
            was_truncated = True
            break

    if hunk_skipped > 0:
        result.append(f"  ... ({hunk_skipped} lines truncated)")
        was_truncated = True
    if current_file and (added > 0 or removed > 0):
        result.append(f"  +{added} -{removed}")
    if was_truncated:
        result.append("[full diff: rtk git diff --no-compact]")

    return "\n".join(result)


git_diff.filter_name = "git-diff"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# git-status — port of git::format_status_output (git.rs L619-730)
# ---------------------------------------------------------------------------

_STATUS_BRANCH_RE = re.compile(r"^On branch (\S+)")
_STATUS_LONG_RE = re.compile(r"^\s*(modified|new file|deleted|renamed|both modified):\s+(.+)$")
_STATUS_PORCELAIN_RE = re.compile(r"^[ MADRCU?!][ MADRCU?!] ")


def git_status(input: str) -> str:
    lines = input.split("\n")
    if not lines or (len(lines) == 1 and not lines[0].strip()):
        return "Clean working tree"

    branch = ""
    staged_files: list[str] = []
    modified_files: list[str] = []
    untracked_files: list[str] = []
    staged = modified = untracked = conflicts = 0

    for raw in lines:
        if not raw.strip():
            continue

        m = _STATUS_BRANCH_RE.match(raw)
        if m:  # long-form "On branch x"
            branch = m.group(1)
            continue

        if raw.startswith("##"):  # porcelain branch header
            branch = re.sub(r"^##\s*", "", raw)
            continue

        if len(raw) >= 3 and _STATUS_PORCELAIN_RE.match(raw):
            x, y, file = raw[0], raw[1], raw[3:]
            if raw[:2] == "??":
                untracked += 1
                untracked_files.append(file)
                continue
            if x in "MADRC":
                staged += 1
                staged_files.append(file)
            elif x == "U":
                conflicts += 1
            if y in ("M", "D"):
                modified += 1
                modified_files.append(file)
            continue

        m = _STATUS_LONG_RE.match(raw)  # "modified:   path", "new file:   path", ...
        if m:
            kind, path = m.group(1), m.group(2).strip()
            if kind == "both modified":
                conflicts += 1
            elif kind in ("modified", "deleted"):
                modified += 1
                modified_files.append(path)
            elif kind in ("new file", "renamed"):
                staged += 1
                staged_files.append(path)

    out = ""
    if branch:
        out += f"* {branch}\n"
    if staged > 0:
        out += f"+ Staged: {staged} files\n"
        for f in staged_files[:STATUS_MAX_FILES]:
            out += f"   {f}\n"
        if len(staged_files) > STATUS_MAX_FILES:
            out += f"   ... +{len(staged_files) - STATUS_MAX_FILES} more\n"
    if modified > 0:
        out += f"~ Modified: {modified} files\n"
        for f in modified_files[:STATUS_MAX_FILES]:
            out += f"   {f}\n"
        if len(modified_files) > STATUS_MAX_FILES:
            out += f"   ... +{len(modified_files) - STATUS_MAX_FILES} more\n"
    if untracked > 0:
        out += f"? Untracked: {untracked} files\n"
        for f in untracked_files[:STATUS_MAX_UNTRACKED]:
            out += f"   {f}\n"
        if len(untracked_files) > STATUS_MAX_UNTRACKED:
            out += f"   ... +{len(untracked_files) - STATUS_MAX_UNTRACKED} more\n"
    if conflicts > 0:
        out += f"conflicts: {conflicts} files\n"
    if staged == modified == untracked == conflicts == 0:
        out += "clean — nothing to commit\n"

    return re.sub(r"\n+$", "", out)


git_status.filter_name = "git-status"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# git-log — keeps commit headers, subjects, Author/Date, stat summaries;
# drops bodies, graph decoration, embedded diffs
# ---------------------------------------------------------------------------

_LOG_COMMIT_RE = re.compile(r"^commit [0-9a-f]{7,40}$", re.IGNORECASE)
_LOG_GRAPH_COMMIT_RE = re.compile(r"^[*|/\\ ]+commit [0-9a-f]{7,40}", re.IGNORECASE)
_LOG_AUTHOR_DATE_RE = re.compile(r"^[*|/\\ ]*(Author|Date):", re.IGNORECASE)
_LOG_SUBJECT_RE = re.compile(r"^[*|/\\ ]*    \S")
_LOG_STAT_RE = re.compile(r"^\d+ file\w* changed")
_LOG_DIFF_RE = re.compile(r"^diff --git ")
_LOG_GRAPH_SHA_RE = re.compile(r"^[*|/\\ ]+([0-9a-f]{7,40}\s+.+)", re.IGNORECASE)
_LOG_ONELINE_RE = re.compile(r"^[0-9a-f]{7,40}\s+", re.IGNORECASE)
_LOG_GRAPH_ONLY_RE = re.compile(r"^[*|/\\ ]+$")


def git_log(text: str, max_lines: int = GIT_LOG_MAX_LINES) -> str:
    if not text:
        return ""

    out: list[str] = []
    skipped = 0
    in_commit = False
    subject_seen = False

    def push(line: str) -> None:
        nonlocal skipped
        if len(out) < max_lines:
            out.append(line)
        else:
            skipped += 1

    for raw in text.split("\n"):
        line = raw.rstrip()
        trimmed = line.strip()

        if _LOG_COMMIT_RE.fullmatch(trimmed) or _LOG_GRAPH_COMMIT_RE.match(trimmed):
            in_commit = True
            subject_seen = False
            push(line)
            continue

        if in_commit:
            if _LOG_AUTHOR_DATE_RE.match(trimmed):
                push(trimmed)
                continue
            if trimmed == "":
                continue
            if not subject_seen and _LOG_SUBJECT_RE.match(line):
                push(f"  Subject: {trimmed}")
                subject_seen = True
                continue
            if _LOG_STAT_RE.match(trimmed):
                push(f"  {trimmed}")
                continue
            if _LOG_DIFF_RE.match(trimmed):
                push("  ... diff body omitted")
                continue
            continue  # commit body — drop

        # --oneline / --graph modes
        m = _LOG_GRAPH_SHA_RE.match(trimmed)
        if m:
            push(m.group(1))
            continue
        if _LOG_ONELINE_RE.match(trimmed):
            push(trimmed)
            continue
        if _LOG_GRAPH_ONLY_RE.match(trimmed) and re.search(r"[*|/\\]", trimmed):
            continue

        push(trimmed)

    if skipped > 0:
        out.append(f"... ({skipped} more lines)")

    result = "\n".join(out)
    if not result:
        return text
    if len(result) > len(text):
        return text
    return result


git_log.filter_name = "git-log"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# build-output — keeps errors/warnings/summary, counts Compiling/Downloading
# ---------------------------------------------------------------------------

_CARGO_ERR_CONT_RE = re.compile(r"^\s*(-->|\||\d+\s*\||=)")
_DEPRECATION_KEEP = 3


def build_output(input: str) -> str:
    lines = input.split("\n")
    errors: list[str] = []
    warnings: list[str] = []
    deprecations: list[str] = []
    summary: list[str] = []
    compiling = downloading = 0
    in_cargo_error = False

    for line in lines:
        trimmed = line.strip()

        if in_cargo_error:  # keep cargo error blocks verbatim
            if not trimmed:
                in_cargo_error = False
                continue
            if _CARGO_ERR_CONT_RE.match(line):
                errors.append(line)
                continue
            in_cargo_error = False

        if not trimmed:
            continue

        if re.match(r"^npm (ERR!|error)", trimmed, re.I) or re.match(r"^yarn error", trimmed, re.I):
            errors.append(line)
            continue
        if re.match(r"^npm warn deprecated", trimmed):
            deprecations.append(line)
            continue
        if re.match(r"^npm warn", trimmed) or re.match(r"^yarn warn", trimmed):
            warnings.append(line)
            continue
        if re.match(r"^error(\[|:)", trimmed, re.I) or trimmed.startswith("error -->"):
            errors.append(line)
            in_cargo_error = True
            continue
        if re.match(r"^warning(\[|:)", trimmed, re.I) or trimmed.startswith("warning -->"):
            warnings.append(line)
            in_cargo_error = True
            continue
        if re.match(r"^ERROR:", trimmed, re.I):
            errors.append(line)
            continue
        if re.match(r"^\[ERROR\]", trimmed, re.I) or re.match(r"^BUILD FAILED", trimmed, re.I):
            errors.append(line)
            continue
        if re.match(r"^\[WARNING\]", trimmed):
            warnings.append(line)
            continue
        if re.match(r"^\s*Compiling\s+\S+", trimmed, re.I):
            compiling += 1
            continue
        if re.match(r"^\s*Downloading\s+\S+", trimmed, re.I) or re.match(r"^Fetching\s+", trimmed):
            downloading += 1
            continue
        if (
            re.match(r"^(added|removed|changed|audited|installed)\s+\d+\s+package", trimmed, re.I)
            or re.match(r"^\s*Finished\s+", trimmed)
            or re.match(r"^BUILD SUCCESS", trimmed, re.I)
            or re.match(r"^\d+\s+(vulnerabilities|packages?|warnings?|errors?)", trimmed, re.I)
            or re.match(r"^Successfully (installed|built)", trimmed)
            or re.match(r"^To address .* issues", trimmed, re.I)
            or re.match(r"^Run `npm (audit|fund)`", trimmed)
            or re.search(r"packages are looking for funding", trimmed)
        ):
            summary.append(line)
            continue

    out = ""
    for d in deprecations[:_DEPRECATION_KEEP]:
        out += f"{d}\n"
    if len(deprecations) > _DEPRECATION_KEEP:
        out += f"... +{len(deprecations) - _DEPRECATION_KEEP} more deprecated packages\n"
    if compiling > 0:
        out += f"Compiled {compiling} packages\n"
    if downloading > 0:
        out += f"Downloaded {downloading} packages\n"
    for e in errors:
        out += f"{e}\n"
    for w in warnings[:5]:
        out += f"{w}\n"
    if len(warnings) > 5:
        out += f"... +{len(warnings) - 5} more warnings\n"
    for s in summary:
        out += f"{s}\n"

    return re.sub(r"\n+$", "", out) or input


build_output.filter_name = "build-output"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# grep — port of grep_wrapper (pipe_cmd.rs L50-86): "file:lineno:content"
# ---------------------------------------------------------------------------

def grep(input: str) -> str:
    by_file: dict[str, list] = {}
    total = 0

    for line in input.split("\n"):
        first = line.find(":")
        if first == -1:
            continue
        second = line.find(":", first + 1)
        if second == -1:
            continue
        file = line[:first]
        line_num = line[first + 1:second]
        content = line[second + 1:]
        if not line_num.isdigit():
            continue
        total += 1
        by_file.setdefault(file, []).append((line_num, content))

    if total == 0:
        return input

    files = sorted(by_file)
    out = f"{total} matches in {len(files)}F:\n\n"
    for file in files:
        matches = by_file[file]
        out += f"[file] {file} ({len(matches)}):\n"
        for line_num, content in matches[:GREP_PER_FILE_MAX]:
            out += f"  {line_num.rjust(4)}: {content.strip()}\n"
        if len(matches) > GREP_PER_FILE_MAX:
            out += f"  +{len(matches) - GREP_PER_FILE_MAX}\n"
        out += "\n"
    return out


grep.filter_name = "grep"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# find — port of find_wrapper (pipe_cmd.rs L89-128): group by parent dir
# ---------------------------------------------------------------------------

def find(input: str) -> str:
    lines = [l for l in input.split("\n") if l.strip()]
    if not lines:
        return input

    by_dir: dict[str, list[str]] = {}
    for path in lines:
        last_sep = max(path.rfind("/"), path.rfind("\\"))
        if last_sep == -1:
            d, basename = ".", path
        else:
            d = path[:last_sep] or "/"
            basename = path[last_sep + 1:]
        by_dir.setdefault(d, []).append(basename)

    dirs = sorted(by_dir)
    out = f"{len(lines)} files in {len(dirs)} dirs:\n\n"
    for d in dirs[:FIND_TOTAL_DIR_MAX]:
        files = by_dir[d]
        dir_label = d.replace("\\", "/")  # windows separators -> forward slash
        out += f"{dir_label}/  ({len(files)})\n"
        for f in files[:FIND_PER_DIR_MAX]:
            out += f"  {f}\n"
        if len(files) > FIND_PER_DIR_MAX:
            out += f"  +{len(files) - FIND_PER_DIR_MAX}\n"
    if len(dirs) > FIND_TOTAL_DIR_MAX:
        out += f"\n+{len(dirs) - FIND_TOTAL_DIR_MAX} more dirs\n"
    return out


find.filter_name = "find"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# dedup-log — collapse consecutive duplicates, cap hard at 2000 lines
# ---------------------------------------------------------------------------

def dedup_log(input: str) -> str:
    out: list[str] = []
    prev = None
    run_count = 0
    blank_streak = 0

    def flush_run() -> None:
        if prev is not None and run_count > 1:
            out.append(f"  ... ({run_count - 1} duplicate lines)")

    for line in input.split("\n"):
        if line.strip() == "":
            if blank_streak < 1:
                out.append(line)
            blank_streak += 1
            flush_run()
            prev = None
            run_count = 0
            continue
        blank_streak = 0
        if line == prev:
            run_count += 1
            continue
        flush_run()
        out.append(line)
        prev = line
        run_count = 1
        if len(out) >= DEDUP_LINE_MAX:
            out.append(f"... (truncated at {DEDUP_LINE_MAX} lines)")
            return "\n".join(out)
    flush_run()
    return "\n".join(out)


dedup_log.filter_name = "dedup-log"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# ls — port of compact_ls (rtk src/cmds/system/ls.rs L154-232)
# ---------------------------------------------------------------------------

_LS_DATE_RE = re.compile(
    r"\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+(\d{4}|\d{2}:\d{2})\s+"
)
_LS_INT_RE = re.compile(r"(0|[1-9]\d*)")


def _human_size(bytes_: int) -> str:
    if bytes_ >= 1_048_576:
        return f"{bytes_ / 1_048_576:.1f}M"
    if bytes_ >= 1024:
        return f"{bytes_ / 1024:.1f}K"
    return f"{bytes_}B"


def _parse_ls_line(line: str):
    m = _LS_DATE_RE.search(line)
    if not m:
        return None
    name = line[m.end():]
    before = [p for p in re.split(r"\s+", line[:m.start()]) if p]
    if len(before) < 4:
        return None

    perms = before[0]
    file_type = perms[0]

    size = 0
    for part in reversed(before):
        if _LS_INT_RE.fullmatch(part):
            size = int(part)
            break
    return file_type, size, name


def ls(input: str) -> str:
    dirs: list[str] = []
    files: list[tuple[str, str]] = []
    by_ext: dict[str, int] = {}

    for line in input.split("\n"):
        if line.startswith("total ") or not line:
            continue
        parsed = _parse_ls_line(line)
        if not parsed:
            continue
        file_type, size, name = parsed
        if name in (".", ".."):
            continue
        if name in LS_NOISE_DIRS:
            continue

        if file_type == "d":
            dirs.append(name)
        elif file_type in ("-", "l"):
            dot = name.rfind(".")
            ext = name[dot:] if dot > 0 else "no ext"
            by_ext[ext] = by_ext.get(ext, 0) + 1
            files.append((name, _human_size(size)))

    if not dirs and not files:
        return input

    out = ""
    for d in dirs:
        out += f"{d}/\n"
    for name, size in files:
        out += f"{name}  {size}\n"

    summary = f"\nSummary: {len(files)} files, {len(dirs)} dirs"
    if by_ext:
        exts = sorted(by_ext.items(), key=lambda kv: -kv[1])
        parts = [f"{c} {e}" for e, c in exts[:LS_EXT_SUMMARY_TOP]]
        summary += f" ({', '.join(parts)}"
        if len(exts) > LS_EXT_SUMMARY_TOP:
            summary += f", +{len(exts) - LS_EXT_SUMMARY_TOP} more"
        summary += ")"

    return out + summary


ls.filter_name = "ls"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# tree — port of filter_tree_output (tree.rs L65-94)
# ---------------------------------------------------------------------------

def tree(input: str) -> str:
    lines = input.split("\n")
    if not lines:
        return input

    filtered: list[str] = []
    for line in lines:
        if "director" in line and "file" in line:  # "5 directories, 23 files"
            continue
        if line.strip() == "" and not filtered:
            continue
        filtered.append(line)

    while filtered and filtered[-1].strip() == "":
        filtered.pop()

    if len(filtered) > TREE_MAX_LINES:
        cut = len(filtered) - TREE_MAX_LINES
        return "\n".join(filtered[:TREE_MAX_LINES]) + f"\n... +{cut} more lines"
    return "\n".join(filtered)


tree.filter_name = "tree"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# smart-truncate — keep head+tail, replace the middle
# ---------------------------------------------------------------------------

def smart_truncate(input: str) -> str:
    lines = input.split("\n")
    if len(lines) < SMART_TRUNCATE_MIN_LINES:
        return input

    head = lines[:SMART_TRUNCATE_HEAD]
    tail = lines[len(lines) - SMART_TRUNCATE_TAIL:]
    cut = len(lines) - len(head) - len(tail)
    return "\n".join([*head, f"... +{cut} lines truncated", *tail])


smart_truncate.filter_name = "smart-truncate"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# read-numbered — "  N|content" dumps (e.g. Cursor read_file): head+tail
# ---------------------------------------------------------------------------

# line-numbered file dumps. Cursor read_file uses "  N|content"; opencode
# read.ts emits "N: content"; Claude Code style is "  N→content". The ": "
# variant requires the space so clock times like "10:30" don't match.
READ_NUMBERED_LINE_RE = re.compile(r"^\s*\d+(?:\||→|│|: )")


def read_numbered(input: str) -> str:
    lines = input.split("\n")
    if len(lines) < SMART_TRUNCATE_MIN_LINES:
        return input

    head = lines[:SMART_TRUNCATE_HEAD]
    tail = lines[len(lines) - SMART_TRUNCATE_TAIL:]
    cut = len(lines) - len(head) - len(tail)
    return "\n".join([*head, f"... +{cut} lines truncated (file continues)", *tail])


read_numbered.filter_name = "read-numbered"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# search-list — Cursor Glob "Result of search in '...' (total N files):"
# ---------------------------------------------------------------------------

SEARCH_LIST_HEADER_RE = re.compile(r"^Result of search in '[^']*' \(total (\d+) files?\):")


def search_list(input: str) -> str:
    lines = input.split("\n")
    if not lines:
        return input

    header = lines[0] or ""
    paths = []
    for raw in lines[1:]:
        t = raw.strip()
        if t.startswith("- "):
            paths.append(t[2:])
    if not paths:
        return input

    by_dir: dict[str, list[str]] = {}
    for p in paths:
        slash = p.rfind("/")
        d = "." if slash == -1 else (p[:slash] or "/")
        name = p if slash == -1 else p[slash + 1:]
        by_dir.setdefault(d, []).append(name)

    dirs = sorted(by_dir)
    out = f"{header}\n{len(paths)} files in {len(dirs)} dirs:\n\n"
    for d in dirs[:SEARCH_LIST_TOTAL_DIR_MAX]:
        names = by_dir[d]
        out += f"{d}/ ({len(names)}):\n"
        for n in names[:SEARCH_LIST_PER_DIR_MAX]:
            out += f"  {n}\n"
        if len(names) > SEARCH_LIST_PER_DIR_MAX:
            out += f"  +{len(names) - SEARCH_LIST_PER_DIR_MAX}\n"
        out += "\n"
    if len(dirs) > SEARCH_LIST_TOTAL_DIR_MAX:
        out += f"+{len(dirs) - SEARCH_LIST_TOTAL_DIR_MAX} more dirs\n"

    return re.sub(r"\n+$", "", out)


search_list.filter_name = "search-list"  # type: ignore[attr-defined]
