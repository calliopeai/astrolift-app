import { createServer, type ServerResponse } from "node:http";
import { ApolloClient, ApolloLink, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useManagedServiceMetrics } from "./use-managed-service-metrics";

const identity = vi.hoisted(() => ({ userId: "actor-a", orgId: "org-a" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.orgId } }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({ useMe: () => ({ user: { id: identity.userId } }) }));
const close: Array<() => Promise<void>> = [];
beforeEach(() => {
  identity.userId = "actor-a";
  identity.orgId = "org-a";
});
afterEach(async () => {
  cleanup();
  for (const done of close.splice(0)) await done();
});
async function fixture() {
  const requests: Array<{
    actor: string;
    org: string;
    variables: { managedServiceId: string; rangeSeconds: number; expectedContextRevision?: string };
    response: ServerResponse;
  }> = [];
  const server = createServer(async (req, response) => {
    let raw = "";
    for await (const part of req) raw += part;
    requests.push({
      actor: String(req.headers.actor),
      org: String(req.headers.org),
      variables: JSON.parse(raw).variables,
      response,
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No test port");
  close.push(
    () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      })
  );
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: ApolloLink.from([
      new ApolloLink((operation, forward) => {
        operation.setContext({ headers: { actor: identity.userId, org: identity.orgId } });
        return forward(operation);
      }),
      new HttpLink({ uri: `http://127.0.0.1:${address.port}`, fetch }),
    ]),
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  const finish = (index: number, value: number, id?: string) => {
    const request = requests[index];
    request.response.setHeader("Content-Type", "application/json");
    request.response.end(
      JSON.stringify({
        data: {
          astroliftAppManagedServiceMetrics: {
            managedServiceId: id ?? request.variables.managedServiceId,
            kind: "postgres",
            name: "database",
            rangeSeconds: request.variables.rangeSeconds,
            series: [
              {
                name: "connections",
                unit: "count",
                source: "prometheus",
                samples: [{ ts: "2026-10-02T00:00:00Z", value }],
              },
            ],
          },
        },
      })
    );
  };
  return { requests, wrapper, finish };
}
it("isolates identical in-flight metrics requests and cached samples across actor ABA", async () => {
  const f = await fixture();
  const hook = renderHook(() => useManagedServiceMetrics("service-guid", "revision-a"), {
    wrapper: f.wrapper,
  });
  await waitFor(() => expect(f.requests).toHaveLength(1));
  const oldRangeHandler = hook.result.current.onRangeChange;
  identity.userId = "actor-b";
  hook.rerender();
  expect(hook.result.current.data).toBeNull();
  await waitFor(() => expect(f.requests).toHaveLength(2));
  expect(f.requests[1].actor).toBe("actor-b");
  act(() => oldRangeHandler("15m"));
  expect(hook.result.current.range).toBe("1h");
  await act(async () => {
    f.finish(1, 22);
  });
  await waitFor(() => expect(hook.result.current.data?.series[0].samples[0].value).toBe(22));
  identity.userId = "actor-a";
  hook.rerender();
  expect(hook.result.current.data).toBeNull();
  await waitFor(() => expect(f.requests).toHaveLength(3));
  await act(async () => {
    f.finish(0, 11);
  });
  expect(hook.result.current.data).toBeNull();
  await act(async () => {
    f.finish(2, 33);
  });
  await waitFor(() => expect(hook.result.current.data?.series[0].samples[0].value).toBe(33));
  expect(
    f.requests.every((request) => request.variables.expectedContextRevision === "revision-a")
  ).toBe(true);
});
it("rejects delayed target/revision responses and refused current reads across organization changes", async () => {
  const f = await fixture();
  const hook = renderHook(({ revision }) => useManagedServiceMetrics("service-guid", revision), {
    wrapper: f.wrapper,
    initialProps: { revision: "revision-a" },
  });
  await waitFor(() => expect(f.requests).toHaveLength(1));
  hook.rerender({ revision: "revision-b" });
  await waitFor(() => expect(f.requests).toHaveLength(2));
  await act(async () => {
    f.finish(1, 22);
  });
  await waitFor(() => expect(hook.result.current.data?.series[0].samples[0].value).toBe(22));
  hook.rerender({ revision: "revision-a" });
  await waitFor(() => expect(f.requests).toHaveLength(3));
  await act(async () => {
    f.finish(0, 11);
  });
  expect(hook.result.current.data).toBeNull();
  await act(async () => {
    f.finish(2, 33);
  });
  await waitFor(() => expect(hook.result.current.data).not.toBeNull());
  identity.orgId = "org-b";
  hook.rerender({ revision: "revision-a" });
  expect(hook.result.current.data).toBeNull();
  await waitFor(() => expect(f.requests).toHaveLength(4));
  await act(async () => {
    f.requests[3].response.end(
      JSON.stringify({
        errors: [{ message: "PRIVATE_PROVIDER_MARKER", extensions: { code: "STALE_TARGET" } }],
      })
    );
  });
  await waitFor(() => expect(hook.result.current.error).toBe(true));
  expect(hook.result.current.data).toBeNull();
  expect(f.requests[3].org).toBe("org-b");
});
it("rejects a mismatched returned GUID instead of presenting its runtime samples", async () => {
  const f = await fixture();
  const hook = renderHook(() => useManagedServiceMetrics("service-guid", "revision-a"), {
    wrapper: f.wrapper,
  });
  await waitFor(() => expect(f.requests).toHaveLength(1));
  await act(async () => {
    f.finish(0, 44, "foreign-service-guid");
  });
  await waitFor(() => expect(hook.result.current.error).toBe(true));
  expect(hook.result.current.data).toBeNull();
});
