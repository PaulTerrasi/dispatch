import { describe, expect, it } from "vitest";
import { prettyToolName } from "../tool-names";

describe("prettyToolName", () => {
  it("strips mcp__<server>__ prefix", () => {
    expect(prettyToolName("mcp__github__list_pull_requests")).toBe(
      "list pull requests",
    );
  });

  it("converts snake_case to spaces", () => {
    expect(prettyToolName("fetch_youtube_channel")).toBe("fetch youtube channel");
  });

  it("splits CamelCase", () => {
    expect(prettyToolName("WebFetch")).toBe("web fetch");
    expect(prettyToolName("WebSearch")).toBe("web search");
  });

  it("lowercases single-word names", () => {
    expect(prettyToolName("Read")).toBe("read");
    expect(prettyToolName("Bash")).toBe("bash");
  });

  it("handles mcp tool with camelcase suffix", () => {
    expect(prettyToolName("mcp__server__doThing")).toBe("do thing");
  });
});
