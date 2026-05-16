from __future__ import annotations

import pytest

from digest.patch import EditError, edit_profile


def test_simple_replacement():
    original = "# Profile\n\n- LLMs\n- Old Topic\n"
    assert (
        edit_profile(original, "- Old Topic", "- Self-hosted home automation")
        == "# Profile\n\n- LLMs\n- Self-hosted home automation\n"
    )


def test_append_via_anchor():
    """The documented append pattern: include the tail in `find` and
    reproduce it in `replace` plus the new content."""
    original = "## Standing interests\n- LLMs\n"
    out = edit_profile(original, "- LLMs\n", "- LLMs\n- Woodworking\n")
    assert out == "## Standing interests\n- LLMs\n- Woodworking\n"


def test_delete_with_empty_replace():
    original = "a\nremove me\nb\n"
    assert edit_profile(original, "remove me\n", "") == "a\nb\n"


def test_multi_line_block_replacement():
    original = "## A\n- old1\n- old2\n\n## B\n- keep\n"
    out = edit_profile(original, "## A\n- old1\n- old2\n", "## A\n- new\n")
    assert out == "## A\n- new\n\n## B\n- keep\n"


def test_empty_find_rejected():
    with pytest.raises(EditError, match="find is empty"):
        edit_profile("anything\n", "", "x")


def test_find_equals_replace_rejected():
    with pytest.raises(EditError, match="identical"):
        edit_profile("a\nb\n", "a", "a")


def test_find_not_present_rejected():
    with pytest.raises(EditError, match="not present"):
        edit_profile("# Profile\n- LLMs\n", "- WRONG", "- new")


def test_not_present_hint_points_at_live_line():
    """When the agent pastes a stale version of an existing line, the
    error should hint at the live version so they can retry without
    another read."""
    original = "## Standing interests\n- LLMs and agents\n"
    # Stale version: missing "and agents" — the live line is the close match.
    with pytest.raises(EditError, match="closest live line is 2"):
        edit_profile(original, "- LLMs only", "- LLMs (deep dives)")


def test_non_unique_find_rejected_with_line_numbers():
    original = "- a\n- a\n- b\n"
    with pytest.raises(EditError, match=r"appears 2 times.*lines 1, 2"):
        edit_profile(original, "- a", "- A")


def test_preserves_trailing_newline_state():
    """No-newline files stay no-newline; newline files stay newline."""
    assert edit_profile("a\nb", "b", "B") == "a\nB"
    assert edit_profile("a\nb\n", "b\n", "B\n") == "a\nB\n"


def test_whitespace_inside_find_is_preserved():
    """Whitespace inside the find string is matched verbatim — but pure
    substring semantics mean indentation in front of the match is fine,
    as long as `replace` accounts for what should remain."""
    original = "  - nested item\n"
    # Pure substring match: the leading spaces stay because they're not in `find`.
    assert edit_profile(original, "- nested item", "- changed") == "  - changed\n"
