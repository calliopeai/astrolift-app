import type { ReactNode } from "react";

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { ClustersClient } from "./clusters-client";

/**
 * /clusters on the shared list (spec 44 §5.1). The card grid and the table
 * are two renderings of the SAME page (#1233), and the server answers the
 * list state (#2150): search, filters, views, sort and the numbered page go
 * out as `astroliftClustersPage` variables and the rows and `totalCount`
 * come back. Nothing is filtered or sliced in the browser. These tests pin
 * the variables, that both modes read one page, and that the list's states
 * (loading, empty, filtered-empty) show in both.
 */

// Mutable state the hoisted mocks read at call time — `vi.hoisted` runs
// before the `vi.mock` factories, so they can close over it safely.
const state = vi.hoisted(() => ({
  page: null as null | {
    items: Record<string, unknown>[];
    totalCount: number | null;
  },
  loading: false,
  variables: [] as Record<string, unknown>[],
  qs: "",
  listeners: new Set<() => void>(),
}));

// Feed the real hook a mocked transport: the code under test is the list
// state as variables and both views, not Apollo.
vi.mock("@apollo/client/react", () => ({
  useQuery: (_q: unknown, opts?: { variables?: Record<string, unknown> }) => {
    if (opts?.variables) state.variables.push(opts.variables);
    return {
      data: state.page ? { astroliftClustersPage: state.page } : undefined,
      previousData: undefined,
      loading: state.loading,
      error: undefined,
      refetch: vi.fn().mockResolvedValue({}),
    };
  },
  useMutation: () => [vi.fn().mockResolvedValue({ data: {} }), { loading: false }],
}));

// The list state lives in the URL; a tiny store stands in for the router.
vi.mock("next/navigation", async () => {
  const React = await import("react");
  const subscribe = (fn: () => void) => {
    state.listeners.add(fn);
    return () => state.listeners.delete(fn);
  };
  return {
    usePathname: () => "/clusters",
    useSearchParams: () => {
      const qs = React.useSyncExternalStore(subscribe, () => state.qs);
      return new URLSearchParams(qs);
    },
    useRouter: () => ({
      replace: (href: string) => {
        state.qs = href.split("?")[1] ?? "";
        state.listeners.forEach((fn) => fn());
      },
      push: () => {},
    }),
  };
});

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    can: () => true,
    granted: new Set(["cluster.manage"]),
    loading: false,
  }),
}));

vi.mock("@/lib/i18n/formatters", () => ({
  useFormatters: () => ({
    locale: "en",
    formatDate: () => "now",
    formatDateTime: () => "now",
    formatNumber: (n: number) => String(n),
    formatPercent: (n: number) => String(n),
    formatCurrency: (n: number) => String(n),
    formatRelativeTime: () => "just now",
  }),
}));

function cluster(slug: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: `id-${slug}`,
    slug,
    name: slug.toUpperCase(),
    isActive: true,
    lifecycle: "managed",
    lastManagementError: "",
    providerPluginSlug: "eks",
    region: "us-west-2",
    ingressClass: "nginx",
    heartbeatStatus: "connected",
    heartbeatAgeSeconds: 12,
    capabilitiesProbedAt: "2026-07-30T00:00:00Z",
    ...over,
  };
}

// The status and heartbeat badges are tooltip triggers; the provider lives
// in the root layout, so the test supplies it.
const renderClusters = () =>
  render(
    <TooltipProvider>
      <ClustersClient />
    </TooltipProvider>
  );

/** What a cold load asks for; `page.tsx` preloads exactly this. */
const COLD = { search: null, filter: null, sort: "name", page: 1, pageSize: 25 };

const showCards = () => fireEvent.click(screen.getByRole("button", { name: "Card view" }));

describe("ClustersClient", () => {
  beforeEach(() => {
    // The list|cards choice persists per person, so a click in one test
    // would pick the starting view for the next one.
    localStorage.clear();
    state.loading = false;
    state.variables = [];
    state.qs = "";
    state.page = { items: [cluster("prod"), cluster("staging")], totalCount: 2 };
  });

  it("renders the fleet in the table, each row linking to its cluster", () => {
    renderClusters();
    const table = screen.getByRole("table", { name: "Clusters" });
    expect(within(table).getByRole("link", { name: /PROD/ })).toHaveAttribute(
      "href",
      "/clusters/prod"
    );
    expect(within(table).getByText("STAGING")).toBeInTheDocument();
  });

  it("says who registered each cluster", () => {
    state.page = {
      items: [cluster("prod", { createdByUsername: "dana" }), cluster("staging")],
      totalCount: 2,
    };
    renderClusters();
    const table = screen.getByRole("table", { name: "Clusters" });
    expect(within(table).getByRole("columnheader", { name: "Registered by" })).toBeInTheDocument();
    expect(within(table).getByText("dana")).toBeInTheDocument();
  });

  it("shows the same rows as cards without a second fetch", () => {
    renderClusters();
    const fetches = state.variables.length;
    showCards();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("PROD")).toBeInTheDocument();
    expect(screen.getByText("STAGING")).toBeInTheDocument();
    // Re-renders re-read the same query; none asks for a different page.
    expect(new Set(state.variables.slice(fetches).map((v) => JSON.stringify(v)))).toEqual(
      new Set([JSON.stringify(COLD)])
    );
  });

  it("sends the search to the server in both modes", async () => {
    renderClusters();
    expect(screen.getByRole("searchbox", { name: "Search clusters..." })).toBeInTheDocument();
    showCards();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search clusters..." }), {
      target: { value: "prod" },
    });
    // The bar debounces; wait for the term to reach the query.
    await waitFor(() => expect(state.variables.at(-1)).toEqual({ ...COLD, search: "prod" }));
  });

  it("numbers pages from the server's totalCount and asks for the page", () => {
    state.page = {
      items: Array.from({ length: 25 }, (_, i) => cluster(`c-${String(i).padStart(2, "0")}`)),
      totalCount: 30,
    };
    renderClusters();
    expect(screen.getByRole("button", { name: "Page 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /next page/i })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: /previous page/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Page 2" }));
    expect(state.qs).toBe("page=2");
    expect(state.variables.at(-1)).toEqual({ ...COLD, page: 2 });
  });

  it("sends the Offline view, Mine and the chips as the server filter", () => {
    state.qs = "view=offline&provider=eks";
    renderClusters();
    expect(state.variables.at(-1)).toEqual({
      ...COLD,
      filter: { provider: ["eks"], live: ["offline"] },
    });
    act(() => {
      state.qs = "view=mine";
      state.listeners.forEach((fn) => fn());
    });
    expect(state.variables.at(-1)).toEqual({ ...COLD, filter: { registeredBy: ["me"] } });
  });

  it("sends a multi-key sort", () => {
    state.qs = "sort=-lastProbe,name";
    renderClusters();
    expect(state.variables.at(-1)).toEqual({ ...COLD, sort: "-lastProbe,name" });
  });

  it("renders the shared empty state in the card grid, not nothing", () => {
    state.page = { items: [], totalCount: 0 };
    renderClusters();
    showCards();
    expect(screen.getByText("No clusters registered")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Cluster prerequisites" })).toBeInTheDocument();
  });

  it("distinguishes a filtered-empty grid from an empty one", () => {
    state.page = { items: [], totalCount: 0 };
    state.qs = "q=nope";
    renderClusters();
    showCards();
    expect(screen.getByText("No clusters match")).toBeInTheDocument();
    expect(screen.queryByText("No clusters registered")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /clear/i }).length).toBeGreaterThan(0);
  });

  it("keeps the grid's geometry while the first page is loading", () => {
    state.page = null;
    state.loading = true;
    renderClusters();
    showCards();
    expect(document.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    expect(screen.queryByText("No clusters registered")).not.toBeInTheDocument();
  });
});
