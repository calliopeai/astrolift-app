import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DataTable } from "./data-table";
import type { Column, PageState } from "./types";
import type { CursorTableController } from "./use-cursor-table";

/**
 * Forty surfaces are about to render through this component, so the states
 * it is supposed to make unskippable are pinned here: an empty list shows
 * the empty state rather than nothing, a filtered-empty list says something
 * different from a genuinely-empty one, loading keeps the table's geometry,
 * and an error offers a retry instead of looking like "no rows".
 */

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

type Row = { id: string; name: string; status: string };

const columns: Column<Row>[] = [
  { id: "name", header: "Name", cell: (r) => r.name },
  { id: "status", header: "Status", cell: (r) => r.status, sortKey: "status" },
];

const rows: Row[] = [
  { id: "1", name: "api", status: "running" },
  { id: "2", name: "worker", status: "failed" },
];

function controller(overrides: Partial<CursorTableController<Row>> = {}) {
  return {
    rows: [],
    state: "ready" as PageState,
    error: undefined,
    retry: vi.fn(),
    refetch: vi.fn(),
    totalCount: null,
    pageIndex: 0,
    hasNext: false,
    hasPrev: false,
    next: vi.fn(),
    prev: vi.fn(),
    pageSize: 25,
    setPageSize: vi.fn(),
    search: "",
    setSearch: vi.fn(),
    isStale: false,
    isSearching: false,
    searchEnabled: false,
    sort: undefined,
    toggleSort: vi.fn(),
    sortEnabled: false,
    isFiltered: false,
    clearFilters: vi.fn(),
    ...overrides,
  } satisfies CursorTableController<Row>;
}

const empty = { icon: <span data-testid="empty-icon" />, title: "No apps yet" };

function renderTable(ctrl: CursorTableController<Row>, props: Record<string, unknown> = {}) {
  return render(
    <DataTable
      label="Apps"
      controller={ctrl}
      columns={columns}
      getRowId={(r) => r.id}
      empty={empty}
      {...props}
    />
  );
}

describe("DataTable", () => {
  it("renders a row per item", () => {
    renderTable(controller({ rows, state: "ready" }));
    expect(screen.getByText("api")).toBeInTheDocument();
    expect(screen.getByText("worker")).toBeInTheDocument();
  });

  it("shows the empty state instead of nothing when the list is empty", () => {
    // Four surfaces rendered literally nothing here.
    renderTable(controller({ rows: [], state: "empty" }));
    expect(screen.getByText("No apps yet")).toBeInTheDocument();
  });

  it("distinguishes a filtered-empty list from an empty one", () => {
    renderTable(controller({ rows: [], state: "emptyFiltered", isFiltered: true }), {
      emptyFiltered: { title: "No matching apps" },
    });
    expect(screen.getByText("No matching apps")).toBeInTheDocument();
    expect(screen.queryByText("No apps yet")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /clear search/i })).toBeInTheDocument();
  });

  it("falls back to derived copy when emptyFiltered is omitted", () => {
    renderTable(controller({ rows: [], state: "emptyFiltered", isFiltered: true }));
    expect(screen.getByText("No matches")).toBeInTheDocument();
    expect(screen.getByText(/no apps match the current filters/i)).toBeInTheDocument();
  });

  it("clears the search from the filtered-empty state", async () => {
    const clearFilters = vi.fn();
    renderTable(controller({ rows: [], state: "emptyFiltered", isFiltered: true, clearFilters }));
    screen.getByRole("button", { name: /clear search/i }).click();
    expect(clearFilters).toHaveBeenCalled();
  });

  it("keeps the table's shape while loading", () => {
    // Skeletons go inside the real table so rows arriving do not reflow
    // the page — a floating skeleton block shifts everything below it.
    const { container } = renderTable(controller({ rows: [], state: "loading" }));
    expect(container.querySelector("table")).toBeInTheDocument();
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  });

  it("offers a retry on error rather than looking empty", () => {
    const retry = vi.fn();
    renderTable(
      controller({ rows: [], state: "error", error: { message: "nope" } as never, retry })
    );

    expect(screen.getByText(/could not load apps/i)).toBeInTheDocument();
    expect(screen.getByText("nope")).toBeInTheDocument();
    screen.getByRole("button", { name: /retry/i }).click();
    expect(retry).toHaveBeenCalled();
  });

  it("fades stale rows while a refetch is in flight", () => {
    // Rows persist across a refetch rather than blanking, so something has
    // to say they are answering the previous question.
    const { container } = renderTable(controller({ rows, state: "ready", isStale: true }));
    const shell = container.querySelector('[aria-busy="true"]');
    expect(shell).toBeInTheDocument();
    expect(shell).toHaveClass("opacity-60");
  });

  it("does not fade settled rows", () => {
    const { container } = renderTable(controller({ rows, state: "ready" }));
    expect(container.querySelector('[aria-busy="true"]')).not.toBeInTheDocument();
  });

  it("names the table for screen readers", () => {
    renderTable(controller({ rows, state: "ready" }));
    expect(screen.getByRole("table", { name: "Apps" })).toBeInTheDocument();
  });

  describe("row activation", () => {
    it("renders real links when rowHref is given", () => {
      // Real anchors so middle-click, open-in-new-tab and copy-link work;
      // an onClick handler on a <tr> supports none of those.
      renderTable(controller({ rows, state: "ready" }), {
        rowHref: (r: Row) => `/apps/${r.id}`,
      });
      expect(screen.getByRole("link", { name: "api" })).toHaveAttribute("href", "/apps/1");
    });

    it("stretches each activator over its own row and no further", () => {
      // Two halves of the same guarantee, and jsdom can hold neither as
      // geometry: the activator is stretched with `after:inset-0`, and the
      // row is the positioned ancestor that stretch resolves against.
      // Drop the first and the hit area shrinks to the first cell's text;
      // drop the second and it grows to the whole table, where the last
      // row swallows every click (#1502).
      //
      // Verified as the fix in a browser: `position: relative` on the row
      // gives each row its own extent, full width; on the cell it confines
      // the activator to the first cell and deadens the rest of the row.
      const cases = [
        { props: { rowHref: (r: Row) => `/apps/${r.id}` }, role: "link" as const },
        {
          props: { onRowActivate: vi.fn(), rowLabel: (r: Row) => r.name },
          role: "button" as const,
        },
      ];
      for (const { props, role } of cases) {
        const { unmount } = renderTable(controller({ rows, state: "ready" }), props);
        for (const row of screen.getAllByRole("row").slice(1)) {
          expect(row).toHaveClass("relative");
        }
        for (const activator of screen.getAllByRole(role)) {
          expect(activator).toHaveClass("after:absolute", "after:inset-0");
        }
        unmount();
      }
    });

    it("does not render links when onRowActivate is given", () => {
      const onRowActivate = vi.fn();
      renderTable(controller({ rows, state: "ready" }), { onRowActivate });
      expect(screen.queryByRole("link")).not.toBeInTheDocument();

      screen.getByText("api").click();
      expect(onRowActivate).toHaveBeenCalledWith(rows[0]);
    });

    it("activates an onRowActivate row through a named button", () => {
      // The activator used to be an `onClick` on the <tr>: no keyboard
      // could reach it and no screen reader announced it (#1503). What
      // fixes that is the control being a real button with a real name,
      // so that is what this holds. Tab order and Enter are the browser's
      // to provide once it is one, and simulating them in jsdom would be
      // testing jsdom.
      const onRowActivate = vi.fn();
      renderTable(controller({ rows, state: "ready" }), {
        onRowActivate,
        rowLabel: (r: Row) => `${r.name} ${r.status}`,
      });

      const button = screen.getByRole("button", { name: "api running" });
      button.click();
      expect(onRowActivate).toHaveBeenCalledWith(rows[0]);
    });

    it("keeps the row's own text as the name when rowLabel is absent", () => {
      // Types require `rowLabel` beside `onRowActivate`; an untyped caller
      // can still omit it, and the row must stay reachable rather than
      // falling back to the unreachable <tr> handler.
      renderTable(controller({ rows, state: "ready" }), { onRowActivate: vi.fn() });
      expect(screen.getByRole("button", { name: "api" })).toBeInTheDocument();
    });
  });

  describe("selection", () => {
    const selection = (over: Record<string, unknown> = {}) => ({
      selectedIds: [],
      selectedCount: 0,
      isSelected: () => false,
      toggle: vi.fn(),
      togglePage: vi.fn(),
      pageSelectionState: () => false as const,
      clear: vi.fn(),
      ...over,
    });

    it("adds a checkbox column with a header select-all", () => {
      renderTable(controller({ rows, state: "ready" }), { selection: selection() });
      expect(
        screen.getByRole("checkbox", { name: /select all apps on this page/i })
      ).toBeInTheDocument();
      expect(screen.getAllByRole("checkbox")).toHaveLength(3); // header + 2 rows
    });

    it("shows the bulk bar with the selected count only when something is selected", () => {
      const { rerender } = renderTable(controller({ rows, state: "ready" }), {
        selection: selection(),
        bulkActions: () => <button>Delete</button>,
      });
      expect(screen.queryByText(/selected/)).not.toBeInTheDocument();

      rerender(
        <DataTable
          label="Apps"
          controller={controller({ rows, state: "ready" })}
          columns={columns}
          getRowId={(r) => r.id}
          empty={empty}
          selection={selection({ selectedCount: 2, selectedIds: ["1", "2"] })}
          bulkActions={() => <button>Delete</button>}
        />
      );
      expect(screen.getByText("2 selected")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
    });

    it("marks selected rows for the styling hook", () => {
      const { container } = renderTable(controller({ rows, state: "ready" }), {
        selection: selection({ isSelected: (id: string) => id === "1" }),
      });
      expect(container.querySelectorAll('tr[data-state="selected"]')).toHaveLength(1);
    });

    it("disables select-all when the page has no rows", () => {
      renderTable(controller({ rows: [], state: "empty" }), { selection: selection() });
      expect(screen.getByRole("checkbox", { name: /select all/i })).toBeDisabled();
    });
  });

  describe("sorting", () => {
    it("renders a plain header when the query cannot sort", () => {
      renderTable(controller({ rows, state: "ready", sortEnabled: false }));
      expect(screen.queryByRole("button", { name: /status/i })).not.toBeInTheDocument();
    });

    it("renders sort controls only for columns that declare a sortKey", () => {
      renderTable(controller({ rows, state: "ready", sortEnabled: true }));
      expect(screen.getByRole("button", { name: /status/i })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^name$/i })).not.toBeInTheDocument();
    });

    it("puts aria-sort on the header cell, not the button", () => {
      const { container } = renderTable(
        controller({
          rows,
          state: "ready",
          sortEnabled: true,
          sort: { key: "status", dir: "desc" },
        })
      );
      const header = container.querySelector('th[aria-sort="descending"]');
      expect(header).toBeInTheDocument();
      expect(within(header as HTMLElement).getByRole("button")).toBeInTheDocument();
    });

    it("toggles sort on click", () => {
      const toggleSort = vi.fn();
      renderTable(controller({ rows, state: "ready", sortEnabled: true, toggleSort }));
      screen.getByRole("button", { name: /status/i }).click();
      expect(toggleSort).toHaveBeenCalledWith("status");
    });
  });

  describe("chrome", () => {
    it("hides the search box when the query has no search argument", () => {
      renderTable(controller({ rows, state: "ready", searchEnabled: false }));
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    });

    it("shows the search box when the query supports it", () => {
      renderTable(controller({ rows, state: "ready", searchEnabled: true }), {
        searchPlaceholder: "Search apps…",
      });
      expect(screen.getByRole("textbox", { name: "Search apps…" })).toBeInTheDocument();
    });

    it("shows the total count when the server reports one", () => {
      renderTable(controller({ rows, state: "ready", totalCount: 137 }));
      expect(screen.getByText("137 results")).toBeInTheDocument();
    });

    it("singularises one result", () => {
      renderTable(controller({ rows: [rows[0]], state: "ready", totalCount: 1 }));
      expect(screen.getByText("1 result")).toBeInTheDocument();
    });

    it("hides pagination entirely when there is only one page", () => {
      renderTable(controller({ rows, state: "ready", hasNext: false, hasPrev: false }));
      expect(screen.queryByRole("button", { name: /next page/i })).not.toBeInTheDocument();
    });

    it("pages forward and back", () => {
      const next = vi.fn();
      const prev = vi.fn();
      renderTable(
        controller({ rows, state: "ready", hasNext: true, hasPrev: true, pageIndex: 2, next, prev })
      );

      expect(screen.getByText("Page 3")).toBeInTheDocument();
      screen.getByRole("button", { name: /next page/i }).click();
      screen.getByRole("button", { name: /previous page/i }).click();
      expect(next).toHaveBeenCalled();
      expect(prev).toHaveBeenCalled();
    });

    it("disables the arrow at each end of the walk", () => {
      renderTable(controller({ rows, state: "ready", hasNext: true, hasPrev: false }));
      expect(screen.getByRole("button", { name: /previous page/i })).toBeDisabled();
      expect(screen.getByRole("button", { name: /next page/i })).not.toBeDisabled();
    });
  });
});
