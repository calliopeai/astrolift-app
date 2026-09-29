import { describe, expect, it } from "vitest";

import { parseListState, effectiveFilters } from "@/components/list/list-state";
import {
  DEPLOY_DEPLOYING,
  DEPLOY_FAILED,
  DEPLOY_PENDING_APPROVAL,
  DEPLOY_RUNNING,
} from "@/components/screens/apps/deployments/app-deployments-logs.fixtures";
import type { AstroliftDeploymentLogEntry } from "@/graphql/lifecycle/lifecycle.types";

import { appsDetailCrumbs, appsListCrumbs, sinceIso } from "./apps-area";
import {
  DEPLOYMENTS_LIST,
  deploymentFailure,
  deploymentSteps,
  deploymentsVariables,
} from "./deployments-list";

const at = (m: number) => new Date(Date.UTC(2026, 8, 28, 12, m)).toISOString();

function entry(status: string, minute: number, message = ""): AstroliftDeploymentLogEntry {
  return {
    id: `${status}-${minute}`,
    deploymentId: "d",
    status,
    message,
    detail: {},
    occurredAt: at(minute),
  };
}

const NOW = Date.parse(at(30));

function varsFor(qs: string) {
  const state = parseListState(DEPLOYMENTS_LIST, qs);
  return deploymentsVariables(effectiveFilters(DEPLOYMENTS_LIST, state), state, NOW);
}

describe("DEPLOYMENTS_LIST", () => {
  it("leads with All and Mine, then the drafted views, cursor paged", () => {
    expect(DEPLOYMENTS_LIST.views.map((v) => v.label)).toEqual([
      "All",
      "Mine",
      "Waiting approval",
      "Failed",
      "Today",
    ]);
    expect(DEPLOYMENTS_LIST.paging).toBe("cursor");
  });
});

describe("deploymentsVariables", () => {
  it("sends app, environment, status, search and the list's order to the server", () => {
    expect(varsFor("app=storefront&environment=prod&status=failed&q=4f2a")).toEqual({
      appSlug: "storefront",
      environmentName: "prod",
      statuses: ["failed"],
      search: "4f2a",
      filter: null,
      sort: "-started",
      limit: 25,
      after: null,
    });
  });

  it("Waiting approval is the pending_approval status", () => {
    expect(varsFor("view=waiting").statuses).toEqual(["pending_approval"]);
  });

  it("Mine is triggeredBy me, trigger is the trigger kind", () => {
    expect(varsFor("view=mine").filter).toEqual({ triggeredBy: ["me"] });
    expect(varsFor("trigger=push").filter).toEqual({ triggerKind: ["push"] });
  });

  it("Today starts at the viewer's local midnight; since windows count back from now", () => {
    expect(varsFor("view=today").filter).toEqual({ startedAfter: sinceIso("today", NOW) });
    expect(varsFor("since=24h").filter).toEqual({
      startedAfter: new Date(NOW - 24 * 3600_000).toISOString(),
    });
  });

  it("asks for one page of the chosen size, never a wider one", () => {
    expect(varsFor("view=mine&pageSize=50").limit).toBe(50);
    expect(varsFor("trigger=push").limit).toBe(25);
  });
});

describe("deploymentSteps", () => {
  const states = (steps: { id: string; state: string }[]) =>
    Object.fromEntries(steps.map((s) => [s.id, s.state]));

  it("a live deploy: image done, rollout done, health ok", () => {
    const log = [entry("pending", 0), entry("deploying", 1), entry("running", 3, "3 of 3 ready")];
    expect(states(deploymentSteps(DEPLOY_RUNNING, log, 0))).toEqual({
      build: "ok",
      push: "ok",
      rollout: "ok",
      health: "ok",
    });
  });

  it("mid-rollout: the rollout runs and its clock ticks", () => {
    const log = [entry("pending", 0), entry("deploying", 1)];
    const steps = deploymentSteps(DEPLOY_DEPLOYING, log, Date.parse(at(2)));
    expect(states(steps)).toMatchObject({ rollout: "running", health: "pending" });
    expect(steps.find((s) => s.id === "rollout")?.durationMs).toBe(60_000);
  });

  it("a probe failure fails health, not the rollout", () => {
    const log = [entry("deploying", 1), entry("failed", 4, "Readiness probe failed on 2 of 3")];
    expect(states(deploymentSteps(DEPLOY_FAILED, log, 0))).toMatchObject({
      rollout: "ok",
      health: "failed",
    });
  });

  it("any other failure fails the rollout and skips health", () => {
    const log = [entry("deploying", 1), entry("failed", 2, "Image pull denied")];
    const d = { ...DEPLOY_FAILED, statusReason: "Image pull denied" };
    expect(states(deploymentSteps(d, log, 0))).toMatchObject({
      rollout: "failed",
      health: "skipped",
    });
  });

  it("a build error fails build and skips everything after", () => {
    const d = { ...DEPLOY_FAILED, buildError: "npm ERR! code ERESOLVE" };
    expect(states(deploymentSteps(d, [], 0))).toEqual({
      build: "failed",
      push: "skipped",
      rollout: "skipped",
      health: "skipped",
    });
  });

  it("waiting on approvals shows an approval step in progress", () => {
    const steps = deploymentSteps(DEPLOY_PENDING_APPROVAL, [entry("pending_approval", 0)], 0);
    expect(steps.find((s) => s.id === "approval")?.state).toBe("running");
  });
});

describe("deploymentFailure", () => {
  it("is null unless failed", () => {
    expect(deploymentFailure(DEPLOY_RUNNING, [])).toBeNull();
  });

  it("prefers the abort reason, then the status reason, then the build error", () => {
    expect(deploymentFailure({ ...DEPLOY_FAILED, abortedReason: "bad tag" }, [])?.reason).toBe(
      "bad tag"
    );
    const build = { ...DEPLOY_FAILED, statusReason: "", buildError: "exit 1" };
    expect(deploymentFailure(build, [])).toEqual({ title: "Build failed", reason: "exit 1" });
    const rolled = [entry("deploying", 1), entry("failed", 2)];
    expect(deploymentFailure(DEPLOY_FAILED, rolled)).toEqual({
      title: "Deployment failed",
      reason: DEPLOY_FAILED.statusReason,
    });
  });
});

describe("apps crumbs", () => {
  it("a list starts at the area, with this function checked in the switcher", () => {
    const [area, fn] = appsListCrumbs("deployments");
    expect(area.label).toBe("Apps");
    expect(area.switcher?.find((o) => o.active)?.href).toBe("/deployments");
    expect(fn).toEqual({ label: "Deployments" });
  });

  it("a detail starts at its function", () => {
    const crumbs = appsDetailCrumbs(
      "deployments",
      { label: "storefront" },
      { label: "deploy 4f2a9c1e" }
    );
    expect(crumbs.map((c) => c.label)).toEqual(["Deployments", "storefront", "deploy 4f2a9c1e"]);
    expect(crumbs[0].switcher?.some((o) => o.href === "/jobs")).toBe(true);
  });
});
