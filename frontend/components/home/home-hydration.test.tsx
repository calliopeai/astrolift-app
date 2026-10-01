import { readFileSync } from "node:fs";
import path from "node:path";
import { act, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ActivityFeed } from "@/components/ActivityFeed";
import type { AstroliftActivityItem } from "@/graphql/operations/operations.types";
import { locales } from "@/i18n/config";
import { BOTH, homeProps } from "./fixtures";
import { HomeScreen } from "./HomeScreen";
import { AgentRunsPanelView } from "./panels/AgentRunsPanel";
import { AGENT_RUNS } from "./panels/builder-operator.fixtures";
import { HOME_PANELS } from "./registry";

const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
const now = new Date("2026-09-30T12:00:00Z");
const item: AstroliftActivityItem = {
  id: "exact-event-id",
  action: "UNKNOWN_ACTION",
  actorDisplay: "RAW_ACTOR",
  eventType: "unknown.event",
  targetKind: "unknown-resource",
  targetLabel: "RAW_TARGET",
  targetHref: "/apps/exact-app/secrets",
  payload: {},
  occurredAt: "2026-09-30T00:30:00Z",
};
const feed = {
  items: [item],
  loading: false,
  error: null,
  hasMore: false,
  loadingMore: false,
  onLoadMore: vi.fn(),
};

describe("Home and activity request context", () => {
  it.each(locales)("%s hydrates the same fixed request clock and time zone", async (locale) => {
    const errors = vi.fn();
    const props = homeProps(BOTH);
    const tree = (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="America/Los_Angeles"
        now={now}
        onError={errors}
      >
        <HomeScreen {...props} firstSignIn onLayoutChange={vi.fn()} />
        <AgentRunsPanelView
          panel={HOME_PANELS["agent-runs"]}
          rows={AGENT_RUNS}
          count={1234}
          loading={false}
          error={null}
          onRetry={vi.fn()}
        />
        <ActivityFeed {...feed} />
      </NextIntlClientProvider>
    );
    const container = document.createElement("div");
    document.body.appendChild(container);
    container.innerHTML = renderToString(tree);
    const before = container.innerHTML;
    const recoverable = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, tree, { onRecoverableError: recoverable });
      });
      expect(container.innerHTML).toBe(before);
      expect(
        screen.getByRole("group", { name: catalogs[locale].home.question })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("region", { name: catalogs[locale].overview.activity.title })
      ).toHaveTextContent(catalogs[locale].shared.feed.yesterday);
      expect(screen.getByRole("link", { name: /RAW_ACTOR/ })).toHaveAttribute(
        "href",
        item.targetHref
      );
      expect(errors).not.toHaveBeenCalled();
      expect(recoverable).not.toHaveBeenCalled();
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });

  it("uses a changed locale and request time zone without changing event identity or paging", () => {
    const errors = vi.fn();
    const tree = (locale: string, timeZone: string) => (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone={timeZone}
        now={now}
        onError={errors}
      >
        <ActivityFeed {...feed} />
      </NextIntlClientProvider>
    );
    const view = render(tree("de", "UTC"));
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent(
      catalogs.de.shared.feed.today
    );
    view.rerender(tree("fr", "America/Los_Angeles"));
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent(
      catalogs.fr.shared.feed.yesterday
    );
    expect(
      screen.getByRole("region", { name: catalogs.fr.overview.activity.title })
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /RAW_ACTOR/ })).toHaveAttribute(
      "href",
      item.targetHref
    );
    expect(feed.onLoadMore).not.toHaveBeenCalled();
    expect(errors).not.toHaveBeenCalled();
  });
});
