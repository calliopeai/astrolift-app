import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/en.json";
import { fireEvent, render as rtlRender, screen } from "@testing-library/react";
import * as React from "react";
import { describe, expect, it } from "vitest";

import { RUNS, RUNS_LIST, type RunRow } from "./fixtures";
import { ListPage } from "./ListPage";
import { type ListState, useLocalListState } from "./use-list-state";

const EMPTY = { icon: null, title: "No runs yet" };
const COLUMNS = [{ id: "id", header: "Run", cell: (r: RunRow) => r.id }];

function List({ embedded, initial }: { embedded: boolean; initial?: Partial<ListState> }) {
  const list = useLocalListState(RUNS_LIST, initial);
  const body = {
    list,
    label: "Runs",
    columns: COLUMNS,
    rows: RUNS.slice(0, 3),
    getRowId: (r: RunRow) => r.id,
    empty: EMPTY,
  };
  return embedded ? (
    <ListPage<RunRow> embedded {...body} />
  ) : (
    <ListPage<RunRow> header={{ crumbs: [{ label: "Runs" }], title: "Runs" }} {...body} />
  );
}

describe("ListPage", () => {
  it("routed: the views are the header's tabs", () => {
    render(<List embedded={false} />);
    expect(screen.getByRole("heading", { level: 1, name: "Runs" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Views" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View" })).toBeNull();
  });

  it("embedded: no header, the views are a picker linking ?view=", async () => {
    render(<List embedded initial={{ view: "failed" }} />);
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Views" })).toBeNull();
    const picker = screen.getByRole("button", { name: "View" });
    expect(picker).toHaveTextContent("Failed");
    fireEvent.pointerDown(picker, { button: 0, ctrlKey: false });
    const items = await screen.findAllByRole("menuitem");
    expect(items.map((i) => i.getAttribute("href"))).toEqual(
      RUNS_LIST.views.map((v) => (v.key === "all" ? "?" : `?view=${v.key}`))
    );
    expect(items.find((i) => i.textContent === "Failed")).toHaveAttribute("aria-current", "page");
  });
});

function render(ui: React.ReactNode) {
  return rtlRender(ui, {
    wrapper: ({ children }) => (
      <NextIntlClientProvider locale="en" messages={messages}>
        {children}
      </NextIntlClientProvider>
    ),
  });
}
