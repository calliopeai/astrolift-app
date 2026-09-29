import { describe, expect, it } from "vitest";

import { agentLogLine } from "./agent-log-lines";

describe("agentLogLine", () => {
  it("reads the time and level off the front", () => {
    expect(agentLogLine("2026-09-28T14:02:15Z DEBUG tool call: incidents.list()")).toEqual({
      ts: "2026-09-28T14:02:15Z",
      message: "tool call: incidents.list()",
      level: "debug",
    });
    expect(agentLogLine("2026-09-28T14:02:15.123+00:00 WARNING  slow")).toMatchObject({
      level: "warn",
      message: "slow",
    });
  });

  it("keeps a timed line without a level whole, classified later by LogView", () => {
    expect(agentLogLine("2026-09-28T14:02:15Z drafting summary")).toEqual({
      ts: "2026-09-28T14:02:15Z",
      message: "drafting summary",
    });
  });

  it("keeps a line without a leading time as its whole text", () => {
    expect(agentLogLine("Traceback (most recent call last):")).toEqual({
      ts: "",
      message: "Traceback (most recent call last):",
    });
  });
});
