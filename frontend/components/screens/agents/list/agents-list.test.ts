import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import {
  AGENTS_LIST,
  agentStatusKey,
  agentsFormerTabTarget,
  agentsCrumbs,
  formatRunMode,
  joinAgents,
  selectAgents,
} from "./agents-list";
import { AGENT_ROWS, FLEET, MANY_AGENT_ROWS, agent } from "./agents-list.fixtures";

function forQuery(qs: string, rows = AGENT_ROWS) {
  const state = parseListState(AGENTS_LIST, qs);
  return selectAgents(rows, {
    q: state.q,
    filters: effectiveFilters(AGENTS_LIST, state),
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
}

const slugs = (r: { rows: { slug: string }[] }) => r.rows.map((a) => a.slug);

describe("AGENTS_LIST", () => {
  it("leads with All and Mine, then Paused, on numbered pages", () => {
    expect(AGENTS_LIST.views.map((v) => v.label)).toEqual(["All", "Mine", "Paused"]);
    expect(AGENTS_LIST.paging).toBe("numbered");
    expect(AGENTS_LIST.views.find((v) => v.key === "mine")?.note).toMatch(/until agents record/);
  });

  it("filters on project, status, model, runtime and cluster", () => {
    expect(AGENTS_LIST.fields.map((f) => f.key)).toEqual([
      "project",
      "status",
      "model",
      "runtime",
      "cluster",
    ]);
  });
});

describe("agentsCrumbs", () => {
  it("is one Agents switcher over the area's functions, Agents active", () => {
    const [first, ...rest] = agentsCrumbs();
    expect(rest).toEqual([]);
    expect(first.label).toBe("Agents");
    expect(first.switcher?.find((o) => o.active)?.href).toBe("/agents");
    expect(first.switcher?.map((o) => o.label)).toContain("Workloads");
  });
});

describe("agentStatusKey", () => {
  it("prefers running, then paused, then a failed last run, then a schedule", () => {
    const base = agent("x");
    expect(agentStatusKey({ ...base, runningCount: 1, runPaused: true })).toBe("running");
    expect(agentStatusKey({ ...base, runPaused: true, lastRunStatus: "failed" })).toBe("paused");
    expect(agentStatusKey({ ...base, lastRunStatus: "timed_out" })).toBe("failing");
    expect(
      agentStatusKey(base, { runningCount: 0, isPaused: false, nextScheduledAt: "2026-09-29" })
    ).toBe("scheduled");
    expect(agentStatusKey(base)).toBe("idle");
  });

  it("reads live status over the fleet row once it arrives", () => {
    const base = agent("x", { runningCount: 3 });
    expect(agentStatusKey(base, { runningCount: 0, isPaused: false, nextScheduledAt: null })).toBe(
      "idle"
    );
  });
});

describe("joinAgents", () => {
  it("takes model and runtime from the spec that shares the slug, clusters from the app", () => {
    const [triage] = AGENT_ROWS;
    expect(triage.model).toBe("managed");
    expect(triage.runtime).toBe("claude");
    expect(triage.clusters).toEqual(["conflict-astrolift", "eks-us-west-2"]);
    expect(triage.running).toBe(2);
    expect(triage.mine).toBe(true);
    const docs = AGENT_ROWS.find((a) => a.slug === "docs-service")!;
    expect(docs.model).toBeNull();
    expect(docs.clusters).toEqual([]);
    expect(docs.mine).toBe(false);
    expect(docs.status).toBe("scheduled");
  });

  it("keeps every fleet row when the side queries return nothing", () => {
    const rows = joinAgents(FLEET, {
      live: [],
      specs: [],
      environments: [],
      myAppSlugs: new Set(),
    });
    expect(rows).toHaveLength(FLEET.length);
    expect(rows.every((r) => r.model === null && r.clusters.length === 0)).toBe(true);
  });
});

describe("selectAgents", () => {
  it("sorts by name by default", () => {
    expect(slugs(forQuery(""))).toEqual([
      "docs-service",
      "nightly-report",
      "no-repo",
      "paused-sync",
      "triage-bot",
    ]);
  });

  it("answers the Mine and Paused views", () => {
    expect(slugs(forQuery("view=mine"))).toEqual(["no-repo", "paused-sync", "triage-bot"]);
    expect(slugs(forQuery("view=paused"))).toEqual(["paused-sync"]);
  });

  it("filters on status, model, runtime, project and cluster chips", () => {
    expect(slugs(forQuery("status=failing"))).toEqual(["nightly-report"]);
    expect(slugs(forQuery("model=api-key"))).toEqual(["nightly-report", "paused-sync"]);
    expect(slugs(forQuery("runtime=service"))).toEqual(["docs-service"]);
    expect(slugs(forQuery("runtime=codex"))).toEqual(["nightly-report"]);
    expect(slugs(forQuery("project=docs"))).toEqual(["docs-service"]);
    expect(slugs(forQuery("cluster=gke-eu-west-4"))).toEqual(["nightly-report"]);
  });

  it("searches name, slug, app, project and repo", () => {
    expect(slugs(forQuery("q=reports"))).toEqual(["nightly-report"]);
    expect(slugs(forQuery("q=TRIAGE"))).toEqual(["triage-bot"]);
  });

  it("sorts by the last run, newest first, never run last", () => {
    const r = forQuery("sort=-lastRun,name");
    expect(slugs(r).at(-1)).toBe("docs-service");
  });

  it("pages with the filtered count", () => {
    const r = forQuery("page=3", MANY_AGENT_ROWS);
    expect(r.totalCount).toBe(60);
    expect(r.rows).toHaveLength(10);
  });
});

describe("formatRunMode", () => {
  it("names the family and mode, once for a service, readable for an unknown mode", () => {
    expect(formatRunMode("task", "schedule")).toBe("Task · Schedule");
    expect(formatRunMode("service", "service")).toBe("Service");
    expect(formatRunMode("task", "custom_mode")).toBe("Task · Custom Mode");
  });
});

describe("agentsFormerTabTarget", () => {
  it("sends each former tab where it lives now", () => {
    expect(agentsFormerTabTarget({ tab: "dispatch" })).toBe("/agents/runs/new");
    expect(agentsFormerTabTarget({ tab: "active" })).toBe("/tasks?view=running");
    expect(agentsFormerTabTarget({ tab: "history" })).toBe("/tasks");
    expect(agentsFormerTabTarget({ tab: "boxes" })).toBe("/workloads");
    expect(agentsFormerTabTarget({ tab: "compliance" })).toBe("/agents");
  });

  it("keeps the project filter when the tab was this list", () => {
    expect(agentsFormerTabTarget({ tab: "registry", project: "platform" })).toBe(
      "/agents?project=platform"
    );
    expect(agentsFormerTabTarget({ tab: "unknown" })).toBe("/agents");
  });

  it("is null without a tab, so the list renders", () => {
    expect(agentsFormerTabTarget({ project: "platform" })).toBeNull();
  });
});
