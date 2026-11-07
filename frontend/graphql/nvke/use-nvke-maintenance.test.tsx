import {
  ApolloClient,
  ApolloLink,
  InMemoryCache,
  Observable,
  type Operation,
} from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it } from "vitest";
import messages from "@/messages/en.json";
import { SITE, OPERATION, MAINTENANCE_OBSERVATION } from "@/components/screens/nvke/fixtures";
import type { NvkeMaintenanceObservation } from "@/components/screens/nvke/NvkeMaintenancePanel";
import { useNvkeMaintenance } from "./use-nvke-maintenance";
function harness() {
  const pending: { request: Operation; reply: (data: NvkeMaintenanceObservation) => void }[] = [],
    cache = new InMemoryCache();
  const client = new ApolloClient({
    cache,
    link: new ApolloLink(
      (request) =>
        new Observable((observer) => {
          pending.push({
            request,
            reply: (data) => {
              observer.next({ data: { astroliftNvkeMaintenanceObservation: data } });
              observer.complete();
            },
          });
        })
    ),
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  return { pending, client, cache, wrapper };
}
function args(actor = 41) {
  const scope = {
    server: "https://astro.example.test/app/gql/config/",
    organizationId: SITE.id,
    actorId: actor,
  };
  return {
    context: {
      scope,
      scopeKey: JSON.stringify(scope),
      access: { read: true, admin: false, manage: false, loading: false },
      loading: false,
      failed: false,
    },
    scopeKey: JSON.stringify(scope),
    siteId: SITE.id,
    operation: {
      ...OPERATION,
      id: MAINTENANCE_OBSERVATION.operationId,
      version: MAINTENANCE_OBSERVATION.operationVersion,
      operationKey: "getCluster",
      status: "SUCCEEDED" as const,
      isInventory: false,
    },
    enabled: true,
  };
}
it("reads exact saved maintenance metadata with actor/site/source fences and no cache", async () => {
  const h = harness();
  const { result } = renderHook(() => useNvkeMaintenance(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.pending).toHaveLength(1));
  expect(h.pending[0].request.variables).toEqual({
    siteId: SITE.id,
    operationId: MAINTENANCE_OBSERVATION.operationId,
    expectedActorId: 41,
    expectedVersion: MAINTENANCE_OBSERVATION.operationVersion,
  });
  await act(async () => h.pending[0].reply(MAINTENANCE_OBSERVATION));
  expect(result.current?.observation?.renewal?.retainedMutationLock).toBe(true);
  expect(h.cache.extract()).toEqual({});
  expect(h.pending[0].request.getContext()).toMatchObject({
    redactDiagnostics: true,
    queryDeduplication: false,
    expectedNvkeOrganization: SITE.id,
  });
  h.client.stop();
});
it("rejects mismatched observation headers", async () => {
  const h = harness();
  const { result } = renderHook(() => useNvkeMaintenance(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.pending).toHaveLength(1));
  await act(async () => h.pending[0].reply({ ...MAINTENANCE_OBSERVATION, operationVersion: 999 }));
  expect(result.current?.observation).toBeNull();
  expect(result.current?.error).toBe(messages.nvke.readFailed);
  h.client.stop();
});
it("ignores late responses across caller changes including A to B to A", async () => {
  const h = harness();
  const { result, rerender } = renderHook(({ actor }) => useNvkeMaintenance(args(actor)), {
    wrapper: h.wrapper,
    initialProps: { actor: 41 },
  });
  await waitFor(() => expect(h.pending).toHaveLength(1));
  rerender({ actor: 42 });
  await waitFor(() => expect(h.pending).toHaveLength(2));
  rerender({ actor: 41 });
  await waitFor(() => expect(h.pending).toHaveLength(3));
  await act(async () => {
    h.pending[0].reply(MAINTENANCE_OBSERVATION);
    h.pending[1].reply(MAINTENANCE_OBSERVATION);
  });
  expect(result.current?.observation).toBeNull();
  expect(result.current?.loading).toBe(true);
  await act(async () => h.pending[2].reply(MAINTENANCE_OBSERVATION));
  expect(result.current?.observation).not.toBeNull();
  h.client.stop();
});
