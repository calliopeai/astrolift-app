import { createServer, type ServerResponse } from "node:http";
import { ApolloClient, ApolloLink, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { useScopedExplorer } from "./use-scoped-explorer";
import { APP, ENV, SCOPE } from "./explorer-fixtures";
const identity = vi.hoisted(() => ({ user: "actor-a", org: "org-guid" }));
vi.mock("@/graphql/user/user.hooks", () => ({ useMe: () => ({ user: { id: identity.user } }) }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org } }),
}));
const close: Array<() => Promise<void>> = [];
beforeEach(() => {
  identity.user = "actor-a";
  identity.org = "org-guid";
});
afterEach(async () => {
  cleanup();
  for (const done of close.splice(0)) await done();
});
async function fixture(mode: "logs" | "traces" = "logs") {
  const requests: Array<{
    name: string;
    variables: Record<string, unknown>;
    response: ServerResponse;
  }> = [];
  const server = createServer(async (req, response) => {
    let raw = "";
    for await (const part of req) raw += part;
    const body = JSON.parse(raw);
    requests.push({ name: body.operationName, variables: body.variables, response });
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
    link: ApolloLink.from([new HttpLink({ uri: `http://127.0.0.1:${address.port}`, fetch })]),
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <NextIntlClientProvider locale="en" messages={messages}>
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  const finish = (index: number, data: Record<string, unknown>) => {
    requests[index].response.setHeader("Content-Type", "application/json");
    requests[index].response.end(JSON.stringify({ data }));
  };
  const hook = renderHook(() => useScopedExplorer(mode), { wrapper });
  await waitFor(() => expect(requests).toHaveLength(1));
  finish(0, { astroliftAppsPage: { items: [APP], nextCursor: null, totalCount: 1 } });
  await waitFor(() => expect(hook.result.current.apps).toEqual([APP]));
  act(() => hook.result.current.onPickApp(APP));
  await waitFor(() => expect(requests).toHaveLength(2));
  finish(1, {
    astroliftEnvironmentsPage: {
      items: [ENV, { ...ENV, id: "preview-guid", name: "preview", kind: "preview" }],
      totalCount: 2,
    },
  });
  await waitFor(() => expect(hook.result.current.environments).toEqual([ENV]));
  act(() => hook.result.current.onPickEnvironment(ENV));
  await waitFor(() => expect(requests).toHaveLength(3));
  return { requests, finish, hook };
}
const line = (message: string) => ({
  podName: "owned-pod",
  container: "server",
  timestamp: "2026-10-03T00:00:00Z",
  message,
  level: "info",
  stream: "stdout",
});
const logs = (message: string, scope = SCOPE, cursor: string | null = null) => ({
  astroliftAppLogs: {
    reason: "OK",
    items: [line(message)],
    nextCursor: cursor,
    historicalAvailable: true,
    reachedRetention: false,
    totalCount: 1,
    scope,
  },
});
it("sends exact immutable environment and server cursor/filter without preview reads", async () => {
  const f = await fixture();
  const variables = f.requests[2].variables;
  expect(variables).toMatchObject({
    appSlug: APP.slug,
    environmentName: ENV.name,
    environmentId: ENV.id,
    limit: 200,
    cursor: null,
  });
  f.finish(2, logs("newer", SCOPE, "opaque-server-cursor"));
  await waitFor(() => expect(f.hook.result.current.lines[0].message).toBe("newer"));
  expect(f.hook.result.current.liveHref).toBe(
    "/apps/example/logs?section=metrics&panel=pods&env=production"
  );
  act(() => f.hook.result.current.onOlder());
  await waitFor(() => expect(f.requests).toHaveLength(4));
  expect(f.requests[3].variables.cursor).toBe("opaque-server-cursor");
  f.finish(3, logs("older"));
  await waitFor(() =>
    expect(f.hook.result.current.lines.map((row) => row.message)).toEqual(["older", "newer"])
  );
  act(() => f.hook.result.current.onSearch("request-id"));
  expect(f.hook.result.current.lines).toEqual([]);
  await waitFor(() => expect(f.requests).toHaveLength(5));
  expect(f.requests[4].variables).toMatchObject({
    cursor: null,
    search: "request-id",
    environmentId: ENV.id,
  });
});
it("discards an in-flight previous environment response and refuses a mismatched receipt", async () => {
  const f = await fixture();
  act(() => f.hook.result.current.onPickEnvironment({ ...ENV, id: "other-env", name: "staging" }));
  expect(f.hook.result.current.logs).toBeNull();
  await waitFor(() => expect(f.requests).toHaveLength(4));
  f.finish(2, logs("OLD_PRIVATE"));
  f.finish(3, logs("WRONG_PRIVATE"));
  await waitFor(() => expect(f.hook.result.current.error).toContain("no longer matches"));
  expect(f.hook.result.current.lines).toEqual([]);
  expect(f.hook.result.current.logs).toBeNull();
});
it("does not resurrect response or selection on actor ABA or organization switch", async () => {
  const f = await fixture();
  identity.user = "actor-b";
  f.hook.rerender();
  expect(f.hook.result.current.app).toBeNull();
  expect(f.hook.result.current.lines).toEqual([]);
  await waitFor(() => expect(f.requests).toHaveLength(4));
  identity.user = "actor-a";
  f.hook.rerender();
  await waitFor(() => expect(f.requests).toHaveLength(5));
  f.finish(2, logs("OLD_ACTOR_PRIVATE"));
  f.finish(3, { astroliftAppsPage: { items: [APP], nextCursor: null, totalCount: 1 } });
  f.finish(4, { astroliftAppsPage: { items: [APP], nextCursor: null, totalCount: 1 } });
  await waitFor(() => expect(f.hook.result.current.apps).toEqual([APP]));
  expect(f.hook.result.current.app).toBeNull();
  expect(f.hook.result.current.logs).toBeNull();
  expect(f.hook.result.current.lines).toEqual([]);
  identity.org = "org-b";
  f.hook.rerender();
  expect(f.hook.result.current.apps).toEqual([]);
  expect(f.hook.result.current.app).toBeNull();
});
it("keeps unconfigured, empty and provider error distinct and retries metadata", async () => {
  const f = await fixture();
  f.finish(2, {
    astroliftAppLogs: {
      reason: "NOT_CONFIGURED",
      items: [],
      nextCursor: null,
      historicalAvailable: false,
      reachedRetention: false,
      scope: SCOPE,
    },
  });
  await waitFor(() => expect(f.hook.result.current.logs?.reason).toBe("NOT_CONFIGURED"));
  expect(f.hook.result.current.liveHref).not.toBeNull();
  act(() => f.hook.result.current.onRefresh());
  await waitFor(() => expect(f.requests).toHaveLength(4));
  f.finish(3, {
    astroliftAppLogs: {
      reason: "NO_DATA_YET",
      items: [],
      nextCursor: null,
      historicalAvailable: true,
      reachedRetention: false,
      scope: SCOPE,
    },
  });
  await waitFor(() => expect(f.hook.result.current.logs?.reason).toBe("NO_DATA_YET"));
  act(() => f.hook.result.current.onRetryChoices());
  await waitFor(() => expect(f.requests).toHaveLength(6));
  expect(
    f.requests
      .slice(4)
      .map((row) => row.name)
      .sort()
  ).toEqual(["TelemetryApps", "TelemetryEnvironments"]);
});
it("scopes bounded trace detail to the admitted selected trace and discards stale filter results", async () => {
  const f = await fixture("traces");
  const traceId = "a".repeat(32);
  f.finish(2, {
    astroliftAppTracePage: {
      reason: "OK",
      items: [
        {
          traceId,
          rootService: "owned",
          rootOperation: "GET /health",
          durationMs: 5,
          spanCount: 1,
          statusCode: "OK",
        },
      ],
      truncated: true,
      limit: 10,
      scope: SCOPE,
    },
  });
  await waitFor(() => expect(f.hook.result.current.traces?.items).toHaveLength(1));
  act(() => f.hook.result.current.onTrace(traceId));
  await waitFor(() => expect(f.requests).toHaveLength(4));
  expect(f.requests[3].variables).toMatchObject({
    traceId,
    environmentId: ENV.id,
    since: f.requests[2].variables.since,
    until: f.requests[2].variables.until,
  });
  act(() => f.hook.result.current.onSearch("other-service"));
  expect(f.hook.result.current.spans).toBeNull();
  f.finish(3, {
    astroliftTraceSpansResult: {
      reason: "OK",
      items: [
        {
          traceId,
          spanId: "1".repeat(16),
          parentSpanId: null,
          service: "owned",
          operation: "OLD_PRIVATE",
          startTime: "1",
          durationMs: 5,
          statusCode: "OK",
          attributes: {},
          resourceAttributes: {},
        },
      ],
      scope: SCOPE,
    },
  });
  await waitFor(() => expect(f.requests).toHaveLength(5));
  expect(f.requests[4].variables).toMatchObject({ service: "other-service", limit: 10 });
  expect(f.hook.result.current.spans).toBeNull();
});
it("resets the previous organization cursor before any new chooser request and on actor ABA", async () => {
  const f = await fixture();
  act(() => f.hook.result.current.onPickApp(null));
  act(() => f.hook.result.current.list.older("old-org-page-cursor"));
  await waitFor(() => expect(f.requests).toHaveLength(4));
  expect(f.requests[3].variables.cursor).toBe("old-org-page-cursor");
  identity.org = "org-b";
  f.hook.rerender();
  expect(f.hook.result.current.apps).toEqual([]);
  await waitFor(() => expect(f.requests).toHaveLength(5));
  expect(f.requests[4].variables.cursor).toBeNull();
  identity.org = "org-guid";
  f.hook.rerender();
  await waitFor(() => expect(f.requests).toHaveLength(6));
  expect(f.requests[5].variables.cursor).toBeNull();
  f.finish(3, {
    astroliftAppsPage: {
      items: [{ ...APP, name: "OLD_ORG_PRIVATE" }],
      nextCursor: null,
      totalCount: 1,
    },
  });
  f.finish(4, { astroliftAppsPage: { items: [APP], nextCursor: null, totalCount: 1 } });
  f.finish(5, { astroliftAppsPage: { items: [], nextCursor: null, totalCount: 0 } });
  await waitFor(() => expect(f.hook.result.current.appsLoading).toBe(false));
  expect(f.hook.result.current.apps).toEqual([]);
});
