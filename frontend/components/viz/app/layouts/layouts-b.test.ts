import { describe, expect, it } from "vitest";

import { makeApp, stepApp, type AppSnapshot } from "../../core/app-model";
import { mulberry32 } from "../../core/semantics";

import {
  COLD_GAP_MS,
  agentFeed,
  formatCountdown,
  functionStats,
  lastCallByTarget,
  layerNodes,
  nextRun,
  parseSchedule,
  pastRuns,
  roleSections,
  summarize,
  truncate,
} from "./layouts-b";

function warm(s: AppSnapshot, steps: number): AppSnapshot {
  const rng = mulberry32(9);
  for (let i = 0; i < steps; i++) s = stepApp(s, rng, 1200);
  return s;
}

describe("agentFeed", () => {
  it("lists agent actions newest first, each aimed at one of the agent's edges", () => {
    const s = warm(makeApp("service-agent"), 8);
    const feed = agentFeed(s);
    expect(feed.length).toBeGreaterThan(0);
    for (let i = 1; i < feed.length; i++) expect(feed[i - 1].at).toBeGreaterThanOrEqual(feed[i].at);
    const targets = new Set(s.edges.filter((e) => e.from === "agent").map((e) => e.to));
    for (const f of feed) expect(targets.has(f.target!.id)).toBe(true);
  });

  it("is deterministic and honours a detail naming a node", () => {
    const base = makeApp("service-agent");
    const s: AppSnapshot = {
      ...base,
      events: [{ id: "e1", kind: "agent_action", nodeId: "agent", at: base.now, detail: "llm" }],
    };
    const [f] = agentFeed(s);
    expect(f.target?.id).toBe("llm");
    expect(f.verb).toBe("called");
    expect(agentFeed(s)).toEqual(agentFeed(s));
  });

  it("marks actions on a failing edge as failing", () => {
    const base = makeApp("service-agent", { incident: true });
    const s: AppSnapshot = {
      ...base,
      events: [{ id: "e1", kind: "agent_action", nodeId: "agent", at: base.now, detail: "db" }],
    };
    expect(agentFeed(s)[0].failing).toBe(true);
  });

  it("keeps the newest call per target", () => {
    const s = warm(makeApp("service-agent"), 10);
    const m = lastCallByTarget(agentFeed(s));
    for (const f of m.values()) {
      const all = agentFeed(s).filter((x) => x.target?.id === f.target?.id);
      expect(f.at).toBe(Math.max(...all.map((x) => x.at)));
    }
  });
});

describe("layerNodes", () => {
  it("puts triggers first and sinks last for functions", () => {
    const s = makeApp("functions");
    const cols = layerNodes(s.nodes, s.edges).map((c) => c.map((n) => n.id));
    expect(cols[0]).toEqual(expect.arrayContaining(["http", "bucket"]));
    expect(cols[cols.length - 1]).toContain("out");
  });

  it("survives a cycle", () => {
    const s = makeApp("workflow");
    const cols = layerNodes(s.nodes, [
      ...s.edges,
      { from: "deploy", to: "build", rps: 1, errorRate: 0 },
    ]);
    expect(cols.flat()).toHaveLength(s.nodes.length);
  });
});

describe("functionStats", () => {
  it("flags a cold start after a quiet gap only", () => {
    const base = makeApp("functions");
    const now = base.now;
    const s: AppSnapshot = {
      ...base,
      events: [
        { id: "a", kind: "invoked", nodeId: "resize", at: now - 1000 },
        { id: "b", kind: "invoked", nodeId: "resize", at: now },
        { id: "c", kind: "invoked", nodeId: "notify", at: now - COLD_GAP_MS - 1000 },
        { id: "d", kind: "invoked", nodeId: "notify", at: now },
      ],
    };
    const by = new Map(functionStats(s).map((f) => [f.node.id, f]));
    expect(by.get("resize")!.cold).toBe(false);
    expect(by.get("resize")!.invocations).toBe(2);
    expect(by.get("notify")!.cold).toBe(true);
    expect(by.get("thumb")!.last).toBeUndefined();
    expect(by.get("thumb")!.cold).toBe(false);
  });
});

describe("schedule", () => {
  const now = Date.UTC(2026, 8, 28, 14, 0, 0);

  it("parses a daily cron and finds the next and past slots", () => {
    const s = parseSchedule("0 2 * * *")!;
    expect(nextRun(s, now)).toBe(Date.UTC(2026, 8, 29, 2, 0, 0));
    const past = pastRuns(s, now, 3);
    expect(past).toEqual([
      Date.UTC(2026, 8, 26, 2, 0, 0),
      Date.UTC(2026, 8, 27, 2, 0, 0),
      Date.UTC(2026, 8, 28, 2, 0, 0),
    ]);
  });

  it("parses step crons and 'every N min'", () => {
    expect(nextRun(parseSchedule("*/15 * * * *")!, now + 1)).toBe(now + 15 * 60_000);
    expect(nextRun(parseSchedule("every 15 min")!, now)).toBe(now + 15 * 60_000);
    expect(nextRun(parseSchedule("every 2 h")!, now)).toBe(Date.UTC(2026, 8, 28, 16, 0, 0));
  });

  it("rejects what it cannot read", () => {
    expect(parseSchedule("on push")).toBeNull();
    expect(parseSchedule("0 2 * * 1")).toBeNull();
    expect(parseSchedule("99 2 * * *")).toBeNull();
  });

  it("formats a countdown", () => {
    expect(formatCountdown(12_000)).toBe("12s");
    expect(formatCountdown(65_000)).toBe("1m 05s");
    expect(formatCountdown(12 * 3600_000 + 5 * 60_000)).toBe("12h 05m");
    expect(formatCountdown(50 * 3600_000)).toBe("2d 2h");
  });
});

describe("roleSections and summaries", () => {
  it("groups mixed nodes by role, dropping empty sections", () => {
    const s = makeApp("mixed");
    const titles = roleSections(s.nodes).map((x) => x.title);
    expect(titles).toEqual(["Traffic", "Services", "Functions", "Jobs", "Data"]);
  });

  it("summarises failing, busy and idle", () => {
    const s = makeApp("service-agent", { incident: true });
    expect(summarize(s.nodes, "nodes")).toMatch(/^5 nodes, 1 failing/);
  });

  it("truncates with an ellipsis", () => {
    expect(truncate("abcdefgh", 5)).toBe("abcd…");
    expect(truncate("abc", 5)).toBe("abc");
  });
});
