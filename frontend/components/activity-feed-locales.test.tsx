import { readFileSync } from "node:fs";
import path from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";
import type { AstroliftActivityItem } from "@/graphql/operations/operations.types";
import { ActivityFeed } from "./ActivityFeed";

const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
const now = new Date("2026-09-30T12:00:00Z");
const base = { loading: false, error: null, hasMore: true, loadingMore: false };
const event: AstroliftActivityItem = {
  id: "immutable-activity-id",
  action: "opaque-action",
  actorDisplay: "SERVER_ACTOR",
  eventType: "secret.unknown",
  targetKind: "secret",
  targetLabel: "SERVER_TARGET",
  targetHref: "/apps/exact-app/secrets",
  payload: {},
  occurredAt: "2026-09-30T11:00:00Z",
};

afterEach(() => vi.useRealTimers());

describe("ActivityFeed configured locales", () => {
  it.each(locales)(
    "%s distinguishes empty, loading, first-page error and paging states",
    (locale) => {
      const messages = catalogs[locale],
        more = vi.fn(),
        retry = vi.fn(),
        errors = vi.fn();
      const tree = (props: Partial<React.ComponentProps<typeof ActivityFeed>>) => (
        <NextIntlClientProvider
          locale={locale}
          messages={messages}
          timeZone="UTC"
          now={now}
          onError={errors}
        >
          <ActivityFeed {...base} items={[]} onLoadMore={more} onRetry={retry} {...props} />
        </NextIntlClientProvider>
      );
      const view = render(tree({}));
      expect(screen.getByText(messages.overview.activity.emptyTitle)).toBeInTheDocument();
      expect(screen.getByText(messages.overview.activity.emptyDescription)).toBeInTheDocument();
      view.rerender(tree({ loading: true }));
      expect(screen.getByRole("status")).toHaveTextContent(messages.shared.feed.loading);
      expect(screen.queryByText(messages.overview.activity.emptyTitle)).not.toBeInTheDocument();
      view.rerender(tree({ error: "RAW_INITIAL_FAILURE" }));
      expect(screen.getByRole("alert")).toHaveTextContent(messages.overview.activity.errorTitle);
      expect(screen.getByRole("alert")).toHaveTextContent("RAW_INITIAL_FAILURE");
      fireEvent.click(screen.getByRole("button", { name: messages.shared.feed.retry }));
      expect(retry).toHaveBeenCalledTimes(1);
      view.rerender(tree({ items: [event], loadingMore: true }));
      const button = screen.getByRole("button", { name: messages.overview.activity.loadingMore });
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(more).not.toHaveBeenCalled();
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it.each(locales)(
    "%s uses the configured locale for frame/time and preserves actual links, values and cursor/retry callbacks",
    (locale) => {
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "overview.activity",
      });
      const errors = vi.fn(),
        more = vi.fn(),
        retry = vi.fn();
      const view = render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          now={now}
          onError={errors}
        >
          <ActivityFeed
            {...base}
            error="RAW_PROVIDER_FAILURE"
            items={[
              event,
              { ...event, id: "invalid", targetHref: null, occurredAt: "RAW_INVALID_TIME" },
            ]}
            onLoadMore={more}
            onRetry={retry}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("region", { name: t("title") })).toBeInTheDocument();
      expect(
        screen.getByText(
          new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(-1, "hour")
        )
      ).toBeInTheDocument();
      expect(screen.getByText("RAW_INVALID_TIME")).toBeInTheDocument();
      expect(screen.getAllByText("SERVER_ACTOR")).toHaveLength(2);
      expect(screen.getAllByText("SERVER_TARGET")).toHaveLength(2);
      expect(screen.getByRole("link")).toHaveAttribute("href", event.targetHref);
      expect(screen.getByRole("alert")).toHaveTextContent("RAW_PROVIDER_FAILURE");
      fireEvent.click(screen.getByRole("button", { name: t("loadMore") }));
      expect(more).toHaveBeenCalledTimes(1);
      expect(
        screen.queryByRole("button", { name: catalogs[locale].shared.feed.retry })
      ).not.toBeInTheDocument();
      expect(retry).not.toHaveBeenCalled();
      view.rerender(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          now={now}
          onError={errors}
        >
          <ActivityFeed
            {...base}
            hasMore={false}
            error="RAW_PROVIDER_FAILURE"
            items={[event]}
            onLoadMore={more}
            onRetry={retry}
          />
        </NextIntlClientProvider>
      );
      fireEvent.click(screen.getByRole("button", { name: catalogs[locale].shared.feed.retry }));
      expect(retry).toHaveBeenCalledTimes(1);
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it("updates row ages as time passes without fetching another page", () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const more = vi.fn();
    render(
      <NextIntlClientProvider locale="ja" messages={catalogs.ja} timeZone="UTC" now={now}>
        <ActivityFeed {...base} items={[event]} onLoadMore={more} />
      </NextIntlClientProvider>
    );
    expect(
      screen.getByText(new Intl.RelativeTimeFormat("ja", { numeric: "auto" }).format(-1, "hour"))
    ).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(3_600_000));
    expect(
      screen.getByText(new Intl.RelativeTimeFormat("ja", { numeric: "auto" }).format(-2, "hour"))
    ).toBeInTheDocument();
    expect(more).not.toHaveBeenCalled();
  });
});
