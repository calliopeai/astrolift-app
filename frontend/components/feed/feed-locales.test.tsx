import { readFileSync } from "node:fs";
import path from "node:path";
import { fireEvent, render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";

import { Feed } from "./Feed";
import { groupItems } from "./use-feed";

const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
const now = new Date("2026-03-09T06:30:00Z");
const items = [
  { id: "today", at: "2026-03-09T06:00:00Z" },
  { id: "yesterday", at: "2026-03-08T05:30:00Z" },
  { id: "older", at: "2026-03-06T23:30:00Z" },
  { id: "invalid", at: "not-a-date" },
];
const common = {
  label: "CallerActivity",
  renderItem: (item: { id: string; at: string }) => item.id,
  keyOf: (item: { id: string; at: string }) => item.id,
  groupBy: { day: (item: { at: string }) => item.at },
};
function provider(locale: string, children: React.ReactNode, onError = vi.fn()) {
  return render(
    <NextIntlClientProvider
      locale={locale}
      timeZone="America/New_York"
      now={now}
      messages={catalogs[locale]}
      onError={onError}
    >
      {children}
    </NextIntlClientProvider>
  );
}
describe("shared Feed locales", () => {
  it.each(locales)(
    "%s formats actual local calendar days across DST and retains cursor callbacks",
    (locale) => {
      const t = createTranslator({ locale, messages: catalogs[locale], namespace: "shared.feed" });
      const onLoadMore = vi.fn();
      const onError = vi.fn();
      const result = provider(
        locale,
        <Feed {...common} items={items} hasMore onLoadMore={onLoadMore} />,
        onError
      );
      expect(screen.getByRole("heading", { name: t("today") })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: t("yesterday") })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: t("unknownDate") })).toBeInTheDocument();
      const date = new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "America/New_York",
      }).format(new Date(items[2].at));
      expect(screen.getByRole("heading", { name: date })).toBeInTheDocument();
      expect(screen.getByRole("region", { name: "CallerActivity" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: t("loadOlder") }));
      expect(onLoadMore).toHaveBeenCalledOnce();
      expect(onError).not.toHaveBeenCalled();
      result.unmount();
    }
  );
  it.each(locales)(
    "%s distinguishes loading/empty/error, preserves overrides and original diagnostics",
    (locale) => {
      const t = createTranslator({ locale, messages: catalogs[locale], namespace: "shared.feed" });
      const label = locale.startsWith("en") ? "calleractivity" : "CallerActivity";
      const retry = vi.fn();
      const onError = vi.fn();
      let view = provider(locale, <Feed {...common} items={[]} loading />, onError);
      expect(screen.getByRole("status")).toHaveTextContent(t("loading"));
      view.unmount();
      view = provider(locale, <Feed {...common} items={[]} />, onError);
      expect(screen.getByText(t("empty", { label }))).toBeInTheDocument();
      view.unmount();
      view = provider(
        locale,
        <Feed {...common} items={[]} error="SERVER_DETAIL_UNCHANGED" onRetry={retry} />,
        onError
      );
      expect(screen.getByText(t("loadFailed", { label }))).toBeInTheDocument();
      expect(screen.getByText("SERVER_DETAIL_UNCHANGED")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: t("retry") }));
      expect(retry).toHaveBeenCalledOnce();
      view.unmount();
      view = provider(
        locale,
        <Feed
          {...common}
          items={items}
          hasMore
          loadingMore
          loadOlderLabel="CallerOlder"
          loadingOlderLabel="CallerLoading"
        />,
        onError
      );
      expect(screen.getByRole("button", { name: "CallerLoading" })).toBeDisabled();
      view.unmount();
      view = provider(
        locale,
        <Feed {...common} items={items} error="ORIGINAL_PAGE_ERROR" onRetry={retry} />,
        onError
      );
      expect(screen.getByRole("alert")).toHaveTextContent("ORIGINAL_PAGE_ERROR");
      fireEvent.click(screen.getByRole("button", { name: t("retry") }));
      expect(retry).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
      view.unmount();
    }
  );
  it("groups by the configured timezone rather than the browser timezone or UTC", () => {
    const rows = [
      { id: "before", at: "2026-03-09T03:59:00Z" },
      { id: "after", at: "2026-03-09T04:01:00Z" },
    ];
    const groups = groupItems(
      rows,
      { day: (row) => row.at },
      { timeZone: "America/New_York", now, today: "TODAY", yesterday: "YESTERDAY" }
    );
    expect(groups.map((group) => [group.label, group.items.map((row) => row.id)])).toEqual([
      ["YESTERDAY", ["before"]],
      ["TODAY", ["after"]],
    ]);
    const utc = groupItems(
      rows,
      { day: (row) => row.at },
      { timeZone: "UTC", now, today: "TODAY" }
    );
    expect(utc).toHaveLength(1);
  });
});
