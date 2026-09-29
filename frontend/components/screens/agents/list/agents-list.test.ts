import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import {
  AGENTS_LIST,
  agentFleetPageVariables,
  agentsFormerTabTarget,
  agentsCrumbs,
  formatRunMode,
  toAgentRow,
} from "./agents-list";
import { agent, AGENT_ROWS, NEXT_DIGEST } from "./agents-list.fixtures";

function forQuery(qs: string) {
  const state = parseListState(AGENTS_LIST, qs);
  return agentFleetPageVariables("org-1", {
    q: state.q,
    filters: effectiveFilters(AGENTS_LIST, state),
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
}

describe("AGENTS_LIST", () => {
  it("leads with All and Mine, then Paused, on numbered pages", () => {
    expect(AGENTS_LIST.views.map((v) => v.label)).toEqual(["All", "Mine", "Paused"]);
    expect(AGENTS_LIST.paging).toBe("numbered");
    expect(AGENTS_LIST.views.find((v) => v.key === "mine")?.note).toMatch(/agents you registered/);
  });

  it("filters on project, status, model, runtime and cluster", () => {
    expect(AGENTS_LIST.fields.map((f) => f.key)).toEqual([
      "project",
      "status",
      "model",
      "runtime",
      "cluster",
    ]);
    expect(AGENTS_LIST.fields.find((f) => f.key === "model")?.options?.map((o) => o.value)).toEqual(
      ["managed", "gateway", "api-key"]
    );
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

describe("agentFleetPageVariables", () => {
  it("asks for page 1 by name with no filter on a cold load", () => {
    expect(forQuery("")).toEqual({
      orgId: "org-1",
      search: null,
      filter: null,
      sort: "name",
      page: 1,
      pageSize: 25,
    });
  });

  it("sends Mine as owner me and Paused as paused", () => {
    expect(forQuery("view=mine").filter).toEqual({ owner: ["me"] });
    expect(forQuery("view=paused").filter).toEqual({ paused: true });
  });

  it("sends each chip as a one-value list", () => {
    expect(
      forQuery("status=failing&model=gateway&runtime=codex&project=docs&cluster=gke-eu-west-4")
        .filter
    ).toEqual({
      project: ["docs"],
      status: ["failing"],
      model: ["gateway"],
      runtime: ["codex"],
      cluster: ["gke-eu-west-4"],
    });
  });

  it("passes search, sort and page through", () => {
    const v = forQuery("q=%20reports%20&sort=-lastRun,name&page=3&pageSize=50");
    expect(v.search).toBe("reports");
    expect(v.sort).toBe("-lastRun,name");
    expect(v.page).toBe(3);
    expect(v.pageSize).toBe(50);
  });
});

describe("toAgentRow", () => {
  it("reads status, model, runtime, clusters and owner off the server row", () => {
    const triage = AGENT_ROWS.find((a) => a.slug === "triage-bot")!;
    expect(triage.status).toBe("running");
    expect(triage.running).toBe(2);
    expect(triage.model).toBe("managed");
    expect(triage.runtime).toBe("claude");
    expect(triage.clusters).toEqual(["conflict-astrolift", "eks-us-west-2"]);
    expect(triage.mine).toBe(true);
    const digest = AGENT_ROWS.find((a) => a.slug === "hourly-digest")!;
    expect(digest.model).toBe("gateway");
    expect(digest.nextScheduledAt).toBe(NEXT_DIGEST);
  });

  it("reads no spec as no model and no runtime, and an unknown status as idle", () => {
    const row = toAgentRow(agent("x", { status: "something-new", modelSource: null }));
    expect(row.model).toBeNull();
    expect(row.runtime).toBeNull();
    expect(row.status).toBe("idle");
    expect(row.nextScheduledAt).toBeNull();
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
