import { describe, expect, it } from "vitest";

import type { AstroliftAgentInteraction } from "@/graphql/agents/agents.types";

import { agentLogLines, agentRunSteps, MAX_INTERACTION_STEPS } from "./agent-run-steps";

const NOW = Date.parse("2026-09-28T12:10:00Z");

const call = (
  id: string,
  name: string,
  at: string,
  status = "ok",
  kind = "tool_call"
): AstroliftAgentInteraction => ({ id, kind, name, status, occurredAt: at });

describe("agentRunSteps", () => {
  it.each(["canceled", "CANCELLED"])(
    "finishes %s runs as skipped rather than successful",
    (status) => {
      const steps = agentRunSteps(
        {
          status,
          createdAt: "2026-09-28T12:00:00Z",
          startedAt: "2026-09-28T12:00:01Z",
          finishedAt: "2026-09-28T12:05:00Z",
        },
        [],
        NOW
      );
      expect(steps[1].state).toBe("skipped");
      expect(steps[1].durationMs).toBe(299_000);
    }
  );

  it("shows queued and the run, ticking while live", () => {
    const steps = agentRunSteps(
      {
        status: "running",
        createdAt: "2026-09-28T12:00:00Z",
        startedAt: "2026-09-28T12:00:30Z",
        finishedAt: null,
      },
      [],
      NOW
    );
    expect(steps.map((s) => [s.name, s.state, s.durationMs])).toEqual([
      ["queued", "ok", 30_000],
      ["running", "running", 570_000],
    ]);
  });

  it("marks a spawn failure as failed before a pod started", () => {
    const steps = agentRunSteps(
      {
        status: "failed",
        createdAt: "2026-09-28T12:00:00Z",
        startedAt: null,
        finishedAt: "2026-09-28T12:00:20Z",
      },
      [],
      NOW
    );
    expect(steps.map((s) => s.state)).toEqual(["failed", "skipped"]);
    expect(steps[0].durationMs).toBe(20_000);
  });

  it("adds calls oldest first, leaves heartbeats to the map, folds repeats", () => {
    const steps = agentRunSteps(
      {
        status: "completed",
        createdAt: "2026-09-28T12:00:00Z",
        startedAt: "2026-09-28T12:00:01Z",
        finishedAt: "2026-09-28T12:05:00Z",
      },
      [
        call("h", "POST /heartbeat", "2026-09-28T12:01:00Z", "ok", "control_api"),
        call("b", "email.draft", "2026-09-28T12:03:00Z", "error"),
        call("a1", "crm.search", "2026-09-28T12:01:00Z"),
        call("a2", "crm.search", "2026-09-28T12:02:00Z"),
        call("g", "approval", "2026-09-28T12:04:00Z", "pending", "gate"),
      ],
      NOW
    );
    expect(steps.slice(2).map((s) => [s.name, s.state, s.detail])).toEqual([
      ["crm.search", "ok", "tool call · ×2"],
      ["email.draft", "failed", "tool call"],
      ["approval", "skipped", "gate"],
    ]);
  });

  it("keeps the newest calls and folds the oldest into one line", () => {
    const many = Array.from({ length: MAX_INTERACTION_STEPS + 5 }, (_, i) =>
      call(`c${i}`, `tool.${i}`, new Date(NOW - (100 - i) * 1000).toISOString())
    );
    const steps = agentRunSteps(
      { status: "running", createdAt: "2026-09-28T12:00:00Z", startedAt: "2026-09-28T12:00:01Z" },
      many,
      NOW
    );
    expect(steps).toHaveLength(2 + 1 + MAX_INTERACTION_STEPS);
    expect(steps[2].name).toBe("5 earlier calls");
    expect(steps.at(-1)?.name).toBe(`tool.${MAX_INTERACTION_STEPS + 4}`);
  });
});

describe("agentLogLines", () => {
  it("keeps a line's own instant, else the fallback", () => {
    expect(
      agentLogLines(
        ["2026-09-28T12:00:01Z INFO  starting", "no stamp here"],
        "2026-09-28T12:00:00Z"
      )
    ).toEqual([
      { ts: "2026-09-28T12:00:01Z", message: "INFO  starting" },
      { ts: "2026-09-28T12:00:00Z", message: "no stamp here" },
    ]);
  });
});
