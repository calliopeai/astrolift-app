import { createServer, type ServerResponse } from "node:http";
import { ApolloClient, ApolloLink, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RESOURCE } from "../../projects/resource-reads.fixtures";
import { useManagedResourceContext } from "./use-managed-resource-context";

const identity = vi.hoisted(() => ({
  userId: "actor-a",
  orgId: "01930000-0000-7000-8000-000000000002",
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.orgId } }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({ useMe: () => ({ user: { id: identity.userId } }) }));
const close: Array<() => Promise<void>> = [];
beforeEach(() => {
  identity.userId = "actor-a";
  identity.orgId = RESOURCE.organizationId;
});
afterEach(async () => {
  cleanup();
  for (const done of close.splice(0)) await done();
});

async function fixture() {
  const requests: Array<{
    actor: string;
    body: { query: string; variables: { id: string } };
    response: ServerResponse;
  }> = [];
  const server = createServer(async (req, response) => {
    let raw = "";
    for await (const data of req) raw += data;
    requests.push({ actor: String(req.headers.actor), body: JSON.parse(raw), response });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing fixture port");
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
        operation.setContext({ headers: { actor: identity.userId } });
        return forward(operation);
      }),
      new HttpLink({ uri: `http://127.0.0.1:${address.port}`, fetch }),
    ]),
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  function finish(index: number, extra: Record<string, unknown> = {}) {
    requests[index].response.setHeader("Content-Type", "application/json");
    requests[index].response.end(
      JSON.stringify({
        data: {
          astroliftManagedService: {
            ...RESOURCE,
            ownerScope: "app",
            projectId: null,
            registeredAppId: "app-guid",
            registeredAppSlug: "example-api",
            ...extra,
          },
        },
      })
    );
  }
  return { requests, wrapper, finish };
}

it("does not reuse an identical in-flight GUID read across actors or actor ABA", async () => {
  const f = await fixture();
  const hook = renderHook(() => useManagedResourceContext(RESOURCE.id, "example-api"), {
    wrapper: f.wrapper,
  });
  await waitFor(() => expect(f.requests).toHaveLength(1));
  identity.userId = "actor-b";
  hook.rerender();
  expect(hook.result.current.current).toBeNull();
  await waitFor(() => expect(f.requests).toHaveLength(2));
  expect(f.requests[1].actor).toBe("actor-b");
  await act(async () => {
    f.finish(1, { name: "actor-b-current" });
  });
  await waitFor(() => expect(hook.result.current.current?.name).toBe("actor-b-current"));
  identity.userId = "actor-a";
  hook.rerender();
  await waitFor(() => expect(f.requests).toHaveLength(3));
  await act(async () => {
    f.finish(0, { name: "old-actor-a" });
  });
  expect(hook.result.current.current).toBeNull();
  await act(async () => {
    f.finish(2, { name: "new-actor-a" });
  });
  await waitFor(() => expect(hook.result.current.current?.name).toBe("new-actor-a"));
  for (const request of f.requests)
    expect(request.body.query).not.toMatch(/config|statusError|secretGrants|backendRef/);
});

it("refuses a moved owner, null target and foreign organization rather than following a name", async () => {
  const f = await fixture();
  const hook = renderHook(() => useManagedResourceContext(RESOURCE.id, "example-api"), {
    wrapper: f.wrapper,
  });
  await waitFor(() => expect(f.requests).toHaveLength(1));
  await act(async () => {
    f.finish(0, { registeredAppSlug: "replacement-app" });
  });
  await waitFor(() => expect(hook.result.current.refused).toBe(true));
  act(() => hook.result.current.retry());
  await waitFor(() => expect(f.requests).toHaveLength(2));
  await act(async () => {
    f.requests[1].response.end(JSON.stringify({ data: { astroliftManagedService: null } }));
  });
  await waitFor(() => expect(hook.result.current.refused).toBe(true));
  act(() => hook.result.current.retry());
  await waitFor(() => expect(f.requests).toHaveLength(3));
  await act(async () => {
    f.finish(2, { organizationId: "foreign-org" });
  });
  await waitFor(() => expect(hook.result.current.refused).toBe(true));
  expect(hook.result.current.current).toBeNull();
  expect(f.requests.every((request) => request.body.variables.id === RESOURCE.id)).toBe(true);
});
