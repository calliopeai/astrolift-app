import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CLUSTER } from "../settings/fixtures";
import { rebind } from "./fixtures";
import { useClusterDetail } from "./use-cluster-detail";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const messages = JSON.parse(readFileSync("messages/en.json", "utf8"));

/**
 * The overview polls while a re-point's role move is in flight (#2472), as it
 * does while the management workflow runs, so its progress moves on screen.
 */
function harness(state: string | null) {
  const reads: number[] = [];
  const cluster = {
    ...CLUSTER,
    __typename: "AstroliftTenantCluster",
    lifecycle: "managed",
    lastBootstrapRun: null,
    workloadIdentityRebind: state
      ? {
          ...rebind({ state }),
          __typename: "AstroliftWorkloadIdentityRebind",
          failures: [],
        }
      : null,
  };
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://cluster-detail.test.invalid/graphql/",
      fetch: async (_url, options) => {
        const request = JSON.parse(String(options?.body));
        if (request.operationName !== "GetCluster")
          throw new Error(`Unexpected operation ${request.operationName}`);
        reads.push(Date.now());
        return Response.json({ data: { astroliftCluster: cluster } });
      },
    }),
  });
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider locale="en" messages={messages}>
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return { reads, Wrapper, slug: cluster.slug };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("useClusterDetail while a re-point moves roles", () => {
  it.each(["pending", "running"])("polls while the move is %s", async (state) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { reads, Wrapper, slug } = harness(state);
    const hook = renderHook(() => useClusterDetail(slug), { wrapper: Wrapper });
    await waitFor(() =>
      expect(hook.result.current.cluster?.workloadIdentityRebind?.state).toBe(state)
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_000);
    });

    expect(reads.length).toBeGreaterThanOrEqual(3);
    hook.unmount();
  });

  it.each(["succeeded", "failed", null])("does not poll once the move is %s", async (state) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { reads, Wrapper, slug } = harness(state);
    const hook = renderHook(() => useClusterDetail(slug), { wrapper: Wrapper });
    await waitFor(() => expect(hook.result.current.cluster?.slug).toBe(slug));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_000);
    });

    expect(reads).toHaveLength(1);
    hook.unmount();
  });
});
