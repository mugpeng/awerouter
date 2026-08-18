"""RTK port constants (mirror the Rust defaults rtk uses and 9router carried over).

Sizes are measured in len(str) code points. The upstream JS port used UTF-16
units and Rust used bytes; the guards are heuristic thresholds, so the small
divergence for CJK content is acceptable.
"""

# Skip blobs below this many chars (compressing tiny output only adds noise).
MIN_COMPRESS_SIZE = 500
# Never touch blobs above this size (matches rtk's RAW_CAP).
RAW_CAP = 10 * 1024 * 1024
# detect_filter() only peeks at this prefix.
DETECT_WINDOW = 1024

GIT_DIFF_HUNK_MAX_LINES = 100     # git-diff: per-hunk line cap
# Upstream caps git-diff at 500 total lines, but output must stay below
# SMART_TRUNCATE_MIN_LINES: compacted diffs are resent every turn, and a
# 250+ line summary would re-enter the generic smart-truncate fallback on
# the next pass — losing more data and breaking provider cache prefixes.
GIT_DIFF_MAX_LINES = 240
GIT_LOG_MAX_LINES = 200           # git-log: line cap
DEDUP_LINE_MAX = 2000             # dedup-log: truncation cap

# grep/find caps (rtk pipe_cmd.rs parity)
GREP_PER_FILE_MAX = 10
FIND_PER_DIR_MAX = 10
FIND_TOTAL_DIR_MAX = 20

# git-status caps (rtk config::limits())
STATUS_MAX_FILES = 10
STATUS_MAX_UNTRACKED = 10

# ls compact_ls (rtk src/cmds/system/ls.rs)
LS_EXT_SUMMARY_TOP = 5
LS_NOISE_DIRS = [
    "node_modules", ".git", "target", "__pycache__",
    ".next", "dist", "build", ".cache", ".turbo",
    ".vercel", ".pytest_cache", ".mypy_cache", ".tox",
    ".venv", "venv",
    "env",  # Python legacy virtualenv; .env (dotenv) intentionally excluded
    "coverage", ".nyc_output", ".DS_Store", "Thumbs.db",
    ".idea", ".vscode", ".vs", "*.egg-info", ".eggs",
]

TREE_MAX_LINES = 200

# Cursor Glob "Result of search in '...' (total N files):" lists
SEARCH_LIST_PER_DIR_MAX = 10
SEARCH_LIST_TOTAL_DIR_MAX = 20

# smart truncate (rtk filter.rs smart_truncate fallback)
SMART_TRUNCATE_HEAD = 120         # lines kept from the top
SMART_TRUNCATE_TAIL = 60          # lines kept from the bottom
SMART_TRUNCATE_MIN_LINES = 250    # only kick in above this
# Cap on "skeleton" lines (signatures/imports) kept from the truncated
# middle, ported from rtk Rust filter.rs. Chosen so a truncated result
# (head + marker + skeleton + tail) stays below SMART_TRUNCATE_MIN_LINES
# — a 250+ line output would re-enter truncation on the next pass.
SMART_TRUNCATE_STRUCT_MAX = 60

# read-numbered ("  N|content" file dumps, e.g. Cursor read_file)
READ_NUMBERED_MIN_HIT_RATIO = 0.7
