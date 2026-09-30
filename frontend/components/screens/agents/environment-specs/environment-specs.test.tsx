import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { activeFor, NAV, visibleNav } from "@/lib/shell/nav-model";

import { environmentSpecsVariables } from "./environment-specs";
import { ENVIRONMENT_SPEC } from "./environment-specs.fixtures";
import { useEnvironmentSpec, useEnvironmentSpecs } from "./use-environment-specs";

let orgId = "org-a";
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: orgId ? { id: orgId } : null, loading: !orgId }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/agents/environment-specs",
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => {
  orgId = "org-a";
});

function network() {
  const requests: Array<{ operation: string; variables: Record<string, unknown> }> = [];
  let fail = false;
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (op) =>
        new Observable((observer) => {
          requests.push({ operation: op.operationName ?? "", variables: op.variables });
          const requestedOrg = String(op.variables.orgId);
          const timer = setTimeout(() => {
            if (fail) observer.error(new Error("Recipe read refused"));
            else {
              const spec = {
                ...ENVIRONMENT_SPEC,
                __typename: "AstroliftAgentEnvironmentSpec",
                id: requestedOrg,
                name: requestedOrg,
                slug: String(op.variables.slug ?? ENVIRONMENT_SPEC.slug),
              };
              observer.next({
                data:
                  op.operationName === "AgentEnvironmentSpecDetail"
                    ? { agentEnvironmentSpec: spec }
                    : {
                        agentEnvironmentSpecsPage: {
                          items: [spec],
                          totalCount: 205,
                          page: 1,
                          pageSize: 25,
                        },
                      },
              });
              observer.complete();
            }
          }, 15);
          return () => clearTimeout(timer);
        })
    ),
  });
  return {
    client,
    requests,
    setFailure: (value: boolean) => {
      fail = value;
    },
    wrapper: ({ children }: { children: ReactNode }) => (
      <ApolloProvider client={client}>{children}</ApolloProvider>
    ),
  };
}

it("sends search, Mine, type, multi-sort and page state to the server", () => {
  expect(
    environmentSpecsVariables({
      q: " recipe ",
      filters: { agentType: "codex", createdBy: "me" },
      sort: [
        { key: "updated", dir: "desc" },
        { key: "name", dir: "asc" },
      ],
      page: 9,
      pageSize: 100,
    })
  ).toEqual({
    search: "recipe",
    filter: { agentType: ["codex"], createdBy: ["me"] },
    sort: "-updated,name",
    page: 9,
    pageSize: 100,
  });
});

it("exposes server counts, retries failures and retains recipes with an explicit refresh error", async () => {
  const n = network();
  const h = renderHook(useEnvironmentSpecs, { wrapper: n.wrapper });
  await waitFor(() => expect(h.result.current.totalCount).toBe(205));
  n.setFailure(true);
  act(() => h.result.current.onRetry());
  await waitFor(() => expect(h.result.current.error?.message).toContain("Recipe read refused"));
  expect(h.result.current.rows[0].name).toBe("org-a");
  n.setFailure(false);
  act(() => h.result.current.onRetry());
  await waitFor(() => expect(h.result.current.error).toBeNull());
  h.unmount();
  n.client.stop();
});

it("never carries the previous organization's recipes into a new organization", async () => {
  const n = network();
  const h = renderHook(useEnvironmentSpecs, { wrapper: n.wrapper });
  await waitFor(() => expect(h.result.current.rows[0]?.name).toBe("org-a"));
  orgId = "org-b";
  h.rerender();
  expect(h.result.current.rows.every((s) => s.name !== "org-a")).toBe(true);
  await waitFor(() => expect(h.result.current.rows[0]?.name).toBe("org-b"));
  expect(n.requests.at(-1)?.variables.orgId).toBe("org-b");
  h.unmount();
  n.client.stop();
});

it("separates identical detail slugs by organization and retains a failed refresh only in that context", async () => {
  const n = network();
  const h = renderHook(() => useEnvironmentSpec("same-slug"), { wrapper: n.wrapper });
  await waitFor(() => expect(h.result.current.spec?.name).toBe("org-a"));
  n.setFailure(true);
  act(() => h.result.current.onRetry());
  await waitFor(() => expect(h.result.current.error).toBeTruthy());
  expect(h.result.current.spec?.name).toBe("org-a");
  n.setFailure(false);
  orgId = "org-b";
  h.rerender();
  expect(h.result.current.spec?.name).not.toBe("org-a");
  await waitFor(() => expect(h.result.current.spec?.name).toBe("org-b"));
  expect(n.requests.at(-1)?.variables).toEqual({ slug: "same-slug", orgId: "org-b" });
  h.unmount();
  n.client.stop();
});

it("waits for an active organization before querying", async () => {
  orgId = "";
  const n = network();
  const h = renderHook(useEnvironmentSpecs, { wrapper: n.wrapper });
  expect(h.result.current.loading).toBe(true);
  expect(n.requests).toHaveLength(0);
  orgId = "org-a";
  h.rerender();
  await waitFor(() => expect(n.requests).toHaveLength(1));
  h.unmount();
  n.client.stop();
});

it("has a distinct Agents nav home that stays behind the agents module", () => {
  expect(activeFor(NAV, "/agents/environment-specs/same-slug")).toEqual({
    area: "agents",
    fn: "environment-specs",
  });
  const enabled = visibleNav(NAV, (m) => m === "agents");
  expect(
    enabled
      .flatMap((a) => a.groups.flatMap((g) => g.functions))
      .some((f) => f.href === "/agents/environment-specs")
  ).toBe(true);
  const hidden = visibleNav(NAV, (m) => m === "apps");
  expect(
    hidden
      .flatMap((a) => a.groups.flatMap((g) => g.functions))
      .some((f) => f.href === "/agents/environment-specs")
  ).toBe(false);
});
