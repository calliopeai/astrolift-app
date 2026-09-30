import { renderWithIntl as render } from "@/test/render-with-intl";
import type { ReactNode } from "react";

import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EventsClient } from "./events-client";

/**
 * /events was the surface that fired a server query per keystroke: the
 * filter input was wired straight into `eventType`, and a second,
 * client-side `useListControls` pass sliced whatever fit under a
 * `limit: 200` fetch (#1230). Both are gone, and the stream is a Feed now
 * (list rule 5). These tests pin the claims that replaced them: the search
 * box is debounced and server-side, older events load on the server's
 * cursor, and grouping still opens a bucket's members, because all three
 * regress silently.
 */

type Vars = Record<string, unknown>;

const state = vi.hoisted(() => ({
  raw: null as null | { items: Record<string, unknown>[]; nextCursor: string | null },
  agg: null as null | {
    items: Record<string, unknown>[];
    nextCursor: string | null;
    totalCount: number | null;
  },
  calls: [] as { op: string; variables: Record<string, unknown> }[],
}));

function pageFor(op: string) {
  return op === "ListEventsAggregatedPage"
    ? state.agg && { astroliftEventsAggregatedPage: state.agg }
    : state.raw && { astroliftEventsPage: state.raw };
}

// The hook under test is `useCursorFeed`, not Apollo: mock the transport
// and record what each query was actually asked for, since "what reached
// the server" is the whole point of this migration. Older pages go through
// the client's `query`, so it records too.
vi.mock("@apollo/client/react", () => ({
  useApolloClient: () => ({
    query: async ({
      query: doc,
      variables,
    }: {
      query: { definitions?: { kind: string; name?: { value: string } }[] };
      variables?: Vars;
    }) => {
      const op = doc.definitions?.find((d) => d.kind === "OperationDefinition")?.name?.value ?? "";
      state.calls.push({ op, variables: variables ?? {} });
      return { data: pageFor(op) ?? undefined };
    },
  }),
  useQuery: (
    doc: { definitions?: { kind: string; name?: { value: string } }[] },
    options?: { variables?: Vars }
  ) => {
    const op = doc.definitions?.find((d) => d.kind === "OperationDefinition")?.name?.value ?? "";
    state.calls.push({ op, variables: options?.variables ?? {} });
    const data = pageFor(op);
    return {
      data: data ?? undefined,
      previousData: undefined,
      loading: false,
      error: undefined,
      refetch: vi.fn().mockResolvedValue({}),
    };
  },
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const MESSAGES: Record<string, string> = {
  title: "Events",
  description: "Append-only platform fact log.",
  filterPlaceholder: "Filter by event type (e.g. app.deployed)",
  aggregateToggle: "Group repeats",
  expandLabel: "Expand",
  sourceLabel: "Source",
  emptyTitle: "Quiet feed",
  emptyDescription: "No events match the current filter.",
};

vi.mock("next-intl", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next-intl")>();
  return {
    ...actual,
    useTranslations: (namespace?: string) =>
      namespace?.startsWith("shared.")
        ? actual.useTranslations(namespace)
        : (key: string, vars?: Record<string, unknown>) =>
            key === "aggregateBadge" ? `×${vars?.count} similar` : (MESSAGES[key] ?? key),
  };
});

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
  PageShell: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));

function event(id: string, eventType: string): Record<string, unknown> {
  return {
    id,
    eventType,
    payload: { app_slug: "api" },
    organizationId: "org-1",
    teamId: null,
    projectId: null,
    registeredAppId: null,
    occurredAt: new Date().toISOString(),
    resourceKind: "app",
    resourceId: "api",
    severity: "info",
  };
}

// The feed's own calls: the rate card passes no `search`, and a bucket's
// member fetch is the only one that pins `eventType`.
const tableCalls = () =>
  state.calls.filter((c) => "search" in c.variables && c.variables.eventType === undefined);
const lastTableVars = () => tableCalls()[tableCalls().length - 1].variables;

const ungroup = () => fireEvent.click(screen.getByRole("checkbox", { name: "Group repeats" }));
const searchBox = () => screen.getByRole("searchbox", { name: MESSAGES.filterPlaceholder });

describe("EventsClient", () => {
  beforeEach(() => {
    state.calls = [];
    state.raw = {
      items: [event("e1", "app.deployed"), event("e2", "workload.unhealthy")],
      nextCursor: null,
    };
    state.agg = {
      items: [
        {
          representative: event("e1", "app.deployed"),
          count: 3,
          firstAt: new Date().toISOString(),
          lastAt: new Date().toISOString(),
          eventType: "app.deployed",
          resourceKind: "app",
          resourceId: "api",
        },
      ],
      nextCursor: null,
      totalCount: 3,
    };
  });

  it("renders each raw event as a real link to its detail page", () => {
    render(<EventsClient />);
    ungroup();
    const feed = screen.getByRole("region", { name: "Events" });
    expect(within(feed).getByRole("link", { name: "app.deployed" })).toHaveAttribute(
      "href",
      "/events/e1"
    );
  });

  it("debounces the search instead of querying the server per keystroke", async () => {
    render(<EventsClient />);
    ungroup();

    fireEvent.change(searchBox(), { target: { value: "a" } });
    fireEvent.change(searchBox(), { target: { value: "app.dep" } });
    // The input is no longer wired into query variables: nothing has been
    // asked of the server yet.
    expect(lastTableVars().search).toBeNull();

    await vi.waitFor(() => expect(lastTableVars().search).toBe("app.dep"));
    // …and only the settled term ever reached it — no query for "a".
    const searched = tableCalls()
      .map((c) => c.variables.search)
      .filter(Boolean);
    expect([...new Set(searched)]).toEqual(["app.dep"]);
  });

  it("loads older events with the cursor the server handed back, not a client slice", async () => {
    state.agg = { ...state.agg!, nextCursor: "cursor-2", totalCount: 40 };
    render(<EventsClient />);
    expect(lastTableVars().after).toBeUndefined();

    fireEvent.click(screen.getByRole("button", { name: "Load older" }));
    // The older page is its own request; the newest page keeps polling as it was.
    await vi.waitFor(() =>
      expect(tableCalls().some((c) => c.variables.after === "cursor-2")).toBe(true)
    );
  });

  it("keeps the roll-up window out of the walk's way", () => {
    render(<EventsClient />);
    // Grouping is a server-side fold now; the window is a query argument,
    // not a client-side pass over a fetched page.
    expect(lastTableVars().aggregateWindowSeconds).toBe(300);
    expect(screen.getByRole("region", { name: "Event groups" })).toBeInTheDocument();
  });

  it("opens a bucket's members in a dialog", async () => {
    render(<EventsClient />);
    expect(screen.getByText("×3 similar")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand" }));

    const dialog = await vi.waitFor(() => screen.getByRole("dialog"));
    // Narrowed server-side by type + resource before the exact match, so
    // the fetch carries both.
    const members = state.calls.filter((c) => c.variables.eventType !== undefined);
    expect(members[members.length - 1].variables).toMatchObject({
      eventType: "app.deployed",
      search: "api",
    });
    expect(within(dialog).getByRole("link", { name: /app\.deployed/ })).toHaveAttribute(
      "href",
      "/events/e1"
    );
  });

  it("drops the sort controls the old page ran over one fetched page", () => {
    render(<EventsClient />);
    ungroup();
    // `astroliftEventsPage` takes no sort argument, so sorting could only
    // have reordered the page in hand — wrong at every page boundary.
    expect(screen.queryByRole("button", { name: /^Time$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Type$/ })).not.toBeInTheDocument();
  });
});
