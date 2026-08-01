import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppTabs } from "./app-tabs";

/**
 * #1247 moved the mutating pod surfaces out of Observe: acting on a running
 * pod is control, not observability. These pin the resulting grouping, plus
 * the legacy `/console` alias — the tab table is a plain data structure, so
 * a stray edit regresses the IA without breaking anything that would fail
 * elsewhere.
 */

const pathname = vi.hoisted(() => ({ value: "/apps/acme" }));

vi.mock("next/navigation", () => ({ usePathname: () => pathname.value }));
vi.mock("next-intl", () => ({
  // Render the raw i18n key so assertions read as tab identity, not copy.
  useTranslations: () => (key: string) => key,
}));
vi.mock("./app-chrome-context", () => ({
  useAppChrome: () => ({ basePath: "/apps", agentShell: false }),
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

/** The sub-tab row for whichever pillar the current pathname selects. */
function subTabs(): { labels: string[]; hrefs: string[] } {
  const nav = screen.getByLabelText("ariaLabel");
  const links = within(nav).getAllByRole("link");
  return {
    labels: links.map((l) => l.textContent ?? ""),
    hrefs: links.map((l) => l.getAttribute("href") ?? ""),
  };
}

function renderAt(path: string, active?: "console") {
  pathname.value = path;
  render(<AppTabs slug="acme" active={active} />);
}

describe("AppTabs grouping (#1247)", () => {
  it("keeps Observe read-only — logs, and no way to act on a pod", () => {
    renderAt("/apps/acme/logs");
    const { labels, hrefs } = subTabs();

    expect(labels).toEqual(["observability", "logs"]);
    expect(hrefs).toContain("/apps/acme/logs");
    // The whole point of the split: nothing that runs anything sits here.
    expect(labels).not.toContain("shell");
    expect(labels).not.toContain("commands");
    expect(labels).not.toContain("console");
  });

  it("puts both ways of running something on a pod under Control", () => {
    renderAt("/apps/acme/shell");
    const { labels, hrefs } = subTabs();

    expect(labels.slice(0, 2)).toEqual(["shell", "commands"]);
    expect(hrefs).toContain("/apps/acme/shell");
    expect(hrefs).toContain("/apps/acme/commands");
  });

  it("no longer offers commands under Run", () => {
    renderAt("/apps/acme/deployments");
    expect(subTabs().labels).not.toContain("commands");
  });

  it("resolves the legacy console key to Observe › logs", () => {
    // Anything still passing `active="console"` must land on the logs half,
    // not fall through to the Run › Overview default.
    renderAt("/apps/acme/console", "console");
    const { labels } = subTabs();
    expect(labels).toEqual(["observability", "logs"]);
  });

  it("selects the commands tab from its own pathname", () => {
    // `commands` changed pillar, so its pathname must now resolve to Control
    // rather than the Run row it used to live in.
    renderAt("/apps/acme/commands");
    expect(subTabs().labels).toContain("shell");
  });
});
