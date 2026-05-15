from __future__ import annotations

import pytest

from digest.patch import PatchError, apply_unified_diff


def test_simple_addition():
    original = "# Profile\n\n## Standing interests\n- LLMs\n"
    diff = "@@ -3,2 +3,3 @@\n ## Standing interests\n - LLMs\n+- Woodworking\n"
    result = apply_unified_diff(original, diff)
    assert "Woodworking" in result
    assert result.endswith("\n")


def test_replacement():
    original = "# Profile\n\n## Standing interests\n- LLMs\n- Old Topic\n"
    diff = "@@ -4,2 +4,2 @@\n - LLMs\n-- Old Topic\n+- Self-hosted home automation\n"
    result = apply_unified_diff(original, diff)
    assert "Old Topic" not in result
    assert "Self-hosted home automation" in result


def test_multiple_hunks():
    original = "A\nB\nC\nD\nE\n"
    diff = "@@ -1,1 +1,1 @@\n-A\n+A1\n@@ -4,1 +4,1 @@\n-D\n+D1\n"
    result = apply_unified_diff(original, diff)
    assert result == "A1\nB\nC\nD1\nE\n"


def test_context_mismatch_rejected():
    original = "# Profile\n\n## Standing interests\n- LLMs\n"
    diff = "@@ -3,2 +3,3 @@\n ## Standing interests\n - WRONG\n+- Added\n"
    with pytest.raises(PatchError, match="context mismatch"):
        apply_unified_diff(original, diff)


def test_removal_mismatch_rejected():
    original = "A\nB\nC\n"
    diff = "@@ -2,1 +2,0 @@\n-X\n"
    with pytest.raises(PatchError, match="removal mismatch"):
        apply_unified_diff(original, diff)


def test_empty_diff_rejected():
    with pytest.raises(PatchError, match="no hunks"):
        apply_unified_diff("anything\n", "")


def test_malformed_header_rejected():
    with pytest.raises(PatchError, match="malformed hunk header"):
        apply_unified_diff("a\n", "@@ this is not a header @@\n a\n")


def test_single_line_hunk_header_without_count():
    """`@@ -3 +3 @@` (no comma) is valid — count defaults to 1."""
    original = "a\nb\nc\n"
    diff = "@@ -2 +2 @@\n-b\n+B\n"
    assert apply_unified_diff(original, diff) == "a\nB\nc\n"


def test_blank_preamble_before_first_hunk_tolerated():
    """A leading blank line before any @@ is fine; we skip it."""
    original = "a\nb\n"
    diff = "\n@@ -1,1 +1,2 @@\n a\n+extra\n"
    assert apply_unified_diff(original, diff) == "a\nextra\nb\n"


def test_line_outside_any_hunk_rejected():
    """Non-blank text before the first @@ is a malformed diff."""
    with pytest.raises(PatchError, match="outside any hunk"):
        apply_unified_diff("a\n", "garbage line\n@@ -1,1 +1,1 @@\n a\n")


def test_blank_body_line_inside_hunk_treated_as_context():
    """A truly empty line inside a hunk represents a blank context line."""
    original = "alpha\n\nbeta\n"
    # Note the empty middle line — apply_unified_diff promotes it to " ".
    diff = "@@ -1,3 +1,3 @@\n alpha\n\n+gamma\n beta\n"
    out = apply_unified_diff(original, diff)
    assert out == "alpha\n\ngamma\nbeta\n"


def test_no_newline_marker_ignored():
    r"""`\ No newline at end of file` lines are tolerated and ignored."""
    original = "a\nb"  # no trailing newline
    diff = "@@ -2,1 +2,1 @@\n-b\n\\ No newline at end of file\n+B\n\\ No newline at end of file\n"
    assert apply_unified_diff(original, diff) == "a\nB"


def test_unexpected_prefix_rejected():
    with pytest.raises(PatchError, match="unexpected line prefix"):
        apply_unified_diff("a\n", "@@ -1,1 +1,1 @@\n a\n?not-a-real-prefix\n")


def test_hunk_starts_before_previous_rejected():
    """Hunks must be in increasing source-line order."""
    original = "a\nb\nc\nd\n"
    diff = "@@ -3,1 +3,1 @@\n-c\n+C\n@@ -1,1 +1,1 @@\n-a\n+A\n"
    with pytest.raises(PatchError, match="before the previous hunk"):
        apply_unified_diff(original, diff)


def test_hunk_past_eof_rejected():
    original = "a\nb\n"
    diff = "@@ -10,1 +10,1 @@\n-x\n"
    with pytest.raises(PatchError, match="past end of file"):
        apply_unified_diff(original, diff)


def test_removal_past_eof_reports_eof_marker():
    original = "a\n"
    diff = "@@ -1,2 +1,1 @@\n a\n-b\n"
    with pytest.raises(PatchError, match="<EOF>"):
        apply_unified_diff(original, diff)


def test_context_past_eof_reports_eof_marker():
    original = "a\n"
    diff = "@@ -1,2 +1,2 @@\n a\n b\n"
    with pytest.raises(PatchError, match="<EOF>"):
        apply_unified_diff(original, diff)


def test_preserves_missing_trailing_newline():
    """Files without a trailing newline keep that property after patching."""
    original = "a\nb"
    diff = "@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    assert apply_unified_diff(original, diff) == "a\nB"


def test_addition_followed_by_more_hunk_lines():
    """A `+` line in the middle of a hunk must not short-circuit the rest of the hunk."""
    original = "a\nb\nc\n"
    diff = "@@ -1,3 +1,4 @@\n a\n+inserted\n b\n c\n"
    assert apply_unified_diff(original, diff) == "a\ninserted\nb\nc\n"


def test_zero_count_hunk_header_at_top():
    """`@@ -0,0 +1,1 @@` (empty source file) inserts the added line(s)."""
    diff = "@@ -0,0 +1,1 @@\n+only-line\n"
    assert apply_unified_diff("", diff) == "only-line"


def test_file_headers_tolerated():
    original = "a\nb\n"
    diff = "--- a/profile.md\n+++ b/profile.md\n@@ -1,1 +1,2 @@\n a\n+a-extra\n"
    result = apply_unified_diff(original, diff)
    assert result == "a\na-extra\nb\n"
