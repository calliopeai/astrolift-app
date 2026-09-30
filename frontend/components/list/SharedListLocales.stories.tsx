import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import * as React from "react";

import { DataTable, useRowSelection } from "@/components/data-table";
import { fakeController } from "@/components/data-table/fixtures";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";

import { ListPage } from "./ListPage";
import { ListSummary } from "./ListSummary";
import { NewRowsPill } from "./NewRowsPill";
import { type ListDefinition, useLocalListState } from "./use-list-state";

const meta: Meta = { title: "List/SharedListLocales", parameters: { layout: "padded" } };
export default meta;

// These localized caller-owned labels are fixtures. Production screen/list
// definitions provide their own labels; the shared controls do not replace them.
const copy = {
  de: {
    label: "Einträge",
    name: "Name",
    status: "Status",
    all: "Alle",
    mine: "Meine",
    search: "Einträge suchen…",
    title: "Noch keine Einträge",
    view: "Aktiv",
    active: "Aktiv",
  },
  ja: {
    label: "項目",
    name: "名前",
    status: "状態",
    all: "すべて",
    mine: "自分の項目",
    search: "項目を検索…",
    title: "まだ項目がありません",
    view: "有効",
    active: "有効",
  },
};
function Surface({
  locale,
  state = "ready",
  card = false,
  long = false,
}: {
  locale: "de" | "ja";
  state?: "ready" | "loading" | "error" | "filtered" | "view";
  card?: boolean;
  long?: boolean;
}) {
  const labels = copy[locale];
  const definition: ListDefinition = {
    id: `locale-story-${locale}-${state}-${card}`,
    fields: [
      { key: "status", label: labels.status, options: [{ value: "active", label: labels.active }] },
    ],
    searchPlaceholder: labels.search,
    defaultSort: [{ key: "name", dir: "desc" }],
    views: [
      { key: "all", label: labels.all, filters: {} },
      { key: "mine", label: labels.mine, filters: { owner: "me" } },
      { key: "active", label: labels.view, filters: { status: "active" } },
    ],
    paging: "cursor",
    pageSizes: [25, 50],
  };
  const list = useLocalListState(definition, {
    q: state === "filtered" ? "no-match" : "",
    view: state === "view" ? "active" : "all",
  });
  const { setMode } = list;
  React.useEffect(() => {
    setMode(card ? "card" : "list");
  }, [card, setMode]);
  const rows =
    state === "ready"
      ? [{ id: long ? "cluster/" + "very-long-workload-name-".repeat(20) : "api" }]
      : [];
  const error =
    state === "error"
      ? { message: "SOURCE_ERROR_DETAIL: " + "request-id/".repeat(long ? 30 : 1) }
      : undefined;
  return (
    <ListPage
      embedded
      list={list}
      label={labels.label}
      columns={[
        {
          id: "name",
          header: labels.name,
          sortKey: "name",
          cell: (row: { id: string }) => <span className="font-mono break-all">{row.id}</span>,
        },
        { id: "status", header: labels.status, cell: () => labels.active },
      ]}
      rows={rows}
      getRowId={(row) => row.id}
      loading={state === "loading"}
      error={error}
      onRetry={() => {}}
      empty={{ icon: null, title: labels.title, learnMoreHref: "/documentation" }}
      nextCursor={state === "ready" ? "next-cursor" : null}
      totalCount={state === "ready" ? 12345 : null}
      renderCard={(row) => <p className="min-w-0 rounded-md border p-4 break-all">{row.id}</p>}
    />
  );
}
function GermanTable() {
  const selection = useRowSelection();
  return (
    <>
      <DataTable
        label="Einträge"
        controller={fakeController({
          rows: [{ id: "api" }],
          totalCount: 12345,
          hasNext: true,
          search: "api",
        })}
        columns={[
          { id: "name", header: "Name", sortKey: "name", cell: (row: { id: string }) => row.id },
        ]}
        getRowId={(row) => row.id}
        selection={selection}
        bulkActions={() => <span>Aktion</span>}
        empty={{ icon: null, title: "Noch keine Einträge" }}
      />
      <NewRowsPill count={124} onReveal={() => {}} />
      <ListSummary
        title="Einträge"
        count={12345}
        rows={[{ id: "api" }]}
        keyOf={(row) => row.id}
        renderRow={(row) => row.id}
        viewAllHref="/example"
      />
    </>
  );
}
const frame = (locale: "de" | "ja", children: React.ReactNode) => (
  <NextIntlClientProvider locale={locale} messages={locale === "de" ? de : ja}>
    <div className="flex min-w-0 flex-col gap-4" style={{ width: 768 }}>
      {children}
    </div>
  </NextIntlClientProvider>
);
export const GermanWidth768: StoryObj = { render: () => frame("de", <Surface locale="de" />) };
export const GermanCardsLongStrings: StoryObj = {
  render: () => frame("de", <Surface locale="de" card long />),
};
export const GermanTableWidth768: StoryObj = { render: () => frame("de", <GermanTable />) };
export const JapaneseFilteredWidth768: StoryObj = {
  render: () => frame("ja", <Surface locale="ja" state="filtered" />),
};
export const JapaneseEmptyViewWidth768: StoryObj = {
  render: () => frame("ja", <Surface locale="ja" state="view" />),
};
export const JapaneseLoadingWidth768: StoryObj = {
  render: () => frame("ja", <Surface locale="ja" state="loading" />),
};
export const GermanErrorLongStrings: StoryObj = {
  render: () => frame("de", <Surface locale="de" state="error" long />),
};
export const JapaneseCardErrorLongStrings: StoryObj = {
  render: () => frame("ja", <Surface locale="ja" state="error" card long />),
};
