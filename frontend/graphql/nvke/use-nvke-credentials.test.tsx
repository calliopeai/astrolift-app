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
import { SITE } from "@/components/screens/nvke/fixtures";
import { OPERATION } from "@/components/screens/nvke/fixtures";
import { CREDENTIAL_PROPS } from "@/components/screens/nvke/NvkeCredentialPanel.stories";
import { useNvkeCredentials } from "./use-nvke-credentials";
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
      operationKey: "metrics.credentials",
      status: "SUCCEEDED" as const,
      version: 12,
    },
    enabled: true,
  };
}
const observation = CREDENTIAL_PROPS.observation!;
const summary = (changes: object = {}) => ({
  astroliftNvkeCredentialObservation: {
    ok: true,
    errors: [],
    data: { ...observation, ...changes },
  },
});
const page = (field: string, changes: object = {}) => ({
  [field]: {
    ok: true,
    errors: [],
    data: {
      siteId: SITE.id,
      operationId: OPERATION.id,
      operationVersion: 12,
      scrapedAt: observation.scrapedAt,
      totalCount: 2,
      nextCursor: "opaque-cursor",
      items: field.includes("Certificates")
        ? CREDENTIAL_PROPS.certificates.rows
        : CREDENTIAL_PROPS.samples.rows,
      ...changes,
    },
  },
});
const fieldFor = (name = "") =>
  name.includes("Certificates")
    ? "astroliftNvkeCredentialCertificatesPage"
    : "astroliftNvkeCredentialMetricsPage";

it("reads historical observations and exact paged values with READ access and no cache", async () => {
  const h = harness();
  const { result } = renderHook(() => useNvkeCredentials(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  expect(h.calls[0].request.variables).toEqual({
    siteId: SITE.id,
    operationId: OPERATION.id,
    expectedActorId: 41,
    expectedVersion: 12,
  });
  await act(async () => h.calls[0].reply(summary()));
  await waitFor(() => expect(h.calls).toHaveLength(3));
  await act(async () => {
    for (const call of h.calls.slice(1)) call.reply(page(fieldFor(call.request.operationName)));
  });
  await waitFor(() => expect(result.current?.samples.rows).toHaveLength(1));
  expect(result.current?.samples.rows[0].value).toBe("9007199254740993");
  expect(result.current?.certificates.rows[0].latestInspectionAvailable).toBe(false);
  expect(result.current?.observation?.renewalAutomationEnabled).toBeNull();
  expect(h.cache.extract()).toEqual({});
  for (const call of h.calls)
    expect(call.request.getContext()).toMatchObject({
      redactDiagnostics: true,
      queryDeduplication: false,
      expectedNvkeOrganization: SITE.id,
    });
  act(() => result.current!.samples.next());
  await waitFor(() => expect(h.calls).toHaveLength(4));
  expect(h.calls[3].request.variables.after).toBe("opaque-cursor");
  await act(async () =>
    h.calls[3].reply(page("astroliftNvkeCredentialMetricsPage", { nextCursor: null }))
  );
  act(() => result.current!.samples.setSearch("cp-2"));
  await waitFor(() => expect(h.calls).toHaveLength(5));
  expect(h.calls[4].request.variables).toMatchObject({
    search: "cp-2",
    expectedVersion: 12,
    expectedActorId: 41,
  });
  expect(h.calls[4].request.variables.after ?? null).toBeNull();
  h.client.stop();
});

it.each([
  { operationVersion: 13 },
  { siteId: OPERATION.id },
  { clusterReported: undefined },
  { scrapedAt: "unreported" },
  { collectionSucceeded: "0" },
  { sourceVersion: "1" },
])("refuses unconfirmed historical headers %j before any page read", async (changes) => {
  const h = harness();
  const { result } = renderHook(() => useNvkeCredentials(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply(summary(changes)));
  expect(result.current?.observation).toBeNull();
  expect(result.current?.error).toBe(messages.nvke.readFailed);
  expect(h.calls).toHaveLength(1);
  h.client.stop();
});

it.each([
  { operationVersion: 13 },
  { scrapedAt: "2026-11-01T00:00:00Z" },
  { siteId: OPERATION.id },
  { items: null },
  { items: [{ key: "inexact", metric: "serving_csr_count", value: 9007199254740992 }] },
])("refuses mismatched or incomplete metric pages %j", async (changes) => {
  const h = harness();
  const { result } = renderHook(() => useNvkeCredentials(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply(summary()));
  await waitFor(() => expect(h.calls).toHaveLength(3));
  await act(async () => {
    for (const call of h.calls.slice(1))
      call.reply(page(fieldFor(call.request.operationName), changes));
  });
  await waitFor(() => expect(result.current?.samples.state).toBe("error"));
  expect(result.current?.samples.rows).toEqual([]);
  expect(result.current?.certificates.rows).toEqual([]);
  h.client.stop();
});

it("fences historical data across A to B to A and hides it on permission loss", async () => {
  const h = harness();
  const { result, rerender } = renderHook(
    ({ actor, read }) => {
      const a = args(actor);
      return useNvkeCredentials({
        ...a,
        context: { ...a.context, access: { ...a.context.access, read } },
      });
    },
    { wrapper: h.wrapper, initialProps: { actor: 41, read: true } }
  );
  await waitFor(() => expect(h.calls).toHaveLength(1));
  rerender({ actor: 42, read: true });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  rerender({ actor: 41, read: true });
  await waitFor(() => expect(h.calls).toHaveLength(3));
  await act(async () => {
    h.calls[0].reply(summary());
    h.calls[1].reply(summary());
  });
  expect(result.current?.observation).toBeNull();
  expect(result.current?.loading).toBe(true);
  expect(h.calls).toHaveLength(3);
  await act(async () => h.calls[2].reply(summary()));
  await waitFor(() => expect(h.calls).toHaveLength(5));
  rerender({ actor: 41, read: false });
  await act(async () => {
    for (const call of h.calls.slice(3)) call.reply(page(fieldFor(call.request.operationName)));
  });
  expect(result.current).toBeNull();
  expect(h.cache.extract()).toEqual({});
  h.client.stop();
});
