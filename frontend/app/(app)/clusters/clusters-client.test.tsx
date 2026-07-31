import type { ReactNode } from "react";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { ClustersClient } from "./clusters-client";

/**
 * /clusters is the one migrated surface with a live ViewToggle: the card
 * grid and the table are two renderings of the SAME `useCursorTable` walk
 * (#1233). A card view that fetched its own rows — or sliced a fetched
 * page in the browser, which is what this surface used to do through
 * `useListControls` — would put the two modes on different data and hide
 * the fleet past the first page in one of them. These tests pin that both
 * modes read one controller, and that the card grid carries the four
 * states DataTable gives the table for free.
 */

// Mutable state the hoisted mocks read at call time — `vi.hoisted` runs
// before the `vi.mock` factories, so they can close over it safely.
const state = vi.hoisted(() => ({
  page: null as null | {
    items: Record<string, unknown>[];
    nextCursor: string | null;
    totalCount: number | null;
  },
  loading: false,
}));

// Feed the real controller a mocked transport: the hook under test is
// `useCursorTable`, not Apollo, and both views have to run for real.
vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({
    data: state.page ? { astroliftClustersPage: state.page } : undefined,
    previousData: undefined,
    loading: state.loading,
    error: undefined,
    refetch: vi.fn().mockResolvedValue({}),
  }),
  useMutation: () => [vi.fn().mockResolvedValue({ data: {} }), { loading: false }],
}));

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

// PageShell reads the app-chrome context; render a thin shell so the test
// targets this client rather than page chrome.
vi.mock("@/components/PageShell", () => ({
  PageShell: ({ actions, children }: { actions?: ReactNode; children?: ReactNode }) => (
    <div>
      <div>{actions}</div>
      {children}
    </div>
  ),
}));

function cluster(slug: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: `id-${slug}`,
    slug,
    name: slug.toUpperCase(),
    isActive: true,
    lifecycle: "managed",
    lastManagementError: null,
    providerPluginSlug: "aws",
    region: "us-west-2",
    ingressClass: "nginx",
    heartbeatStatus: "connected",
    heartbeatAgeSeconds: 12,
    capabilitiesProbedAt: "2026-07-30T00:00:00Z",
    ...over,
  };
}

// The lifecycle and heartbeat badges are tooltip triggers; the provider
// lives in the root layout, so the test supplies it.
const renderClusters = () =>
  render(
    <TooltipProvider>
      <ClustersClient />
    </TooltipProvider>
  );

const showList = () => fireEvent.click(screen.getByRole("button", { name: "List view" }));

describe("ClustersClient", () => {
  beforeEach(() => {
    // useViewToggle persists the mode, so a click in one test would pick
    // the starting view for the next one.
    localStorage.clear();
    state.loading = false;
    state.page = {
      items: [cluster("prod"), cluster("staging")],
      nextCursor: null,
      totalCount: 2,
    };
  });

  it("renders one card per row of the page the controller fetched", () => {
    renderClusters();
    expect(screen.getByText("PROD")).toBeInTheDocument();
    expect(screen.getByText("STAGING")).toBeInTheDocument();
    // Card mode is not a table: the rows are links to the detail page.
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the same rows in the table without a second fetch", () => {
    renderClusters();
    showList();
    const table = screen.getByRole("table", { name: "Clusters" });
    expect(within(table).getByText("PROD")).toBeInTheDocument();
    expect(within(table).getByText("STAGING")).toBeInTheDocument();
  });

  it("offers server-side search in both modes", () => {
    renderClusters();
    expect(screen.getByRole("textbox", { name: "Search clusters..." })).toBeInTheDocument();
    showList();
    expect(screen.getByRole("textbox", { name: "Search clusters..." })).toBeInTheDocument();
  });

  it("pages the card grid from the controller's cursor walk", () => {
    state.page = { items: [cluster("prod")], nextCursor: "cursor-2", totalCount: 40 };
    renderClusters();
    // The grid gets DataTable's pagination, so a fleet longer than one
    // page is reachable in card mode too — it was not before (#1233).
    expect(screen.getByRole("button", { name: /next page/i })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: /previous page/i })).toBeDisabled();
  });

  it("renders the shared empty state in the card grid, not nothing", () => {
    state.page = { items: [], nextCursor: null, totalCount: 0 };
    renderClusters();
    expect(screen.getByText("No clusters registered")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Cluster prerequisites" })).toBeInTheDocument();
  });

  it("distinguishes a filtered-empty grid from an empty one", () => {
    state.page = { items: [], nextCursor: null, totalCount: 0 };
    renderClusters();
    fireEvent.change(screen.getByRole("textbox", { name: "Search clusters..." }), {
      target: { value: "nope" },
    });
    // The controller debounces, so the term reaches `isFiltered` only after
    // the debounce window; drive it explicitly rather than waiting on a timer.
    return vi.waitFor(() => {
      expect(screen.getByText("No matching clusters")).toBeInTheDocument();
      expect(screen.queryByText("No clusters registered")).not.toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: /clear search/i }).length).toBeGreaterThan(0);
    });
  });

  it("keeps the grid's geometry while the first page is loading", () => {
    state.page = null;
    state.loading = true;
    const { container } = renderClusters();
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    expect(screen.queryByText("No clusters registered")).not.toBeInTheDocument();
  });
});
