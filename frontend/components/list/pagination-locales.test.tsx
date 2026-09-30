import { readFileSync } from "node:fs";
import path from "node:path";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";

import { ListPagination } from "./ListPagination";

const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
function shape(elements: MessageFormatElement[]): string[] {
  return elements
    .flatMap((node): string[] => {
      if (node.type === 0 || node.type === 7) return [];
      if (node.type === 8) return [`tag:${node.value}`, ...shape(node.children)];
      if (node.type === 5 || node.type === 6)
        return [
          `select:${node.value}`,
          ...Object.values(node.options).flatMap((option) => shape(option.value)),
        ];
      return [`${node.type}:${node.value}`];
    })
    .sort();
}
const sizes = { pageSize: 25, pageSizes: [25, 50, 100], onPageSize: vi.fn() };

describe("shared pagination locales", () => {
  it.each(locales)("%s formats counts and retains numbered navigation", (locale) => {
    const messages = catalogs[locale];
    const onError = vi.fn();
    const onPage = vi.fn();
    const t = createTranslator({ locale, messages, namespace: "shared.pagination" });
    expect(Object.keys(messages.shared.pagination).sort()).toEqual(
      Object.keys(catalogs.en.shared.pagination).sort()
    );
    for (const key of Object.keys(catalogs.en.shared.pagination)) {
      expect(shape(parse(messages.shared.pagination[key])), key).toEqual(
        shape(parse(catalogs.en.shared.pagination[key]))
      );
    }
    const { unmount } = render(
      <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
        <ListPagination {...sizes} mode="numbered" page={2} totalCount={12345} onPage={onPage} />
      </NextIntlClientProvider>
    );
    const nav = screen.getByRole("navigation", { name: t("pages") });
    expect(nav.textContent).toContain(new Intl.NumberFormat(locale).format(12345));
    expect(
      screen.getByRole("button", { name: t("page", { page: 2 }) }).getAttribute("aria-current")
    ).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: t("nextPage") }));
    expect(onPage).toHaveBeenCalledWith(3);
    fireEvent.click(screen.getByRole("button", { name: t("previousPage") }));
    expect(onPage).toHaveBeenLastCalledWith(1);
    expect(screen.getByRole("combobox").getAttribute("aria-label")).toBe(t("rowsPerPage"));
    expect(onError).not.toHaveBeenCalled();
    unmount();
  });

  it.each(locales)("%s keeps cursor totals approximate and controls bounded", (locale) => {
    const messages = catalogs[locale];
    const t = createTranslator({ locale, messages, namespace: "shared.pagination" });
    const onOlder = vi.fn();
    const onNewer = vi.fn();
    const onError = vi.fn();
    const { unmount } = render(
      <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
        <ListPagination
          {...sizes}
          mode="cursor"
          shown={25}
          totalCount={1240}
          approximate
          hasNewer={false}
          hasOlder
          onNewer={onNewer}
          onOlder={onOlder}
        />
      </NextIntlClientProvider>
    );
    const compact = new Intl.NumberFormat(locale, {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(1240);
    expect(screen.getByRole("navigation").textContent).toContain(
      t("approximately", { count: compact })
    );
    expect(screen.getByRole("button", { name: t("newer") })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: t("older") }));
    expect(onOlder).toHaveBeenCalledTimes(1);
    expect(onNewer).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    unmount();
  });

  it("renders an empty numbered range and a cursor with unknown total", () => {
    const { rerender } = render(
      <NextIntlClientProvider locale="fr" messages={catalogs.fr}>
        <ListPagination {...sizes} mode="numbered" page={5} totalCount={0} onPage={vi.fn()} />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("navigation").textContent).toBe("0 sur 0");
    rerender(
      <NextIntlClientProvider locale="ja" messages={catalogs.ja}>
        <ListPagination
          {...sizes}
          mode="cursor"
          shown={25}
          totalCount={null}
          hasNewer={false}
          hasOlder={false}
          onNewer={vi.fn()}
          onOlder={vi.fn()}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("navigation").textContent).toContain("25件表示");
    expect(screen.getByRole("navigation").textContent).not.toContain("0件中");
  });
});
