import { renderWithIntl as render } from "@/test/render-with-intl";
import type { ReactNode } from "react";

import { fireEvent, screen } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { HOME_QUESTION } from "@/components/home/HomeScreen";

import { HomeClient } from "./home-client";

/**
 * Home at /dashboard, wired for real: the first-sign-in question shows once,
 * and each panel's queries run only for what the person may see (what the
 * old DashboardClient test held for the dashboard it replaced).
 */

type Call = { operation: string; skip: boolean; variables: unknown };

const state = vi.hoisted(() => ({
  modules: new Set<string>(),
  permissions: new Set<string>(),
  calls: [] as Call[],
}));

const opName = (doc: { definitions?: Array<{ kind: string; name?: { value: string } }> }) =>
  doc.definitions?.find((d) => d.kind === "OperationDefinition")?.name?.value ?? "";

vi.mock("@apollo/client/react", () => ({
  useQuery: (
    doc: Parameters<typeof opName>[0],
    options?: { skip?: boolean; variables?: unknown }
  ) => {
    state.calls.push({
      operation: opName(doc),
      skip: options?.skip ?? false,
      variables: options?.variables,
    });
    return {
      data: undefined,
      previousData: undefined,
      loading: !options?.skip,
      error: undefined,
      refetch: () => Promise.resolve(),
      fetchMore: () => Promise.resolve(),
    };
  },
  useMutation: () => [() => Promise.resolve({}), { loading: false }],
  useSubscription: () => ({}),
  useApolloClient: () => ({ query: () => Promise.resolve({ data: undefined }) }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next-intl", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next-intl")>();
  return {
    ...actual,
    useTranslations: (namespace?: string) =>
      namespace === "home" || namespace?.startsWith("shared.")
        ? actual.useTranslations(namespace)
        : (key: string) => key,
    useLocale: () => "en",
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {}, prefetch: () => {} }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("./onboarding-host", () => ({ OnboardingHost: () => null }));

vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1", slug: "acme" } }),
}));

vi.mock("@/graphql/user/user.hooks", () => ({
  useModules: () => ({
    loading: false,
    modules: new Map([...state.modules].map((k) => [k, { key: k, canView: true }])),
    canView: (key: string) => state.modules.has(key),
  }),
  useMe: () => ({ user: { profile: { username: "leo" } }, loading: false }),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    loading: false,
    granted: state.permissions,
    can: (p: string) => state.permissions.has(p),
  }),
}));

const ran = (operation: string) => state.calls.filter((c) => c.operation === operation && !c.skip);

describe("HomeClient", () => {
  beforeAll(() => {
    window.localStorage.clear();
  });

  beforeEach(() => {
    state.modules = new Set(["apps", "agents"]);
    state.permissions = new Set(["app.read", "agent.read"]);
    state.calls = [];
  });

  it("asks the one question on first sign-in, fetches no panel until it is answered, and asks once", () => {
    const first = render(<HomeClient />);
    expect(screen.getByRole("group", { name: HOME_QUESTION })).toBeTruthy();
    // Only the access reads (mocked here) may run before the answer.
    expect(state.calls.filter((c) => !c.skip)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Home · Builder");
    first.unmount();

    render(<HomeClient />);
    expect(screen.queryByRole("group", { name: HOME_QUESTION })).toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Home · Builder");
  });

  it("runs only the queries the person's access allows, and reads the run window once", () => {
    render(<HomeClient />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Home · Builder");

    // No audit_log.read: Activity is not drawn, so its feed never asks.
    expect(state.calls.some((c) => c.operation === "GetRecentActivity")).toBe(false);
    expect(screen.queryByRole("heading", { name: "Activity" })).toBeNull();
    // No billing.read: the KPI strip leaves spend out and skips its query.
    expect(ran("GetCostForecast")).toEqual([]);
    expect(ran("GetDeploymentMetrics")).toHaveLength(1);

    // Agent runs and the KPI strip ask for the same window: one cache entry.
    const windows = ran("ListAgentTasksPage").filter(
      (c) => (c.variables as { status: unknown; limit: number }).status === null
    );
    expect(windows.length).toBeGreaterThanOrEqual(2);
    expect(new Set(windows.map((c) => JSON.stringify(c.variables))).size).toBe(1);
  });

  it("never reads deploy figures for a person with Agents only", () => {
    state.modules = new Set(["agents"]);
    render(<HomeClient />);
    // The saved Builder is no longer offered, so Home falls back to Agents.
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Home · Agents");
    expect(ran("GetDeploymentMetrics")).toEqual([]);
    expect(ran("ListDeploymentsPage")).toEqual([]);
  });

  it("draws the Operator panels the person may see, and fetches for no other", () => {
    state.modules = new Set(["apps", "agents", "admin"]);
    render(<HomeClient />);
    fireEvent.pointerDown(screen.getByRole("button", { name: /Layout/ }), { button: 0 });
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Operator/ }));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Home · Operator");
    expect(ran("ListAlertEventsPage")).toHaveLength(1);
    expect(ran("ListClustersPage")).toHaveLength(1);
    // No billing.read and no audit_log.read: neither panel is drawn or asks.
    expect(state.calls.some((c) => c.operation === "ListBudgets")).toBe(false);
    expect(state.calls.some((c) => c.operation === "ListWorkflowRuns")).toBe(false);
  });
});
