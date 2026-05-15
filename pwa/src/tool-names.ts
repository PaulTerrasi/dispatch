// Convert a raw tool name (e.g. "mcp__github__list_pull_requests",
// "fetch_youtube_channel", "WebFetch") into a human-readable label
// for display in tool-call indicators.
export function prettyToolName(name: string): string {
  // MCP tools come through as "mcp__<server>__<tool>"; drop the prefix
  // and use just the tool portion.
  let s = name;
  if (s.startsWith("mcp__")) {
    const parts = s.split("__");
    if (parts.length >= 3) s = parts.slice(2).join("__");
  }

  // snake_case → spaced words
  if (s.includes("_")) {
    s = s.replace(/_+/g, " ").trim();
  } else {
    // CamelCase → spaced words (e.g. "WebFetch" → "Web Fetch")
    const spaced = s.replace(/([a-z])([A-Z])/g, "$1 $2");
    if (spaced !== s) s = spaced;
  }

  return s.toLowerCase();
}
