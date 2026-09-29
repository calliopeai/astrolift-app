import type { ReactNode } from "react";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { ClustersClient } from "./clusters-client";

/**
 * /clusters on the shared list (spec 44 §5.1). The card grid and the table
 * are two renderings of the SAME fleet (#1233), and the fleet is the whole
 * fleet: `astroliftClustersPage` is a keyset walk with no filter, sort or
 * offset argument, so the hook walks every page for the current search
 * (#1230) and filters, sorts and numbers pages over that. A list that sliced
 * the first fetched page in the browser would hide the fleet past it; these
 * tests pin that it does not, that both modes read one fleet, and that the
 * list's states (loading, empty, filtered-empty) show in both.
 */

// Mutable state the hoisted mocks read at call time — `vi.hoisted` runs
// before the `vi.mock` factories, so they can close over it safely.
const state = vi.hoisted(() => ({
  page: null as null | {
    items: Record<string, unknown>[];
    nextCursor: string | null;
    totalCount: number | null;
  },
  /** Pages after the first, by cursor, for the walk. */
  more: {} as Record<string, { items: Record<string, unknown>[]; nextCursor: string | null }>,
  loading: false,
  variables: [] as Record<string, unknown>[],
  walked: [] as Record<string, unknown>[],
  qs: "",
  listeners: new Set<() => void>(),
  client: {
    query: async ({ variables }: { variables: Record<string, unknown> }) => {
      state.walked.push(variables);
      return { data: { astroliftClustersPage: state.more[variables.after as string] } };
    },
  },
}));

// Feed the real hook a mocked transport: the code under test is the walk,
// the list state and both views, not Apollo.
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
  // One client for the life of the app, as Apollo's provider gives.
  useApolloClient: () => state.client,
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

const showCards = () => fireEvent.click(screen.getByRole("button", { name: "Card view" }));

describe("ClustersClient", () => {
  beforeEach(() => {
    // The list|cards choice persists per person, so a click in one test
    // would pick the starting view for the next one.
    localStorage.clear();
    state.loading = false;
    state.variables = [];
    state.walked = [];
    state.more = {};
    state.qs = "";
    state.page = {
      items: [cluster("prod"), cluster("staging")],
      nextCursor: null,
      totalCount: 2,
    };
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

  it("shows the same rows as cards without a second fetch", () => {
    renderClusters();
    const fetches = state.variables.length;
    showCards();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("PROD")).toBeInTheDocument();
    expect(screen.getByText("STAGING")).toBeInTheDocument();
    // Re-renders re-read the same query; none asks for a different page.
    expect(new Set(state.variables.slice(fetches).map((v) => JSON.stringify(v)))).toEqual(
      new Set([JSON.stringify({ search: null, limit: 200 })])
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
    await waitFor(() => expect(state.variables.at(-1)).toEqual({ search: "prod", limit: 200 }));
  });

  it("walks every page past the first, so no cluster is out of reach", async () => {
    state.page = { items: [cluster("prod")], nextCursor: "c2", totalCount: 3 };
    state.more = {
      c2: { items: [cluster("qa")], nextCursor: "c3" },
      c3: { items: [cluster("staging")], nextCursor: null },
    };
    renderClusters();
    expect(await screen.findByText("STAGING")).toBeInTheDocument();
    expect(screen.getByText("QA")).toBeInTheDocument();
    expect(state.walked.map((v) => v.after)).toEqual(["c2", "c3"]);
  });

  it("numbers pages over the whole fleet", () => {
    state.page = {
      items: Array.from({ length: 30 }, (_, i) => cluster(`c-${String(i).padStart(2, "0")}`)),
      nextCursor: null,
      totalCount: 30,
    };
    renderClusters();
    expect(screen.getByRole("button", { name: "Page 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /next page/i })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: /previous page/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Page 2" }));
    expect(state.qs).toBe("page=2");
    expect(screen.getByText("C-29")).toBeInTheDocument();
    expect(screen.queryByText("C-00")).not.toBeInTheDocument();
  });

  it("filters on the Offline view", () => {
    state.page = {
      items: [cluster("prod"), cluster("edge", { heartbeatStatus: "offline" })],
      nextCursor: null,
      totalCount: 2,
    };
    state.qs = "view=offline";
    renderClusters();
    expect(screen.getByText("EDGE")).toBeInTheDocument();
    expect(screen.queryByText("PROD")).not.toBeInTheDocument();
  });

  it("renders the shared empty state in the card grid, not nothing", () => {
    state.page = { items: [], nextCursor: null, totalCount: 0 };
    renderClusters();
    showCards();
    expect(screen.getByText("No clusters registered")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Cluster prerequisites" })).toBeInTheDocument();
  });

  it("distinguishes a filtered-empty grid from an empty one", () => {
    state.page = { items: [], nextCursor: null, totalCount: 0 };
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
