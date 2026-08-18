"""Tests for awerouter.rtk — ported from 9router's rtk test cases."""

from awerouter import rtk
from awerouter.rtk.apply import safe_apply
from awerouter.rtk.autodetect import detect_filter
from awerouter.rtk.filters import (
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


def _big_grep_output(files=2, per_file=30):
    lines = []
    for f in range(files):
        for i in range(per_file):
            lines.append(f"src/file{f}.py:{i * 5 + 1}:def helper_{f}_{i}()")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# safe_apply (fail-open)
# ---------------------------------------------------------------------------

class TestSafeApply:
    def test_exception_returns_original(self):
        def boom(_):
            raise RuntimeError("panic")
        boom.filter_name = "boom"
        assert safe_apply(boom, "raw") == "raw"

    def test_non_str_returns_original(self):
        def weird(_):
            return None
        assert safe_apply(weird, "raw") == "raw"

    def test_ok_passes_through(self):
        assert safe_apply(lambda t: t.upper(), "x") == "X"


# ---------------------------------------------------------------------------
# detect_filter — all 12 branches + none
# ---------------------------------------------------------------------------

class TestDetectFilter:
    def test_git_log(self):
        assert detect_filter("commit abc1234\nAuthor: A <a@x>\n    subject\n") is git_log

    def test_git_log_graph(self):
        assert detect_filter("* commit abc1234\n") is git_log

    def test_git_diff(self):
        assert detect_filter("diff --git a/x.py b/x.py\nindex 1..2\n") is git_diff

    def test_git_diff_hunk_only(self):
        assert detect_filter("@@ -1,2 +1,3 @@\n+new\n") is git_diff

    def test_git_status_long(self):
        assert detect_filter("On branch main\nnothing to commit, working tree clean\n") is git_status

    def test_git_status_porcelain(self):
        text = "\n".join([" M src/a.py", "M  src/b.py", "?? new.txt"])
        assert detect_filter(text) is git_status

    def test_build_output_beats_porcelain(self):
        # cargo "Compiling" lines must not be misread as git-status porcelain
        text = "\n".join(["   Compiling serde v1.0", "   Compiling rtk v0.42", "   Compiling clap v4"])
        assert detect_filter(text) is build_output

    def test_build_output_npm(self):
        assert detect_filter("npm warn deprecated left-pad@1.0\nadded 52 packages in 3s\n") is build_output

    def test_grep(self):
        assert detect_filter("src/a.py:10:def x()\nsrc/a.py:20:def y()\n") is grep

    def test_find(self):
        assert detect_filter("./a.py\n./b.py\n./c.py\n") is find

    def test_find_windows_drive_letter(self):
        text = "\n".join([r"C:\Users\me\proj\a.py", r"C:\Users\me\proj\b.py", r"C:\Users\me\proj\c.py"])
        assert detect_filter(text) is find

    def test_tree(self):
        assert detect_filter("proj\n├── src\n│   └── main.py\n└── README.md\n") is tree

    def test_ls(self):
        rows = "\n".join([
            "total 24",
            "-rw-r--r--  1 me  staff  220 Jan  3 10:00 a.py",
            "-rw-r--r--  1 me  staff  640 Jan  3 10:00 b.py",
            "drwxr-xr-x  4 me  staff  128 Jan  3 10:00 src",
        ])
        assert detect_filter(rows) is ls

    def test_search_list(self):
        text = "Result of search in '*.py' (total 3 files):\n- ./a.py\n- ./b.py\n- ./c.py\n"
        assert detect_filter(text) is search_list

    def test_read_numbered(self):
        # 250+ very short numbered lines must fit the 1024-char detect window
        text = "\n".join(["1|x"] * 260)
        assert detect_filter(text) is read_numbered

    def test_read_numbered_long_lines_outside_window(self):
        # realistic file dumps exceed the 1024-char window long before 250
        # lines fit in it — detection must use the full-text line count
        text = "\n".join(f"{i}|def function_{i}(arg1, arg2): return arg1 + arg2" for i in range(1, 301))
        assert detect_filter(text) is read_numbered
        assert len(text) > 1024

    def test_read_numbered_opencode_colon_format(self):
        # opencode read.ts: `${n}: ${line}`
        text = "\n".join(f"{i}: import module_{i} # some content" for i in range(1, 301))
        assert detect_filter(text) is read_numbered

    def test_read_numbered_claude_arrow_format(self):
        text = "\n".join(f"{i:>4}→import module_{i}" for i in range(1, 301))
        assert detect_filter(text) is read_numbered

    def test_clock_times_not_read_numbered(self):
        # "10:30" has no space after the colon and must not match the ":" variant
        text = "\n".join(f"10:{30+i%10}:0{i%10} event {i} happened" for i in range(300))
        assert detect_filter(text) is not read_numbered

    def test_indented_text_not_porcelain(self):
        # Claude Code read dumps pad line numbers with spaces; 3+ leading
        # spaces must not read as git-status porcelain
        text = "\n".join(f"{i:>6}→def fn_{i}(x): return x" for i in range(1, 400))
        assert detect_filter(text) is read_numbered

    def test_real_porcelain_still_detected(self):
        # both-slots-blank XY never occurs in real porcelain, so rejecting it
        # keeps every actual status line detectable
        text = "\n".join([" M src/a.py", "M  src/b.py", "?? new.txt", "D  gone.py"])
        assert detect_filter(text) is git_status

    def test_dedup_log(self):
        assert detect_filter("alpha\nbeta\ngamma\ndelta\nepsilon\n") is dedup_log

    def test_smart_truncate(self):
        # mostly-blank blob: not enough non-empty lines for dedup, but long
        assert detect_filter("x\n" + "\n" * 300) is smart_truncate

    def test_none(self):
        assert detect_filter("hello") is None

    def test_detect_window_limits_detection(self):
        # git-log marker beyond the 1024-char window is invisible
        assert detect_filter("x" * 1100 + "\ncommit abc1234\n") is not git_log


# ---------------------------------------------------------------------------
# filters — one behavior check each (ported expectations)
# ---------------------------------------------------------------------------

class TestFilters:
    def test_git_diff(self):
        text = (
            "diff --git a/x.py b/x.py\n"
            "@@ -1,2 +1,3 @@\n"
            " context line\n"
            "+added line\n"
            "-removed line\n"
        )
        out = git_diff(text)
        assert "x.py" in out
        assert "+1 -1" in out
        assert "added line" in out

    def test_git_diff_hunk_truncation_marker(self):
        changed = "\n".join(f"+line{i}" for i in range(150))
        text = f"diff --git a/x.py b/x.py\n@@ -1,100 +1,250 @@\n{changed}"
        out = git_diff(text)
        assert "lines truncated" in out
        assert "[full diff: rtk git diff --no-compact]" in out

    def test_git_status_groups(self):
        text = (
            "On branch dev\n"
            "Changes to be committed:\n"
            "  new file:   src/a.py\n"
            "Changes not staged for commit:\n"
            "  modified:   src/b.py\n"
            "Untracked files:\n"
            "  (use \"git add <file>...\" to include in what will be committed)\n"
            "  notes.txt\n"
        )
        out = git_status(text)
        assert "* dev" in out
        assert "+ Staged: 1 files" in out
        assert "~ Modified: 1 files" in out

    def test_git_status_clean(self):
        assert git_status("On branch main\nnothing to commit, working tree clean\n") == \
            "* main\nclean — nothing to commit"

    def test_git_status_empty(self):
        assert git_status("") == "Clean working tree"

    def test_git_log_keeps_subject_drops_body(self):
        text = (
            "commit abc1234def\n"
            "Author: Peng <p@x>\n"
            "Date:   Mon Aug 18 10:00:00 2026 +0800\n"
            "\n"
            "    fix: handle empty body\n"
            "\n"
            "    Long commit message body that should be dropped\n"
            "    because the model only needs the subject.\n"
        )
        out = git_log(text)
        assert "Subject: fix: handle empty body" in out
        assert "Long commit message body" not in out

    def test_git_log_oneline(self):
        text = "abc1234 first commit\ndef5678 second commit\n"
        out = git_log(text)
        assert "abc1234 first commit" in out
        assert "def5678 second commit" in out

    def test_build_output(self):
        text = "\n".join([
            "Compiling pkg-a v1",
            "Compiling pkg-b v2",
            "Compiling pkg-c v3",
            "error[E0308]: mismatched types",
            "  --> src/main.rs:2:3",
            "Finished dev",
        ])
        out = build_output(text)
        assert "Compiled 3 packages" in out
        assert "error[E0308]" in out
        assert "src/main.rs" in out
        assert "Finished dev" in out

    def test_grep_groups_and_caps(self):
        text = "\n".join(f"src/a.py:{i}:match {i}" for i in range(15))
        out = grep(text)
        assert "15 matches in 1F:" in out
        assert out.count(": match") == 10          # GREP_PER_FILE_MAX
        assert "+5" in out                          # remainder marker

    def test_compressed_output_survives_recompression(self):
        # tool results are resent every turn; a second pass over already
        # compressed text must be a no-op or the provider cache prefix breaks
        samples = [
            "\n".join(f"{i:>5}|def fn_{i}(x): return {i}" for i in range(1, 400)),
            "\n".join(f"{i}: import mod_{i}" for i in range(1, 400)),
            "\n".join(f"src/m{i%6}.py:{i*3}:def h_{i}(req):" for i in range(200)),
            "\n".join(f"./src/p{i%8}/f{i}.py" for i in range(120)),
            "diff --git a/x.py b/x.py\n" + "".join(
                f"@@ -{i},9 +{i},9 @@\n" + "".join(f"+l{j}\n" for j in range(20))
                for i in range(1, 50)),
            "On branch dev\nChanges not staged:\n" + "".join(f"  modified:   f{i}.py\n" for i in range(50)),
            "commit abc\nAuthor: x\n\n    subj\n\n    body\n" * 40,
            "npm warn d\n" + "Compiling p\n" * 200 + "Finished\n",
            "p\n" + "".join(f"├── d{i}\n│  └── f{i}.py\n" for i in range(120)) + "2 dirs\n",
        ]
        for text in samples:
            body = {"messages": [{"role": "tool", "tool_call_id": "c", "content": text}]}
            rtk.compress_body(body, "openai-chat")
            once = body["messages"][0]["content"]
            body2 = {"messages": [{"role": "tool", "tool_call_id": "c", "content": once}]}
            stats = rtk.compress_body(body2, "openai-chat")
            assert body2["messages"][0]["content"] == once, text[:40]
            assert stats.hits == [], text[:40]

    def test_find_groups_and_caps(self):
        files = [f"./src/deep/dir/file{i}.py" for i in range(15)]
        out = find("\n".join(files))
        assert "15 files in 1 dirs:" in out
        assert "+5" in out
        assert "file0.py" in out

    def test_dedup_log(self):
        text = "\n".join(["start", "repeat", "repeat", "repeat", "end"])
        out = dedup_log(text)
        assert out.splitlines() == ["start", "repeat", "  ... (2 duplicate lines)", "end"]

    def test_ls(self):
        text = "\n".join([
            "total 24",
            "drwxr-xr-x  4 me  staff  128 Jan  3 10:00 src",
            "-rw-r--r--  1 me  staff  220 Jan  3 10:00 a.py",
            "-rw-r--r--  1 me  staff  640 Jan  3 10:00 b.py",
        ])
        out = ls(text)
        assert "src/" in out
        assert "a.py  220B" in out
        assert "Summary: 2 files, 1 dirs" in out
        assert "2 .py" in out

    def test_tree_drops_summary(self):
        text = "proj\n├── src\n└── README.md\n\n2 directories, 2 files\n"
        out = tree(text)
        assert "directories" not in out
        assert "README.md" in out

    def test_smart_truncate(self):
        lines = [f"line{i}" for i in range(300)]
        out = smart_truncate("\n".join(lines))
        out_lines = out.split("\n")
        assert "line0" in out_lines[0]
        assert out_lines[-1] == "line299"
        assert any("+120 lines truncated" in l for l in out_lines)

    def test_smart_truncate_short_input_untouched(self):
        assert smart_truncate("a\nb\nc") == "a\nb\nc"

    def test_read_numbered(self):
        lines = [f"{i}|content {i}" for i in range(1, 301)]
        out = read_numbered("\n".join(lines))
        assert "(file continues)" in out
        assert out.split("\n")[0] == "1|content 1"

    def test_search_list(self):
        text = (
            "Result of search in '*.py' (total 3 files):\n"
            "- ./src/a.py\n- ./src/b.py\n- ./c.py\n"
        )
        out = search_list(text)
        assert "3 files in 2 dirs:" in out
        assert "a.py" in out


# ---------------------------------------------------------------------------
# compress_body — shapes, guards, determinism
# ---------------------------------------------------------------------------

class TestCompressBody:
    PAYLOAD = _big_grep_output()

    def _anthropic(self, content=None):
        return {"model": "auto", "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": content or self.PAYLOAD},
            ]},
        ]}

    def test_anthropic_string_form(self):
        body = self._anthropic()
        stats = rtk.compress_body(body, "anthropic")
        assert stats and stats.hits
        assert body["messages"][0]["content"][0]["content"] != self.PAYLOAD
        assert stats.saved_tokens > 0

    def test_anthropic_array_form(self):
        body = self._anthropic([{"type": "text", "text": self.PAYLOAD}])
        stats = rtk.compress_body(body, "anthropic")
        assert stats.hits[0].shape == "claude-array"
        assert body["messages"][0]["content"][0]["content"][0]["text"] != self.PAYLOAD

    def test_anthropic_is_error_skipped(self):
        body = self._anthropic()
        body["messages"][0]["content"][0]["is_error"] = True
        stats = rtk.compress_body(body, "anthropic")
        assert stats.hits == []
        assert body["messages"][0]["content"][0]["content"] == self.PAYLOAD

    def test_openai_chat_tool_string(self):
        body = {"messages": [{"role": "tool", "tool_call_id": "c1", "content": self.PAYLOAD}]}
        stats = rtk.compress_body(body, "openai-chat")
        assert stats.hits[0].shape == "openai-tool"
        assert body["messages"][0]["content"] != self.PAYLOAD

    def test_openai_chat_tool_parts(self):
        body = {"messages": [{"role": "tool", "content": [
            {"type": "text", "text": self.PAYLOAD}]}]}
        stats = rtk.compress_body(body, "openai-chat")
        assert stats.hits[0].shape == "openai-tool-array"

    def test_responses_output_string(self):
        body = {"input": [{"type": "function_call_output", "call_id": "c1", "output": self.PAYLOAD}]}
        stats = rtk.compress_body(body, "openai-responses")
        assert stats.hits[0].shape == "openai-responses-string"
        assert body["input"][0]["output"] != self.PAYLOAD

    def test_responses_output_parts(self):
        body = {"input": [{"type": "function_call_output", "call_id": "c1", "output": [
            {"type": "input_text", "text": self.PAYLOAD}]}]}
        stats = rtk.compress_body(body, "openai-responses")
        assert stats.hits[0].shape == "openai-responses-array"

    def test_small_content_untouched(self):
        body = self._anthropic("only ten chars")
        stats = rtk.compress_body(body, "anthropic")
        assert stats.hits == []
        assert stats.chars_before == len("only ten chars")

    def test_never_grows_never_empty(self):
        # every match in its own file: grouped output would be larger — guard reverts
        payload = "\n".join(f"src/file{i}.py:1:match" for i in range(40))
        body = self._anthropic(payload)
        stats = rtk.compress_body(body, "anthropic")
        assert stats.hits == []
        assert body["messages"][0]["content"][0]["content"] == payload

    def test_unique_line_blob_falls_back_to_smart_truncate(self):
        # codex reads files via shell (cat/sed): plain un-numbered dumps land
        # in dedup-log, save nothing, and must fall back to smart-truncate
        payload = "\n".join(f"def fn_{i}(x): return x * 2" for i in range(400))
        body = {"input": [{"type": "function_call_output", "call_id": "c1", "output": payload}]}
        stats = rtk.compress_body(body, "openai-responses")
        assert stats.hits[0].filter == "smart-truncate"
        assert "lines truncated" in body["input"][0]["output"]

    def test_dedup_savings_keep_dedup(self):
        # when dedup-log actually shrinks the text there is no fallback
        payload = "\n".join(["progress: " + "tick " * 10] * 60 + ["unique tail line"])
        body = {"input": [{"type": "function_call_output", "call_id": "c1", "output": payload}]}
        stats = rtk.compress_body(body, "openai-responses")
        assert stats.hits[0].filter == "dedup-log"

    def test_uncompressible_body_returns_empty_stats(self):
        body = {"messages": [{"role": "user", "content": "hi"}]}
        stats = rtk.compress_body(body, "anthropic")
        assert stats is not None
        assert stats.hits == []

    def test_unknown_protocol_returns_none(self):
        assert rtk.compress_body({}, "kiro") is None
        assert rtk.compress_body(None, "anthropic") is None

    def test_deterministic(self):
        b1, b2 = self._anthropic(), self._anthropic()
        rtk.compress_body(b1, "anthropic")
        rtk.compress_body(b2, "anthropic")
        assert (b1["messages"][0]["content"][0]["content"]
                == b2["messages"][0]["content"][0]["content"])

    def test_broken_body_never_raises(self):
        # fail-open: garbage structures must not escape as exceptions
        assert rtk.compress_body({"messages": "not-a-list"}, "anthropic") is not None
        assert rtk.compress_body({"messages": [None, 42, {"content": [None]}]}, "anthropic") is not None


class TestFormatLog:
    def test_none_when_no_hits(self):
        assert rtk.format_log(None) is None
        assert rtk.format_log(rtk.RtkStats()) is None

    def test_line_contents(self):
        stats = rtk.compress_body(
            {"messages": [{"role": "tool", "content": _big_grep_output()}]},
            "openai-chat",
        )
        line = rtk.format_log(stats)
        assert line.startswith("[rtk] saved ")
        assert "via [grep]" in line
        assert line.endswith(f"hits={len(stats.hits)}")
