import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import { locales } from "@/i18n/config";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { BRAINS } from "./brains.fixtures";
import { BRAINS_LIST } from "./brains-list";
import { BrainsScreen } from "./BrainsScreen";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };

function Screen({ rows = BRAINS }) {
  const list = useLocalListState(BRAINS_LIST);
  return (
    <BrainsScreen
      list={list}
      rows={rows}
      totalCount={rows.length}
      loading={false}
      stale={false}
      error={null}
      onRetry={() => {}}
    />
  );
}

describe("semantic layers (#2320)", () => {
  it.each(locales)("%s renders every key without falling back to English", (locale) => {
    const onError = vi.fn();
    const copy = catalogs[locale].brains;
    expect(Object.keys(copy).sort()).toEqual(Object.keys(en.brains).sort());
    render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={onError}
      >
        <Screen />
      </NextIntlClientProvider>
    );
    expect(screen.getAllByText(copy.title).length).toBeGreaterThan(0);
    expect(screen.getByText(copy.notServed)).toBeInTheDocument();
    expect(screen.getByText(copy.notShared)).toBeInTheDocument();
    if (locale !== "en") expect(copy.context).not.toBe(en.brains.context);
    expect(onError).not.toHaveBeenCalled();
  });

  it("links each layer's endpoints, shares and access, and says when there are none", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Screen />
      </NextIntlClientProvider>
    );
    const endpoint = screen.getByText("production: https://brain.acme.example");
    expect(endpoint.closest("a")).toHaveAttribute("href", "https://brain.acme.example");
    expect(screen.getByText("medops")).toHaveAttribute("title", "medops: viewer");
    expect(screen.getAllByText(en.brains.manageAccess)[0].closest("a")).toHaveAttribute(
      "href",
      "/apps/leo-brain/access"
    );
  });

  it("shows the empty state when nothing is readable", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Screen rows={[]} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(en.brains.emptyTitle)).toBeInTheDocument();
  });
});
