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


def test_file_headers_tolerated():
    original = "a\nb\n"
    diff = "--- a/profile.md\n+++ b/profile.md\n@@ -1,1 +1,2 @@\n a\n+a-extra\n"
    result = apply_unified_diff(original, diff)
    assert result == "a\na-extra\nb\n"
