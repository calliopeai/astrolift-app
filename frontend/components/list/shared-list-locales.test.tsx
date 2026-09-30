import { readFileSync } from "node:fs";
import path from "node:path";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DataTable, useRowSelection } from "@/components/data-table";
import { fakeController } from "@/components/data-table/fixtures";
import { EmptyState } from "@/components/EmptyState";
import { locales } from "@/i18n/config";

import { FilterBar } from "./FilterBar";
import type { ListDefinition } from "./list-state";
import { ListPage } from "./ListPage";
import { ListSummary } from "./ListSummary";
import { NewRowsPill } from "./NewRowsPill";
import { useLocalListState } from "./use-list-state";

const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
// Plural categories differ by language. Require the same argument types and
// rich tags, including arguments inside every selectable branch.
function argumentsOf(elements: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      elements.flatMap((node): string[] => {
        if (node.type === 0 || node.type === 7) return [];
        if (node.type === 8) return [`tag:${node.value}`, ...argumentsOf(node.children)];
        if (node.type === 5 || node.type === 6)
          return [
            `${node.type}:${node.value}`,
            ...Object.values(node.options).flatMap((option) => argumentsOf(option.value)),
          ];
        return [`${node.type}:${node.value}`];
      })
    ),
  ].sort();
}
const definition: ListDefinition = {
  id: "translated-regression",
  fields: [
    {
      key: "status",
      label: "CallerStatus",
      options: [{ value: "running", label: "CallerRunning" }],
    },
    { key: "name", label: "CallerName" },
  ],
  searchPlaceholder: "CallerSearch",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: [
    { key: "all", label: "CallerAll", filters: {} },
    { key: "mine", label: "CallerMine", filters: { owner: "me" } },
  ],
  paging: "cursor",
  pageSizes: [25, 50],
};
const columns = [
  { id: "name", header: "CallerName", sortKey: "name", cell: (row: { id: string }) => row.id },
  { id: "status", header: "CallerStatus", cell: () => "CallerRunning" },
];
function Controls() {
  const list = useLocalListState(definition);
  const { setMode } = list;
  React.useLayoutEffect(() => {
    setMode("card");
  }, [setMode]);
  return (
    <>
      <FilterBar
        list={list}
        columns={[
          { id: "name", label: "CallerName", sortKey: "name", hideable: false },
          { id: "status", label: "CallerStatus" },
        ]}
      />
      <output data-testid="state">
        {JSON.stringify({
          filters: list.state.filters,
          q: list.state.q,
          sort: list.state.sort,
          mode: list.mode,
          hidden: list.hiddenColumns,
        })}
      </output>
    </>
  );
}
function SelectedTable({
  controller,
}: {
  controller: ReturnType<typeof fakeController<{ id: string }>>;
}) {
  const selection = useRowSelection();
  return (
    <DataTable
      label="CallerRows"
      controller={controller}
      columns={columns}
      getRowId={(row) => row.id}
      empty={{ icon: null, title: "CallerEmpty" }}
      selection={selection}
      bulkActions={() => <span>CallerBulk</span>}
    />
  );
}
function Page({
  error,
  card = false,
  filtered = false,
}: {
  error?: { message: string };
  card?: boolean;
  filtered?: boolean;
}) {
  const list = useLocalListState(definition, {
    q: filtered ? "search" : "",
    view: filtered ? "all" : "mine",
  });
  const { setMode } = list;
  React.useLayoutEffect(() => {
    setMode(card ? "card" : "list");
  }, [card, setMode]);
  return (
    <ListPage
      embedded
      list={list}
      label="CallerRows"
      columns={columns}
      rows={[]}
      getRowId={(row) => row.id}
      empty={{ icon: null, title: "CallerEmpty" }}
      error={error}
      renderCard={card ? (row) => row.id : undefined}
    />
  );
}
function mount(locale: string, children: React.ReactNode, onError = vi.fn()) {
  return render(
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
      {children}
    </NextIntlClientProvider>
  );
}

beforeEach(() => localStorage.clear());

describe("shared list/table translations", () => {
  it.each(locales)(
    "%s supplies complete ICU contracts without modifying other shared domains",
    (locale) => {
      for (const domain of ["list", "table"]) {
        const messages = catalogs[locale].shared[domain];
        expect(Object.keys(messages).sort()).toEqual(
          Object.keys(catalogs.en.shared[domain]).sort()
        );
        for (const key of Object.keys(messages))
          expect(argumentsOf(parse(messages[key])), `${domain}.${key}`).toEqual(
            argumentsOf(parse(catalogs.en.shared[domain][key]))
          );
      }
    }
  );
  it.each(locales)(
    "%s keeps filter/sort/view/column values and caller labels intact",
    async (locale) => {
      const t = createTranslator({ locale, messages: catalogs[locale], namespace: "shared.list" });
      const onError = vi.fn();
      mount(locale, <Controls />, onError);
      fireEvent.click(screen.getByRole("button", { name: t("filter") }));
      fireEvent.click(screen.getByRole("option", { name: /CallerStatus/ }));
      expect(
        screen.getByRole("textbox", { name: t("fieldValue", { field: "CallerStatus" }) })
      ).toHaveAttribute("placeholder", t("findValue", { field: "CallerStatus" }));
      fireEvent.click(screen.getByRole("option", { name: "CallerRunning" }));
      expect(JSON.parse(screen.getByTestId("state").textContent!).filters).toEqual({
        status: "running",
      });
      fireEvent.click(
        screen.getByRole("button", {
          name: t("removeFilter", { field: "CallerStatus", value: "CallerRunning" }),
        })
      );
      expect(JSON.parse(screen.getByTestId("state").textContent!).filters).toEqual({});
      fireEvent.click(screen.getByRole("button", { name: t("filter") }));
      fireEvent.click(screen.getByRole("option", { name: /CallerName/ }));
      fireEvent.change(
        screen.getByRole("textbox", { name: t("fieldValue", { field: "CallerName" }) }),
        { target: { value: "  literal <tag> value  " } }
      );
      fireEvent.click(screen.getByRole("option"));
      expect(JSON.parse(screen.getByTestId("state").textContent!).filters).toEqual({
        name: "literal <tag> value",
      });
      fireEvent.click(screen.getByRole("button", { name: t("clear") }));
      fireEvent.pointerDown(screen.getByRole("button", { name: new RegExp(t("sortPrefix")) }), {
        button: 0,
        ctrlKey: false,
      });
      fireEvent.click(await screen.findByRole("menuitemradio", { name: "CallerName" }));
      expect(JSON.parse(screen.getByTestId("state").textContent!).sort).toEqual([
        { key: "name", dir: "desc" },
      ]);
      fireEvent.pointerDown(screen.getByRole("button", { name: new RegExp(t("sortPrefix")) }), {
        button: 0,
        ctrlKey: false,
      });
      fireEvent.click(await screen.findByRole("menuitemradio", { name: t("ascending") }));
      expect(JSON.parse(screen.getByTestId("state").textContent!).sort).toEqual([
        { key: "name", dir: "asc" },
      ]);
      fireEvent.pointerDown(screen.getByRole("button", { name: t("chooseColumns") }), {
        button: 0,
        ctrlKey: false,
      });
      fireEvent.click(await screen.findByRole("menuitemcheckbox", { name: "CallerStatus" }));
      expect(JSON.parse(screen.getByTestId("state").textContent!).hidden).toEqual(["status"]);
      fireEvent.keyDown(document, { key: "Escape" });
      fireEvent.click(screen.getByRole("button", { name: t("listView") }));
      expect(JSON.parse(screen.getByTestId("state").textContent!).mode).toBe("list");
      fireEvent.change(screen.getByRole("searchbox", { name: "CallerSearch" }), {
        target: { value: "actual search" },
      });
      fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Enter" });
      await waitFor(() =>
        expect(JSON.parse(screen.getByTestId("state").textContent!).q).toBe("actual search")
      );
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it.each(locales)(
    "%s localizes table counts, selection and paging without changing callbacks or ARIA protocol",
    (locale) => {
      const t = createTranslator({ locale, messages: catalogs[locale], namespace: "shared.table" });
      const p = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "shared.pagination",
      });
      const controller = fakeController({
        rows: [{ id: "guid-1" }],
        totalCount: 12345,
        sort: { key: "name", dir: "desc" },
        next: vi.fn(),
        prev: vi.fn(),
        toggleSort: vi.fn(),
        setSearch: vi.fn(),
        hasNext: true,
        hasPrev: true,
      });
      const onError = vi.fn();
      mount(locale, <SelectedTable controller={controller} />, onError);
      expect(
        screen.getByText(
          (text) => text.replace(/\s/g, " ") === t("results", { count: 12345 }).replace(/\s/g, " ")
        )
      ).toBeInTheDocument();
      expect(screen.getByRole("columnheader", { name: "CallerName" })).toHaveAttribute(
        "aria-sort",
        "descending"
      );
      fireEvent.click(screen.getByRole("button", { name: "CallerName" }));
      expect(controller.toggleSort).toHaveBeenCalledWith("name");
      fireEvent.click(
        screen.getByRole("checkbox", {
          name: t("selectPage", { label: locale.startsWith("en") ? "callerrows" : "CallerRows" }),
        })
      );
      expect(screen.getByText(t("selected", { count: 1 }))).toBeInTheDocument();
      expect(
        screen.getByRole("checkbox", { name: t("selectRow", { id: "guid-1" }) })
      ).toBeChecked();
      fireEvent.click(screen.getByRole("button", { name: t("clear") }));
      expect(
        screen.getByRole("checkbox", { name: t("selectRow", { id: "guid-1" }) })
      ).not.toBeChecked();
      fireEvent.click(screen.getByRole("button", { name: p("nextPage") }));
      fireEvent.click(screen.getByRole("button", { name: p("previousPage") }));
      expect(controller.next).toHaveBeenCalledOnce();
      expect(controller.prev).toHaveBeenCalledOnce();
      fireEvent.change(screen.getByRole("textbox", { name: t("search") }), {
        target: { value: "unchanged payload" },
      });
      expect(controller.setSearch).toHaveBeenCalledWith("unchanged payload");
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it.each(locales)(
    "%s distinguishes filtered/view emptiness and preserves original server errors in both renderers",
    (locale) => {
      const t = createTranslator({ locale, messages: catalogs[locale], namespace: "shared.list" });
      const noun = locale.startsWith("en") ? "callerrows" : "CallerRows";
      const a = mount(locale, <Page />);
      expect(
        screen.getByText(t("emptyView", { label: noun, view: "CallerMine" }))
      ).toBeInTheDocument();
      expect(screen.queryByText("CallerEmpty")).not.toBeInTheDocument();
      a.unmount();
      for (const card of [false, true]) {
        const b = mount(locale, <Page card={card} filtered />);
        expect(screen.getByText(t("emptyFiltered", { label: noun }))).toBeInTheDocument();
        b.unmount();
        const c = mount(
          locale,
          <Page card={card} error={{ message: "SERVER_DETAIL_UNCHANGED" }} />
        );
        expect(screen.getByText("SERVER_DETAIL_UNCHANGED")).toBeInTheDocument();
        expect(screen.getByText(t("loadFailed", { label: noun }))).toBeInTheDocument();
        c.unmount();
      }
    }
  );
  it.each(locales)(
    "%s formats summary/live counts, preserves unknown totals and keeps help overrides",
    (locale) => {
      const t = createTranslator({ locale, messages: catalogs[locale], namespace: "shared.list" });
      const reveal = vi.fn();
      const onError = vi.fn();
      const a = mount(
        locale,
        <>
          <NewRowsPill count={1} onReveal={reveal} />
          <ListSummary
            title="CallerSummary"
            count={12345}
            rows={[{ id: "one" }]}
            keyOf={(row) => row.id}
            renderRow={(row) => row.id}
            viewAllHref="/caller?status=running"
          />
          <EmptyState icon={null} title="CallerEmpty" learnMoreHref="/docs/caller" />
        </>,
        onError
      );
      fireEvent.click(screen.getByRole("button"));
      expect(reveal).toHaveBeenCalledOnce();
      expect(screen.getByRole("link", { name: t("learnMore") })).toHaveAttribute(
        "href",
        "/docs/caller"
      );
      expect(
        screen.getByRole("link", { name: new RegExp(new Intl.NumberFormat(locale).format(12345)) })
      ).toHaveAttribute("href", "/caller?status=running");
      expect(onError).not.toHaveBeenCalled();
      a.unmount();
      const b = mount(
        locale,
        <>
          <NewRowsPill count={124} onReveal={reveal} />
          <ListSummary
            title="CallerSummary"
            rows={[{ id: "one" }]}
            keyOf={(row) => row.id}
            renderRow={(row) => row.id}
            viewAllHref="/caller"
          />
          <EmptyState
            icon={null}
            title="CallerEmpty"
            learnMoreHref="/docs/caller"
            learnMoreLabel="CallerHelpOverride"
          />
        </>
      );
      expect(screen.getByRole("button")).toHaveTextContent("99+");
      expect(screen.getByRole("link", { name: t("viewAll") })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "CallerHelpOverride" })).toBeInTheDocument();
      b.unmount();
    }
  );
});
