import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { buildSchema, parse, validate } from "graphql";
import { readFileSync } from "node:fs";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { CLUSTER_WORKLOAD_HEALTH } from "@/graphql/clusters/clusters.queries";
import { HEALTH, WORKLOADS } from "./fixtures";
import { useClusterHealth } from "./use-cluster-health";
import { useClusterWorkloadHealth } from "./use-cluster-workload-health";

const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
function transport(initial: "ok" | "wrong" | "deferred" = "ok") {
  let mode: "ok" | "wrong" | "deferred" | "failed" = initial;
  const pending: (() => void)[] = [];
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        expect(validate(schema, parse(request.query))).toEqual([]);
        const captured = mode;
        if (captured === "failed") throw new Error("RAW_HEALTH_READ_FAILURE");
        if (captured === "deferred") await new Promise<void>((resolve) => pending.push(resolve));
        const data =
          request.operationName === "ClusterHealth"
            ? {
                astroliftClusterHealth: {
                  clusterId: captured === "wrong" ? B : request.variables.clusterId,
                  pods: HEALTH.pods,
                  events: HEALTH.events,
                },
              }
            : {
                astroliftClusterWorkloadHealth: [
                  { ...WORKLOADS.rows[0], workloadName: request.variables.clusterId },
                ],
              };
        return new Response(JSON.stringify({ data }), {
          headers: { "content-type": "application/json" },
        });
      },
    }),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  return {
    client,
    wrapper,
    requests,
    set: (next: typeof mode) => {
      mode = next;
    },
    release: () => pending.splice(0).forEach((resolve) => resolve()),
  };
}

describe("cluster health observation reads", () => {
  it("rejects a driver response naming a different cluster", async () => {
    const h = transport("wrong");
    const view = renderHook(() => useClusterHealth(A, 25), { wrapper: h.wrapper });
    await waitFor(() => expect(view.result.current.loading).toBe(false));
    expect(h.requests).toHaveLength(1);
    expect(h.requests[0].variables).toEqual({ clusterId: A, eventLimit: 25 });
    expect(view.result.current.pods).toEqual([]);
    expect(view.result.current.events).toEqual([]);
    view.unmount();
    h.client.stop();
  });
  it("does not carry cached workload rows onto another target while its response is pending", async () => {
    const h = transport("deferred");
    h.client.cache.writeQuery({
      query: CLUSTER_WORKLOAD_HEALTH,
      variables: { clusterId: A },
      data: {
        astroliftClusterWorkloadHealth: [{ ...WORKLOADS.rows[0], workloadName: "CACHED_A" }],
      },
    });
    const view = renderHook(({ id }) => useClusterWorkloadHealth(id), {
      initialProps: { id: A },
      wrapper: h.wrapper,
    });
    await waitFor(() => expect(h.requests).toHaveLength(1));
    expect(view.result.current.rows[0].workloadName).toBe("CACHED_A");
    expect(view.result.current.loading).toBe(true);
    view.rerender({ id: B });
    await waitFor(() => expect(h.requests).toHaveLength(2));
    expect(view.result.current.rows).toEqual([]);
    await act(async () => h.release());
    await waitFor(() => expect(view.result.current.rows[0]?.workloadName).toBe(B));
    expect(view.result.current.loading).toBe(false);
    view.unmount();
    h.client.stop();
  });
  it("surfaces failed refreshes and recovers with the same scoped variables", async () => {
    const h = transport();
    const view = renderHook(() => useClusterHealth(A, 25), { wrapper: h.wrapper });
    await waitFor(() => expect(view.result.current.pods.length).toBeGreaterThan(0));
    h.set("failed");
    await act(async () => view.result.current.refetch());
    await waitFor(() => expect(view.result.current.error).toContain("RAW_HEALTH_READ_FAILURE"));
    h.set("ok");
    await act(async () => view.result.current.refetch());
    await waitFor(() => expect(view.result.current.error).toBeNull());
    expect(h.requests).toHaveLength(3);
    expect(
      h.requests.every((r) => r.variables.clusterId === A && r.variables.eventLimit === 25)
    ).toBe(true);
    expect(view.result.current.pods.length).toBeGreaterThan(0);
    view.unmount();
    h.client.stop();
  });
});
