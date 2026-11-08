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
import { SITE, OPERATION } from "@/components/screens/nvke/fixtures";
import { PROVIDER_PROPS } from "@/components/screens/nvke/NvkeProvidersPanel.stories";
import { useNvkeProviders } from "./use-nvke-providers";
function harness() {
  const calls: { request: Operation; reply: (data: Record<string, unknown>) => void }[] = [],
    cache = new InMemoryCache();
  const client = new ApolloClient({
    cache,
    link: new ApolloLink(
      (request) =>
        new Observable((observer) => {
          calls.push({
            request,
            reply: (data) => {
              observer.next({ data });
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
  return { calls, cache, client, wrapper };
}
const observation = PROVIDER_PROPS.observation!;
function args(actor = 41, admin = true) {
  const scope = {
    server: "https://astro.example.test/app/gql/config/",
    organizationId: SITE.id,
    actorId: actor,
  };
  return {
    context: {
      scope,
      scopeKey: JSON.stringify(scope),
      access: { read: true, admin, manage: false, loading: false },
      loading: false,
      failed: false,
    },
    scopeKey: JSON.stringify(scope),
    siteId: SITE.id,
    operation: {
      ...OPERATION,
      operationKey: "infrastructure.providers",
      status: "SUCCEEDED" as const,
      version: 12,
      providerSource: {
        connectionId: observation.connectionId,
        connectionVersion: observation.connectionVersion,
        siteVersion: observation.sourceSiteVersion,
        upstreamRevision: observation.upstreamRevision,
      },
    },
    enabled: true,
  };
}
const summary = (changes: object = {}) => ({
  astroliftNvkeProviderObservation: { ok: true, errors: [], data: { ...observation, ...changes } },
});
const page = (changes: object = {}) => ({
  astroliftNvkeProviderIdentitiesPage: {
    ok: true,
    errors: [],
    data: {
      siteId: SITE.id,
      operationId: OPERATION.id,
      operationVersion: 12,
      observedAt: observation.observedAt,
      provider: "nico",
      items: PROVIDER_PROPS.identities.rows,
      totalCount: 31,
      nextCursor: "opaque",
      ...changes,
    },
  },
});
it("binds saved source and exact timestamps, then searches and pages the full retained identity list without caching", async () => {
  const h = harness();
  const { result } = renderHook(() => useNvkeProviders(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  expect(h.calls[0].request.variables).toEqual({
    siteId: SITE.id,
    operationId: OPERATION.id,
    expectedActorId: 41,
    expectedVersion: 12,
  });
  await act(async () => h.calls[0].reply(summary()));
  await waitFor(() => expect(h.calls).toHaveLength(2));
  expect(h.calls[1].request.variables.search).toBe("");
  await act(async () => h.calls[1].reply(page()));
  await waitFor(() => expect(result.current?.identities.rows).toHaveLength(1));
  expect(result.current?.observation?.providers[0].credentials?.identityGeneration).toBe(
    "9007199254740993"
  );
  expect(result.current?.observation?.observedAt).toBe(observation.observedAt);
  act(() => result.current!.identities.next());
  await waitFor(() => expect(h.calls).toHaveLength(3));
  expect(h.calls[2].request.variables.after).toBe("opaque");
  await act(async () => h.calls[2].reply(page({ nextCursor: null })));
  act(() => result.current!.identities.setSearch("additional-030"));
  await waitFor(() => expect(h.calls).toHaveLength(4));
  expect(h.calls[3].request.variables).toMatchObject({
    search: "additional-030",
    provider: "nico",
  });
  expect(h.calls[3].request.variables.after ?? null).toBeNull();
  await act(async () => h.calls[3].reply(page({ totalCount: 1, nextCursor: null })));
  act(() => result.current!.identities.setSearch(""));
  await waitFor(() => expect(h.calls).toHaveLength(5));
  expect(h.calls[4].request.variables.search).toBe("");
  expect(h.calls[4].request.variables.after ?? null).toBeNull();
  expect(h.cache.extract()).toEqual({});
  for (const call of h.calls)
    expect(call.request.getContext()).toMatchObject({
      redactDiagnostics: true,
      queryDeduplication: false,
      expectedNvkeOrganization: SITE.id,
    });
  h.client.stop();
});
it.each([
  { providers: [] },
  { providers: [observation.providers[0]] },
  { providers: null },
  { operationVersion: 13 },
  { connectionId: OPERATION.id },
  { connectionVersion: 4 },
  { sourceSiteVersion: 8 },
  { observedAt: "unreported" },
  { complete: false },
  { upstreamRevision: "0".repeat(40) },
  ...[9007199254740992, "invalid", "9223372036854775808", true].map((value) => ({
    providers: [{ ...observation.providers[0], readyReplicas: value }, observation.providers[1]],
  })),
  {
    providers: [
      {
        ...observation.providers[0],
        credentials: { ...observation.providers[0].credentials, observedGeneration: "1" },
      },
      observation.providers[1],
    ],
  },
])(
  "rejects mismatched or malformed snapshots without requesting identity pages %j",
  async (changes) => {
    const h = harness();
    const { result } = renderHook(() => useNvkeProviders(args()), { wrapper: h.wrapper });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => h.calls[0].reply(summary(changes)));
    expect(result.current?.observation).toBeNull();
    expect(result.current?.error).toBe(messages.nvke.readFailed);
    expect(h.calls).toHaveLength(1);
    h.client.stop();
  }
);
it.each([
  { provider: "launchlayer" },
  { operationVersion: 13 },
  { observedAt: "2026-11-08T12:00:00.123456788Z" },
  { totalCount: 30 },
  { items: [{ key: "wrong-key", namespace: "capnico-system", name: "extra" }] },
  {
    items: [
      { key: "capnico-system/primary-nico", namespace: "capnico-system", name: "primary-nico" },
    ],
  },
  { items: [PROVIDER_PROPS.identities.rows[0], PROVIDER_PROPS.identities.rows[0]] },
  { items: null },
  { nextCursor: "" },
  { items: [], nextCursor: "opaque" },
])("rejects inconsistent identity pages %j", async (changes) => {
  const h = harness();
  const { result } = renderHook(() => useNvkeProviders(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply(summary()));
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => h.calls[1].reply(page(changes)));
  await waitFor(() => expect(result.current?.identities.state).toBe("error"));
  expect(result.current?.identities.rows).toEqual([]);
  h.client.stop();
});
it("discards old provider pages and resets paging when provider selection changes", async () => {
  const h = harness();
  const { result } = renderHook(() => useNvkeProviders(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply(summary()));
  await waitFor(() => expect(h.calls).toHaveLength(2));
  act(() => result.current!.onProviderChange("launchlayer"));
  await waitFor(() => expect(h.calls).toHaveLength(3));
  expect(h.calls[2].request.variables).toMatchObject({ provider: "launchlayer" });
  await act(async () => h.calls[1].reply(page()));
  expect(result.current?.identities.rows).toEqual([]);
  await act(async () =>
    h.calls[2].reply(page({ provider: "launchlayer", items: [], totalCount: 0, nextCursor: null }))
  );
  await waitFor(() => expect(result.current?.identities.state).toBe("empty"));
  h.client.stop();
});
it("requires both capabilities and fences responses across actor and permission changes", async () => {
  const h = harness();
  const { result, rerender } = renderHook(
    ({ actor, admin }) => useNvkeProviders(args(actor, admin)),
    { wrapper: h.wrapper, initialProps: { actor: 41, admin: false } }
  );
  expect(result.current).toBeNull();
  expect(h.calls).toHaveLength(0);
  rerender({ actor: 41, admin: true });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  rerender({ actor: 42, admin: true });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  rerender({ actor: 41, admin: true });
  await waitFor(() => expect(h.calls).toHaveLength(3));
  await act(async () => {
    h.calls[0].reply(summary());
    h.calls[1].reply(summary());
  });
  expect(result.current?.observation).toBeNull();
  expect(result.current?.loading).toBe(true);
  await act(async () => h.calls[2].reply(summary()));
  await waitFor(() => expect(h.calls).toHaveLength(4));
  rerender({ actor: 41, admin: false });
  await act(async () => h.calls[3].reply(page()));
  expect(result.current).toBeNull();
  expect(h.cache.extract()).toEqual({});
  h.client.stop();
});
