"""String-based profile editing.

Used by reflection and chat agents to modify `profile.md`. We deliberately
avoid unified-diff: LLMs miscount lines, and a one-character context-line
mistake fails the whole patch. Instead, `edit_profile` does an exact-match
find-and-replace on a unique substring — the same shape as the `Edit` tool
that coding agents use every day, which they get right far more reliably.

Tactics for the agent:
  - Append: pass the last few lines of the profile as `find`, and those
    same lines plus the new content as `replace`.
  - Delete: pass the lines to remove as `find` and `""` as `replace`.
  - Rewrite a section: pass the whole section header + body as `find`.

Errors are written to be self-correcting: they tell the agent *why* the
match failed (not found / not unique) and what to do next.
"""

from __future__ import annotations


class EditError(ValueError):
    pass


def edit_profile(original: str, find: str, replace: str) -> str:
    """Replace the single occurrence of `find` in `original` with `replace`.

    Raises EditError if `find` is empty, missing, or non-unique. The message
    is shaped for the agent to read and retry: it states which case failed
    and (for the non-unique case) where the matches are.
    """
    if not find:
        raise EditError(
            "find is empty. Pass a unique substring of the current profile "
            "as `find` to anchor the edit. To append, include the last few "
            "existing lines in `find` and reproduce them in `replace` "
            "followed by the new content."
        )
    count = original.count(find)
    # Check presence before the identical-strings check: if `find` isn't in
    # the profile but happens to equal `replace`, "not found" is the more
    # actionable signal — otherwise the agent might think the edit was
    # already applied when in fact `find` doesn't exist there at all.
    if count == 0:
        hint = _nearest_line_hint(original, find)
        raise EditError(
            "find string not present in profile.md. Call read_profile first "
            "and copy the text verbatim, preserving every space, dash, and "
            "newline. " + hint
        )
    if count > 1:
        locations = _match_line_numbers(original, find)
        raise EditError(
            f"find string appears {count} times in profile.md (lines "
            f"{', '.join(str(n) for n in locations)}). Include more "
            "surrounding context in `find` so it matches exactly one spot."
        )
    if find == replace:
        raise EditError("find and replace are identical — nothing to change.")
    return original.replace(find, replace, 1)


def _match_line_numbers(text: str, needle: str) -> list[int]:
    """1-based line numbers where each occurrence of `needle` begins.

    Advances by `len(needle)` to count non-overlapping matches, matching
    `str.count()` semantics — so a needle like "aa" in "aaaa" reports two
    matches, not three, keeping this in sync with the count shown in the
    error message.
    """
    lines: list[int] = []
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx < 0:
            break
        lines.append(text.count("\n", 0, idx) + 1)
        start = idx + len(needle)
    return lines


def _nearest_line_hint(text: str, needle: str) -> str:
    """If a source line shares a long prefix with the needle's first line,
    show it. Agents most often paste a stale or slightly-edited version of
    an existing block — surfacing the live version of its first line is
    usually enough to unblock them without another read_profile.
    """
    # Defensive: callers don't guarantee `needle` is non-empty here. Use
    # an explicit list check so the indexing is safe regardless of what
    # `splitlines()` returns.
    lines = needle.splitlines()
    first = lines[0] if lines else ""
    if len(first) < 4:
        return ""
    # Track the matched line text alongside its number so the "best" pair
    # is always self-consistent — no index arithmetic that could quietly
    # alias to splitlines()[-1] if the initial state ever leaked through.
    best: tuple[int, str] | None = None
    best_overlap = 0
    for i, line in enumerate(text.splitlines(), start=1):
        n = _common_prefix_len(first, line)
        if n > best_overlap:
            best_overlap = n
            best = (i, line)
    # Require at least 4 chars (or half the needle line, whichever is more)
    # of overlap before claiming a match — otherwise the hint is noise.
    if best is not None and best_overlap >= max(4, len(first) // 2):
        i, live = best
        return f"(closest live line is {i}: {live!r})"
    return ""


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i
