import { composeStories, setProjectAnnotations } from "@storybook/react";
import { cleanup, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { runStory } from "@/test/run-story";
import * as preview from "../../../.storybook/preview";
import {
  DEGRADED_CONNECTION,
  FULL_ACCESS,
  HEALTH_COUNTS,
  HEALTHY_CONNECTION,
  NO_ACCESS,
  STALE_CONNECTION,
} from "./zentinelle-fixtures";
import {
  gatewayHealth,
  isDegraded,
  scopeVariables,
  tightestBudget,
  visibleTabs,
  windowStart,
} from "./zentinelle-model";
import * as overviewStories from "./ZentinelleOverview.stories";
import * as stories from "./ZentinelleScreen.stories";

setProjectAnnotations(preview);
const s = composeStories(stories);
const o = composeStories(overviewStories);

async function render(Story: Parameters<typeof runStory>[0]) {
  const canvas = document.createElement("div");
  document.body.append(canvas);
  await runStory(Story, canvas);
  return { page: within(canvas), done: () => (cleanup(), canvas.remove()) };
}

async function check(
  Story: Parameters<typeof runStory>[0],
  assert: (page: ReturnType<typeof within>) => void
) {
  const { page, done } = await render(Story);
  try {
    assert(page);
  } finally {
    done();
  }
}

describe("Zentinelle screen states (#1888)", () => {
  it("offers the connect form to an admin when not connected, and no tabs", async () => {
    await check(s.NotConnected, (page) => {
      expect(page.getByLabelText("Enrollment code")).toBeEnabled();
      expect(page.queryByRole("navigation", { name: "Zentinelle sections" })).toBeNull();
    });
  });

  it("locks the form while connecting", async () => {
    await check(s.Connecting, (page) => {
      expect(page.getByLabelText("Enrollment code")).toBeDisabled();
      expect(page.getByRole("button", { name: "Connecting…" })).toBeDisabled();
    });
  });

  it("tells a viewer who cannot connect to ask an admin", async () => {
    await check(s.NotConnectedViewer, (page) => {
      expect(page.getByText("Not connected to Zentinelle")).toBeVisible();
      expect(page.queryByLabelText("Enrollment code")).toBeNull();
    });
  });

  it("shows health, every tab and the admin actions when connected", async () => {
    await check(s.ConnectedHealthy, (page) => {
      const tabs = page.getByRole("navigation", { name: "Zentinelle sections" });
      expect(within(tabs).getAllByRole("link")).toHaveLength(5);
      expect(page.getByText("2 of 2")).toBeVisible();
      expect(page.getByRole("button", { name: "Disconnect" })).toBeVisible();
      expect(page.queryByText(/need attention/)).toBeNull();
    });
  });

  it("shows Zentinelle's version, blocks, open approvals and the tightest budget (#2436)", async () => {
    await check(s.ConnectedHealthy, (page) => {
      expect(page.getByText("1.5.0")).toBeVisible();
      expect(page.getByText("37")).toBeVisible();
      expect(page.getByText("Open approvals")).toBeVisible();
      expect(page.getByText("82% used")).toBeVisible();
      expect(page.getByText(/\$412\.40 of \$500\.00 \(Monthly model budget\)/)).toBeVisible();
      expect(page.getAllByText(/^0\.9\.1, seen /)).toHaveLength(2);
    });
  });

  it("says not available, rather than failing, for an older Zentinelle", async () => {
    await check(o.OlderZentinelle, (page) => {
      expect(page.getAllByText("Not available")).toHaveLength(4);
      expect(page.getAllByText("Version and last seen not available")).toHaveLength(2);
      expect(page.queryByText(/Could not read/)).toBeNull();
      expect(page.queryByText(/need attention/)).toBeNull();
    });
  });

  it("flags a gateway Zentinelle has not heard from", async () => {
    await check(o.StaleGateway, (page) => {
      expect(page.getByText(/need attention/)).toBeVisible();
      expect(page.getByText("Gateway not heard from")).toBeVisible();
      expect(page.getByText("Not reported yet")).toBeVisible();
    });
  });

  it("flags a degraded gateway with its error", async () => {
    await check(s.GatewayDegraded, (page) => {
      expect(page.getByText(/need attention/)).toBeVisible();
      expect(page.getByText(/ImagePullBackOff/)).toBeVisible();
    });
  });

  it("shows nothing to a viewer without a Zentinelle permission", async () => {
    await check(s.NoPermission, (page) => {
      expect(page.getByText("No Zentinelle access")).toBeVisible();
      expect(page.queryByRole("button")).toBeNull();
    });
  });

  it("shows the upstream error with a retry", async () => {
    await check(s.UpstreamError, (page) => {
      expect(page.getByText(/connection refused/)).toBeVisible();
    });
    await check(s.UsageUpstreamError, (page) => {
      expect(page.getByText("Could not read usage from Zentinelle")).toBeVisible();
    });
  });

  it("gives a developer usage and policies only, with no admin actions", async () => {
    await check(s.DeveloperView, (page) => {
      const tabs = page.getByRole("navigation", { name: "Zentinelle sections" });
      expect(
        within(tabs)
          .getAllByRole("link")
          .map((l) => l.textContent)
      ).toEqual(["Overview", "Usage and cost", "Policies"]);
      expect(page.queryByRole("button", { name: "Disconnect" })).toBeNull();
    });
  });

  it("hides audit export without zentinelle.audit_export", async () => {
    await check(s.AuditTab, (page) => {
      expect(page.getByRole("button", { name: "Export CSV" })).toBeVisible();
    });
    await check(s.AuditNoExport, (page) => {
      expect(page.queryByRole("button", { name: "Export CSV" })).toBeNull();
    });
  });
});

describe("Zentinelle model", () => {
  it("shows only the overview until connected", () => {
    expect(visibleTabs(FULL_ACCESS, false)).toEqual(["overview"]);
    expect(visibleTabs(NO_ACCESS, true)).toEqual([]);
    expect(visibleTabs({ ...NO_ACCESS, audit: true }, true)).toEqual(["audit"]);
  });

  it("reads each gateway's health", () => {
    const [ok, broken, pending, off] = DEGRADED_CONNECTION.clusters;
    expect(gatewayHealth(ok, true)).toBe("healthy");
    expect(gatewayHealth(broken, true)).toBe("error");
    expect(gatewayHealth(pending, true)).toBe("pending");
    expect(gatewayHealth(off, true)).toBe("off");
    expect(gatewayHealth(ok, false)).toBe("off");
    expect(isDegraded(HEALTHY_CONNECTION)).toBe(false);
    expect(isDegraded(DEGRADED_CONNECTION)).toBe(true);
  });

  it("reads a deployed gateway not heard from in five minutes as stale", () => {
    const [ok] = HEALTHY_CONNECTION.clusters;
    const now = Date.parse("2026-10-17T12:00:00Z");
    const seen = (minutes: number) => ({
      ...ok,
      lastSeenAt: new Date(now - minutes * 60_000).toISOString(),
    });
    expect(gatewayHealth(seen(4), true, now)).toBe("healthy");
    expect(gatewayHealth(seen(6), true, now)).toBe("stale");
    // Nothing reported (an older Zentinelle) stays as before.
    expect(gatewayHealth({ ...ok, lastSeenAt: null }, true, now)).toBe("healthy");
    expect(gatewayHealth({ ...seen(60), gatewayDeployed: false }, true, now)).toBe("pending");
    expect(isDegraded(STALE_CONNECTION)).toBe(true);
  });

  it("picks the budget closest to its limit", () => {
    expect(tightestBudget(HEALTH_COUNTS.budgets ?? [])?.name).toBe("Monthly model budget");
    expect(tightestBudget([])).toBeNull();
  });

  it("names one scope at most and anchors windows", () => {
    expect(scopeVariables({ kind: "team", id: "t" })).toEqual({
      agentSlug: null,
      projectId: null,
      teamId: "t",
    });
    const now = new Date("2026-10-16T12:00:00Z");
    expect(windowStart("month", now)).toBe("2026-10-01T00:00:00.000Z");
    expect(windowStart("24h", now)).toBe("2026-10-15T12:00:00.000Z");
  });
});
