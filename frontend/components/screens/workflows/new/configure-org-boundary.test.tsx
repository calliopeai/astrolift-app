import { ApolloClient, ApolloLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { Observable } from "rxjs";
import { expect, it, vi } from "vitest";

import { useConfigureWorkflow } from "./use-configure-workflow";

const active = vi.hoisted(() => ({ org: null as { id: string } | null }));
vi.mock("@/graphql/identity/identity.hooks", () => ({ useActiveOrg: () => active }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/workflows/use-picker-options", () => ({
  useAgentWorkloadOptions: () => ({ options: [], loading: false }),
}));
vi.mock("@/graphql/workflows/tiered.hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/graphql/workflows/tiered.hooks")>()),
  useWorkflowsEntitlement: () => ({ canCreate: true, loading: false }),
}));

it("waits for an active org and selects that org for each definition lookup", async () => {
  const requests: Record<string, unknown>[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push({ name: operation.operationName, ...operation.variables });
          observer.next({
            data:
              operation.operationName === "GetTieredWorkflowDefinition"
                ? { workflowDefinition: null }
                : { workflowStages: [] },
          });
          observer.complete();
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  const hook = renderHook(() => useConfigureWorkflow("shared-slug"), { wrapper });
  await act(async () => {});
  expect(requests).toEqual([]);
  active.org = { id: "org-a" };
  await act(async () => hook.rerender());
  active.org = { id: "org-b" };
  await act(async () => hook.rerender());
  expect(requests.filter(({ name }) => name === "GetTieredWorkflowDefinition")).toEqual([
    { name: "GetTieredWorkflowDefinition", slug: "shared-slug", orgId: "org-a" },
    { name: "GetTieredWorkflowDefinition", slug: "shared-slug", orgId: "org-b" },
  ]);
  hook.unmount();
  client.stop();
  active.org = null;
});
