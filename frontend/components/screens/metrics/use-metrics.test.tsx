import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { buildSchema, parse, validate } from "graphql";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APP_HEALTH, DEPLOYMENT_METRICS } from "../ops/metrics-ops-shell.fixtures";
import { useMetrics } from "./use-metrics";

vi.mock("next/navigation", () => ({
  usePathname: () => "/metrics",
  useSearchParams: () => new URLSearchParams("q=storefront"),
  useRouter: () => ({ replace: vi.fn() }),
}));

const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
type Mode = "ok" | "refused" | "offline";
type Request = { query: string; variables: Record<string, unknown>; operationName: string };

function consumer(initial: Mode) {
  let mode = initial;
  const requests: Request[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://metrics.test.invalid/graphql/",
      fetch: async (_uri, options) => {
        const request = JSON.parse(String(options?.body)) as Request;
        requests.push(request);
        expect(validate(schema, parse(request.query))).toEqual([]);
        expect(parse(request.query).definitions).toEqual(
          expect.arrayContaining([
            expect.objectContaining({ kind: "OperationDefinition", operation: "query" }),
          ])
        );
        const metrics = request.operationName === "GetDeploymentMetrics";
        expect(["GetDeploymentMetrics", "ListAppHealthSummary"]).toContain(request.operationName);
        expect(request.variables).toEqual(metrics ? { windowDays: 30 } : {});
        if (mode === "offline") throw new Error("ORIGINAL_TRANSPORT_UNAVAILABLE");
        const body =
          mode === "refused"
            ? { errors: [{ message: metrics ? "RAW_METRICS_DENIAL" : "RAW_HEALTH_DENIAL" }] }
            : {
                data: metrics
                  ? { astroliftDeploymentMetrics: DEPLOYMENT_METRICS }
                  : { astroliftAppHealthSummary: APP_HEALTH },
              };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
    }),
  });
  const hook = renderHook(() => useMetrics(), {
    wrapper: ({ children }) => <ApolloProvider client={client}>{children}</ApolloProvider>,
  });
  return {
    ...hook,
    requests,
    setMode: (next: Mode) => {
      mode = next;
    },
    stop: () => {
      hook.unmount();
      client.stop();
    },
  };
}

beforeEach(() => localStorage.clear());

describe("metrics actual Apollo reads and recovery", () => {
  it.each(["refused", "offline"] as const)(
    "keeps %s initial failures visible and retries both original queries",
    async (mode) => {
      const view = consumer(mode);
      try {
        await waitFor(() => {
          expect(view.result.current.metricsError).toBe(
            mode === "refused" ? "RAW_METRICS_DENIAL" : "ORIGINAL_TRANSPORT_UNAVAILABLE"
          );
          expect(view.result.current.healthError).toBe(
            mode === "refused" ? "RAW_HEALTH_DENIAL" : "ORIGINAL_TRANSPORT_UNAVAILABLE"
          );
        });
        expect(view.result.current.metrics).toBeUndefined();
        expect(view.result.current.apps).toEqual([]);
        expect(view.result.current.list.state.q).toBe("storefront");
        expect(view.requests).toHaveLength(2);
        view.setMode("ok");
        act(() => {
          view.result.current.onRetryMetrics();
          view.result.current.onRetryHealth();
        });
        await waitFor(() => {
          expect(view.result.current.metrics).toEqual(DEPLOYMENT_METRICS);
          expect(view.result.current.apps).toEqual(APP_HEALTH);
          expect(view.result.current.metricsError).toBeNull();
          expect(view.result.current.healthError).toBeNull();
        });
        expect(view.requests).toHaveLength(4);
      } finally {
        view.stop();
      }
    }
  );

  it.each(["refused", "offline"] as const)(
    "retains the last successful snapshot after a %s refresh and recovers",
    async (mode) => {
      const view = consumer("ok");
      try {
        await waitFor(() => expect(view.result.current.apps).toEqual(APP_HEALTH));
        expect(view.result.current.metrics).toEqual(DEPLOYMENT_METRICS);
        view.setMode(mode);
        act(() => {
          view.result.current.onRetryMetrics();
          view.result.current.onRetryHealth();
        });
        await waitFor(() => {
          expect(view.result.current.metricsError).toBe(
            mode === "refused" ? "RAW_METRICS_DENIAL" : "ORIGINAL_TRANSPORT_UNAVAILABLE"
          );
          expect(view.result.current.healthError).toBe(
            mode === "refused" ? "RAW_HEALTH_DENIAL" : "ORIGINAL_TRANSPORT_UNAVAILABLE"
          );
        });
        expect(view.result.current.metrics).toEqual(DEPLOYMENT_METRICS);
        expect(view.result.current.apps).toEqual(APP_HEALTH);
        expect(view.result.current.list.state.q).toBe("storefront");
        view.setMode("ok");
        act(() => {
          view.result.current.onRetryMetrics();
          view.result.current.onRetryHealth();
        });
        await waitFor(() => {
          expect(view.result.current.metricsError).toBeNull();
          expect(view.result.current.healthError).toBeNull();
        });
        expect(view.requests).toHaveLength(6);
      } finally {
        view.stop();
      }
    }
  );
});
