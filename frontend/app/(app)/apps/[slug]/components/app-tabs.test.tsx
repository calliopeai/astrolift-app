import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { activeSection, redirectTarget } from "@/components/screens/apps/detail/app-tabs-model";

import { AppTabs } from "./app-tabs";

/**
 * Spec 44 §5.2: the app detail is one row of tabs named by function, and
 * every former route (topology, previews, jobs, observability, shell,
 * console, commands, members, tokens, security, config, manifest, webhooks,
 * environments) lands in exactly one of them. The tab table is a plain data
 * structure, so a stray edit regresses the IA without breaking anything that
 * would fail elsewhere; these pin it.
 */

const pathname = vi.hoisted(() => ({ value: "/apps/acme" }));
const chrome = vi.hoisted(() => ({
  value: { basePath: "/apps", agentShell: false, framed: false },
}));

vi.mock("next/navigation", () => ({ usePathname: () => pathname.value }));
vi.mock("next-intl", () => ({
  // Render the raw i18n key so assertions read as tab identity, not copy.
  useTranslations: () => (key: string) => key,
}));
vi.mock("@/lib/app-chrome-context", () => ({ useAppChrome: () => chrome.value }));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    "aria-current"?: "page";
  }) => (
    <a href={href} aria-current={rest["aria-current"]}>
      {children}
    </a>
  ),
}));

function tabs(): { labels: string[]; hrefs: string[]; active: string[] } {
  const links = within(screen.getByLabelText("ariaLabel")).getAllByRole("link");
  return {
    labels: links.map((l) => l.textContent ?? ""),
    hrefs: links.map((l) => l.getAttribute("href") ?? ""),
    active: links
      .filter((l) => l.getAttribute("aria-current") === "page")
      .map((l) => l.textContent ?? ""),
  };
}

function renderAt(path: string, active?: string) {
  pathname.value = path;
  render(<AppTabs slug="acme" active={active} />);
}

describe("AppTabs: one row by function (spec 44 §5.2)", () => {
  it("is exactly the eight tabs, in order, each its own route", () => {
    renderAt("/apps/acme");
    const { labels, hrefs } = tabs();
    expect(labels).toEqual([
      "overview",
      "deployments",
      "workloads",
      "logsMetrics",
      "domains",
      "secrets",
      "access",
      "settings",
    ]);
    expect(hrefs).toEqual([
      "/apps/acme",
      "/apps/acme/deployments",
      "/apps/acme/workloads",
      "/apps/acme/logs",
      "/apps/acme/domains",
      "/apps/acme/secrets",
      "/apps/acme/access",
      "/apps/acme/settings",
    ]);
  });

  it("has no BROCS pillar bar", () => {
    renderAt("/apps/acme");
    expect(screen.queryByLabelText("pillarAriaLabel")).toBeNull();
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
  });

  it.each([
    ["/apps/acme", "overview"],
    ["/apps/acme/topology", "overview"],
    ["/apps/acme/deployments", "deployments"],
    ["/apps/acme/previews", "deployments"],
    ["/apps/acme/workloads", "workloads"],
    ["/apps/acme/workloads/web", "workloads"],
    ["/apps/acme/jobs", "workloads"],
    ["/apps/acme/managed-services", "workloads"],
    ["/apps/acme/logs", "logsMetrics"],
    ["/apps/acme/observability", "logsMetrics"],
    ["/apps/acme/shell", "logsMetrics"],
    ["/apps/acme/console", "logsMetrics"],
    ["/apps/acme/commands", "logsMetrics"],
    ["/apps/acme/domains", "domains"],
    ["/apps/acme/secrets", "secrets"],
    ["/apps/acme/access", "access"],
    ["/apps/acme/members", "access"],
    ["/apps/acme/tokens", "access"],
    ["/apps/acme/security", "access"],
    ["/apps/acme/settings", "settings"],
    ["/apps/acme/config", "settings"],
    ["/apps/acme/manifest", "settings"],
    ["/apps/acme/webhooks", "settings"],
    ["/apps/acme/environments", "settings"],
  ])("%s highlights %s, and only it", (path, tab) => {
    renderAt(path);
    expect(tabs().active).toEqual([tab]);
  });

  it("resolves a former route's name as `active` on a path it does not know", () => {
    renderAt("/somewhere/else", "console");
    expect(tabs().active).toEqual(["logsMetrics"]);
  });

  it("renders nothing inside the app frame, which draws the row itself", () => {
    chrome.value = { basePath: "/apps", agentShell: false, framed: true };
    renderAt("/apps/acme/logs");
    expect(screen.queryByRole("navigation")).toBeNull();
    chrome.value = { basePath: "/apps", agentShell: false, framed: false };
  });

  it("renders nothing inside the agent shell, which has its own tabs", () => {
    chrome.value = { basePath: "/agents", agentShell: true, framed: false };
    renderAt("/agents/acme/secrets");
    expect(screen.queryByRole("navigation")).toBeNull();
    chrome.value = { basePath: "/apps", agentShell: false, framed: false };
  });
});

describe("sections and redirects", () => {
  it("picks a tab's section from the query, defaulting to the first", () => {
    expect(activeSection("logs", {})).toBe("logs");
    expect(activeSection("logs", { section: "console" })).toBe("console");
    expect(activeSection("logs", { section: "nope" })).toBe("logs");
    expect(activeSection("deployments", { view: "previews" })).toBe("previews");
    expect(activeSection("workloads", { kind: "cronjob" })).toBe("jobs");
    expect(activeSection("workloads", { section: "managed-services" })).toBe("managed-services");
    expect(activeSection("access", { section: "edge" })).toBe("edge");
    expect(activeSection("settings", { section: "danger-zone" })).toBe("danger-zone");
  });

  it("sends a former route to its tab and section, keeping the old query", () => {
    expect(redirectTarget("acme", "overview", null, {})).toBe("/apps/acme");
    expect(redirectTarget("acme", "logs", "console", { pod: "web-1" })).toBe(
      "/apps/acme/logs?pod=web-1&section=console"
    );
    // The default section adds nothing: the old /console link lands on Logs.
    expect(redirectTarget("acme", "logs", "logs", { pod: "web-1" })).toBe(
      "/apps/acme/logs?pod=web-1"
    );
    expect(redirectTarget("acme", "deployments", "previews", {})).toBe(
      "/apps/acme/deployments?view=previews"
    );
    expect(redirectTarget("acme", "workloads", "jobs", {})).toBe(
      "/apps/acme/workloads?kind=cronjob"
    );
    expect(redirectTarget("acme", "access", "members", { a: ["1", "2"] })).toBe(
      "/apps/acme/access?a=1&a=2"
    );
    expect(redirectTarget("acme", "settings", "configuration", {})).toBe(
      "/apps/acme/settings?section=configuration"
    );
  });
});
