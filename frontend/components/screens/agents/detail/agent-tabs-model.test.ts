import { describe, expect, it } from "vitest";

import {
  activeAgentSection,
  AGENT_FORMER_ROUTES,
  AGENT_TAB_SECTIONS,
  AGENT_TABS,
  agentRedirectTarget,
  agentSectionHref,
  agentTabHref,
  agentTabs,
  resolveAgentTab,
} from "./agent-tabs-model";

describe("agent tabs", () => {
  it("is one row of eight tabs, named by function, in spec 44 §5.2's order", () => {
    expect(agentTabs("support-bot", "/agents/support-bot").map((t) => t.label)).toEqual([
      "Overview",
      "Runs",
      "Configuration",
      "Skills & tools",
      "Logs & metrics",
      "Secrets",
      "Access",
      "Settings",
    ]);
  });

  it("puts Overview at the agent's root and every other tab on its own route", () => {
    expect(agentTabHref("support-bot", "overview")).toBe("/agents/support-bot");
    expect(agentTabHref("support-bot", "skills")).toBe("/agents/support-bot/skills");
    expect(agentTabHref("support-bot", "logs")).toBe("/agents/support-bot/logs");
  });

  it("lights the tab that absorbed a former route", () => {
    expect(resolveAgentTab("/agents/support-bot", "support-bot")).toBe("overview");
    expect(resolveAgentTab("/agents/support-bot/run", "support-bot")).toBe("runs");
    expect(resolveAgentTab("/agents/support-bot/control", "support-bot")).toBe("configuration");
    expect(resolveAgentTab("/agents/support-bot/observability", "support-bot")).toBe("logs");
    expect(resolveAgentTab("/agents/support-bot/secure", "support-bot")).toBe("access");
    expect(resolveAgentTab("/agents/support-bot/domains", "support-bot")).toBe("settings");
    expect(resolveAgentTab("/agents/support-bot/unknown", "support-bot")).toBe("overview");
  });

  it("marks exactly one tab active", () => {
    const tabs = agentTabs("support-bot", "/agents/support-bot/access");
    expect(tabs.filter((t) => t.active).map((t) => t.key)).toEqual(["access"]);
  });

  it("encodes the slug as usePathname does", () => {
    expect(agentTabHref("a b", "runs")).toBe("/agents/a%20b/runs");
    expect(resolveAgentTab("/agents/a%20b/runs", "a b")).toBe("runs");
  });
});

describe("agent tab sections", () => {
  it("picks the section in ?section=, else the tab's first", () => {
    expect(activeAgentSection("logs", {})).toBe("live");
    expect(activeAgentSection("logs", { section: "metrics" })).toBe("metrics");
    expect(activeAgentSection("logs", { section: "nope" })).toBe("live");
    expect(activeAgentSection("access", { section: ["guardrails", "x"] })).toBe("guardrails");
  });

  it("links the default section to the bare tab route", () => {
    expect(agentSectionHref("support-bot", "configuration", "build")).toBe(
      "/agents/support-bot/configuration"
    );
    expect(agentSectionHref("support-bot", "configuration", "model-access")).toBe(
      "/agents/support-bot/configuration?section=model-access"
    );
  });

  it("keeps Settings on the SettingsPage ids, with one Danger zone last", () => {
    expect(AGENT_TAB_SECTIONS.settings?.map((s) => s.id)).toEqual([
      "general",
      "environments",
      "domains",
      "managed-services",
      "danger-zone",
    ]);
  });
});

describe("former agent routes", () => {
  it("redirects every former route to a section that exists", () => {
    for (const [route, { tab, section }] of Object.entries(AGENT_FORMER_ROUTES)) {
      expect(
        AGENT_TABS.some((t) => t.key === tab),
        route
      ).toBe(true);
      if (section) {
        expect(
          AGENT_TAB_SECTIONS[tab]?.some((s) => s.id === section),
          `${route} → ${tab}/${section}`
        ).toBe(true);
      }
    }
  });

  it("folds observe/observability into Logs & metrics and secure/security into Access", () => {
    expect(agentRedirectTarget("observe", "support-bot", {})).toBe("/agents/support-bot/logs");
    expect(agentRedirectTarget("observability", "support-bot", {})).toBe(
      "/agents/support-bot/logs?section=metrics"
    );
    expect(agentRedirectTarget("security", "support-bot", {})).toBe(
      "/agents/support-bot/access?section=security"
    );
    expect(agentRedirectTarget("secure", "support-bot", {})).toBe(
      "/agents/support-bot/access?section=guardrails"
    );
  });

  it("sends overview and run to their tabs, keeping the old query but not its section", () => {
    expect(agentRedirectTarget("overview", "support-bot", {})).toBe("/agents/support-bot");
    expect(agentRedirectTarget("run", "support-bot", { open: "7f3c" })).toBe(
      "/agents/support-bot/runs?open=7f3c"
    );
    expect(agentRedirectTarget("members", "support-bot", { q: "ada", section: "x" })).toBe(
      "/agents/support-bot/access?q=ada"
    );
  });
});
