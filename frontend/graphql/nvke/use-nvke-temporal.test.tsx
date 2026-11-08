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
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { SITE } from "@/components/screens/nvke/fixtures";
import { TEMPORAL_AUTHORITY } from "@/components/screens/nvke/NvkeTemporalPanel.stories";
import type { NvkeTemporalValues } from "@/components/screens/nvke/NvkeTemporalPanel";
import { useNvkeTemporal } from "./use-nvke-temporal";
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
      access: { read: true, admin: true, manage: true, loading: false },
      loading: false,
      failed: false,
    },
    scopeKey: JSON.stringify(scope),
    site: SITE,
    enabled: true,
    onSiteRefresh: vi.fn(),
  };
}
const values: NvkeTemporalValues = {
  address: TEMPORAL_AUTHORITY.address,
  namespace: TEMPORAL_AUTHORITY.namespace,
  taskQueue: TEMPORAL_AUTHORITY.taskQueue,
  serverName: "",
  useTls: true,
  timeoutSeconds: 30,
  credentialMode: "replace",
  apiKey: "PRIVATE_NATIVE_KEY",
  caPem: "",
  clientCertificatePem: "",
  clientKeyPem: "",
};
it("does not read native authority when caller or site reads are unavailable", () => {
  const h = harness(),
    a = args();
  const { result } = renderHook(
    () =>
      useNvkeTemporal({
        ...a,
        context: { ...a.context, access: { ...a.context.access, read: false } },
      }),
    { wrapper: h.wrapper }
  );
  expect(result.current).toBeNull();
  expect(h.calls).toHaveLength(0);
  h.client.stop();
});
it("fences private native writes to actor/site/authority versions without caching credentials", async () => {
  const h = harness(),
    a = args();
  const { result } = renderHook(() => useNvkeTemporal(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply({ astroliftNvkeTemporalAuthority: TEMPORAL_AUTHORITY }));
  let submitted: Promise<boolean>;
  act(() => {
    submitted = result.current!.onConfigure(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  expect(h.calls[1].request.variables.input).toMatchObject({
    siteId: SITE.id,
    expectedSiteVersion: SITE.version,
    expectedVersion: TEMPORAL_AUTHORITY.version,
    expectedActorId: 41,
    credentials: { apiKey: "PRIVATE_NATIVE_KEY" },
  });
  expect(h.calls[1].request.getContext()).toMatchObject({
    redactDiagnostics: true,
    queryDeduplication: false,
    expectedNvkeOrganization: SITE.id,
  });
  await act(async () => {
    h.calls[1].reply({
      configureNvkeTemporalAuthority: {
        ok: true,
        errors: [],
        data: {
          ...TEMPORAL_AUTHORITY,
          version: TEMPORAL_AUTHORITY.version + 1,
          siteVersion: SITE.version + 1,
          verifiedAt: null,
          verificationCode: "",
        },
      },
    });
    expect(await submitted!).toBe(true);
  });
  expect(h.cache.extract()).toEqual({});
  expect(a.onSiteRefresh).toHaveBeenCalledOnce();
  h.client.stop();
});
it("an unconfirmed write blocks another write until fresh metadata is read", async () => {
  const h = harness(),
    a = args();
  const { result } = renderHook(() => useNvkeTemporal(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply({ astroliftNvkeTemporalAuthority: TEMPORAL_AUTHORITY }));
  let submitted: Promise<boolean>;
  act(() => {
    submitted = result.current!.onAction("disable");
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    h.calls[1].reply({
      setNvkeTemporalAuthorityEnabled: { ok: false, errors: [{ code: "PRIVATE_SERVER_ERROR" }] },
    });
    expect(await submitted!).toBe(false);
  });
  expect(result.current?.error).toBe(messages.nvke.configurationFailed);
  await act(async () => expect(await result.current!.onAction("disable")).toBe(false));
  expect(h.calls).toHaveLength(2);
  act(() => result.current!.onRefresh());
  await waitFor(() => expect(h.calls).toHaveLength(3));
  expect(result.current?.canManage).toBe(false);
  await act(async () => h.calls[2].reply({ astroliftNvkeTemporalAuthority: TEMPORAL_AUTHORITY }));
  expect(result.current?.canManage).toBe(true);
  h.client.stop();
});
it("ignores old writes and stale callbacks across A to B to A caller visits", async () => {
  const h = harness();
  const { result, rerender } = renderHook(({ actor }) => useNvkeTemporal(args(actor)), {
    wrapper: h.wrapper,
    initialProps: { actor: 41 },
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply({ astroliftNvkeTemporalAuthority: TEMPORAL_AUTHORITY }));
  const old = result.current!;
  let submitted: Promise<boolean>;
  act(() => {
    submitted = old.onAction("disable");
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  rerender({ actor: 42 });
  await waitFor(() => expect(h.calls).toHaveLength(3));
  rerender({ actor: 41 });
  await waitFor(() => expect(h.calls).toHaveLength(4));
  await act(async () => {
    h.calls[1].reply({
      setNvkeTemporalAuthorityEnabled: {
        ok: true,
        data: {
          ...TEMPORAL_AUTHORITY,
          version: 3,
          siteVersion: 8,
          isEnabled: false,
          verifiedAt: null,
          verificationCode: "",
        },
      },
    });
    expect(await submitted!).toBe(false);
    h.calls[2].reply({ astroliftNvkeTemporalAuthority: TEMPORAL_AUTHORITY });
  });
  expect(result.current?.authority).toBeNull();
  await act(async () => h.calls[3].reply({ astroliftNvkeTemporalAuthority: TEMPORAL_AUTHORITY }));
  await act(async () => expect(await old.onAction("remove")).toBe(false));
  expect(h.calls).toHaveLength(4);
  expect(result.current?.authority?.id).toBe(TEMPORAL_AUTHORITY.id);
  h.client.stop();
});
