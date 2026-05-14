"""Apply a unified diff to profile.md.

Reflection-phase tool. We accept a small subset of unified-diff:
  - one or more @@ hunks
  - context lines start with " "
  - removed lines start with "-"
  - added lines start with "+"
  - file headers (--- / +++) are tolerated and ignored

Rejects malformed diffs cleanly so the agent can retry rather than
corrupting the profile.
"""

from __future__ import annotations

from dataclasses import dataclass


class PatchError(ValueError):
    pass


@dataclass
class _Hunk:
    old_start: int  # 1-based, may be 0 for empty file
    old_count: int
    lines: list[str]  # raw lines including leading " ", "-", "+"


def _parse_hunks(diff: str) -> list[_Hunk]:
    hunks: list[_Hunk] = []
    current: _Hunk | None = None
    for raw in diff.splitlines():
        if raw.startswith(("--- ", "+++ ")):
            continue
        if raw.startswith("@@"):
            # @@ -old_start,old_count +new_start,new_count @@ ...
            try:
                meta = raw.split("@@")[1].strip()
                old_part = next(p for p in meta.split() if p.startswith("-"))
                old_part = old_part[1:]
                if "," in old_part:
                    old_start_s, old_count_s = old_part.split(",", 1)
                    old_start, old_count = int(old_start_s), int(old_count_s)
                else:
                    old_start, old_count = int(old_part), 1
            except (StopIteration, ValueError) as e:
                raise PatchError(f"malformed hunk header: {raw!r}") from e
            current = _Hunk(old_start=old_start, old_count=old_count, lines=[])
            hunks.append(current)
            continue
        if current is None:
            # Allow blank preamble
            if raw.strip() == "":
                continue
            raise PatchError(f"line outside any hunk: {raw!r}")
        if raw == "":
            current.lines.append(" ")
        elif raw[0] in (" ", "-", "+"):
            current.lines.append(raw)
        elif raw.startswith("\\"):
            # "\ No newline at end of file" — ignore
            continue
        else:
            raise PatchError(f"unexpected line prefix: {raw!r}")
    if not hunks:
        raise PatchError("diff contained no hunks")
    return hunks


def apply_unified_diff(original: str, diff: str) -> str:
    """Apply `diff` to `original`. Raises PatchError if it doesn't apply cleanly."""
    hunks = _parse_hunks(diff)
    src_lines = original.splitlines(keepends=False)
    out: list[str] = []
    cursor = 0  # 0-based index into src_lines
    for hunk in hunks:
        target = max(hunk.old_start - 1, 0)
        if target < cursor:
            raise PatchError(f"hunk @@ -{hunk.old_start} starts before the previous hunk ended")
        if target > len(src_lines):
            raise PatchError(
                f"hunk @@ -{hunk.old_start} starts past end of file ({len(src_lines)} lines)"
            )
        out.extend(src_lines[cursor:target])
        cursor = target
        for ln in hunk.lines:
            tag, content = ln[0], ln[1:]
            if tag == " ":
                if cursor >= len(src_lines) or src_lines[cursor] != content:
                    actual = src_lines[cursor] if cursor < len(src_lines) else "<EOF>"
                    raise PatchError(
                        f"context mismatch at source line {cursor + 1}: "
                        f"expected {content!r}, got {actual!r}"
                    )
                out.append(content)
                cursor += 1
            elif tag == "-":
                if cursor >= len(src_lines) or src_lines[cursor] != content:
                    actual = src_lines[cursor] if cursor < len(src_lines) else "<EOF>"
                    raise PatchError(
                        f"removal mismatch at source line {cursor + 1}: "
                        f"expected {content!r}, got {actual!r}"
                    )
                cursor += 1
            elif tag == "+":
                out.append(content)
    out.extend(src_lines[cursor:])
    trailing_nl = original.endswith("\n")
    return "\n".join(out) + ("\n" if trailing_nl else "")
