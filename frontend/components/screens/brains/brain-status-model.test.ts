import { describe, expect, it } from "vitest";

import {
  freshnessOf,
  healthOf,
  ingestState,
  mcpUrl,
  resourceInFlight,
  resourceTone,
  shortSha,
  statusEnvironment,
} from "./brain-status-model";
import { BEHIND, DEPLOYING, HEALTHY, NOT_DEPLOYED, UNKNOWN_VALUES } from "./brain-status.fixtures";

describe("which environment a layer's status is read in", () => {
  it("is production when it is served there, or when nothing is served yet", () => {
    expect(statusEnvironment([{ name: "staging" }, { name: "production" }])).toBe("production");
    expect(statusEnvironment([])).toBe("production");
  });

  it("is the first served environment otherwise", () => {
    expect(statusEnvironment([{ name: "staging" }, { name: "qa" }])).toBe("staging");
  });
});

describe("health", () => {
  it("is the latest deployment's state", () => {
    expect(healthOf(HEALTHY)).toEqual({ kind: "deployment", status: "running" });
    expect(healthOf(DEPLOYING)).toEqual({ kind: "deployment", status: "deploying" });
  });

  it("says when there is no environment or no deployment", () => {
    expect(healthOf(null)).toEqual({ kind: "noEnvironment" });
    expect(healthOf(NOT_DEPLOYED)).toEqual({ kind: "notDeployed" });
  });

  it("keeps a state this build does not know as the API's value", () => {
    expect(healthOf(UNKNOWN_VALUES)).toEqual({ kind: "other", status: "paused_by_operator" });
  });
});

describe("freshness", () => {
  it("reads the compile, the branch head and the last ingest", () => {
    expect(freshnessOf(HEALTHY)).toEqual({
      compiledAt: "2026-10-15T06:12:00Z",
      behind: false,
      ingest: {
        state: "succeeded",
        at: "2026-10-15T06:00:00Z",
        url: "https://github.example.test/acme/platform-brain/actions/runs/101",
      },
      warnings: [],
    });
    const behind = freshnessOf(BEHIND);
    expect(behind.behind).toBe(true);
    expect(behind.compiledAt).toBeNull();
    expect(behind.ingest?.state).toBe("failed");
    expect(behind.warnings).toHaveLength(1);
  });

  it("reads a run as the host reports it", () => {
    expect(ingestState({ status: "queued", conclusion: "" })).toBe("running");
    expect(ingestState({ status: "in_progress", conclusion: "" })).toBe("running");
    expect(ingestState({ status: "completed", conclusion: "success" })).toBe("succeeded");
    expect(ingestState({ status: "completed", conclusion: "timed_out" })).toBe("failed");
    expect(ingestState({ status: "completed", conclusion: "cancelled" })).toBe("other");
  });
});

describe("resources", () => {
  it("colours managed-service and binding states, unknown ones muted", () => {
    expect(resourceTone("active")).toBe("ok");
    expect(resourceTone("provisioning")).toBe("info");
    expect(resourceTone("unbound")).toBe("warn");
    expect(resourceTone("failed")).toBe("error");
    expect(resourceTone("declared")).toBe("muted");
    expect(resourceInFlight("updating")).toBe(true);
    expect(resourceInFlight("active")).toBe(false);
  });
});

it("prints commits short and MCP URLs under the endpoint", () => {
  expect(shortSha("4f1c2a9e7b3d")).toBe("4f1c2a9");
  expect(shortSha(null)).toBe("");
  expect(mcpUrl("https://brain.example.test/")).toBe("https://brain.example.test/mcp");
});
