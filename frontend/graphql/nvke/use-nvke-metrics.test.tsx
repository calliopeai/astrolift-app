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
import { METRICS_AUTHORITY } from "@/components/screens/nvke/NvkeMetricsPanel.stories";
import type { NvkeMetricsValues } from "@/components/screens/nvke/NvkeMetricsPanel";
import { useNvkeMetrics } from "./use-nvke-metrics";
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
const values: NvkeMetricsValues = {
  endpoint: METRICS_AUTHORITY.endpoint,
  allowHttp: false,
  timeoutSeconds: 30,
  credentialMode: "replace",
  token: "PRIVATE_METRICS_TOKEN",
  caPem: "",
  clientCertificatePem: "",
  clientKeyPem: "",
};
it.each([
  { endpoint: "https://replacement.example.test/metrics" },
  { allowHttp: true },
  { timeoutSeconds: 60 },
  { hasCredentials: false },
])("refuses verification of a changed metrics target %j", async (change) => {
  const h = harness();
  const { result } = renderHook(() => useNvkeMetrics(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply({ astroliftNvkeMetricsAuthority: METRICS_AUTHORITY }));
  let submitted!: Promise<boolean>;
  act(() => {
    submitted = result.current!.onAction("verify");
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    h.calls[1].reply({
      verifyNvkeMetricsAuthority: {
        ok: true,
        errors: [],
        data: { ...METRICS_AUTHORITY, ...change },
      },
    });
    expect(await submitted).toBe(false);
  });
  expect(result.current?.error).toBe(messages.nvke.configurationFailed);
  h.client.stop();
});
it("refuses stale metrics write callbacks after administrator access is withdrawn", async () => {
  const h = harness(),
    a = args();
  const { result, rerender } = renderHook(
    ({ admin }) =>
      useNvkeMetrics({ ...a, context: { ...a.context, access: { ...a.context.access, admin } } }),
    { wrapper: h.wrapper, initialProps: { admin: true } }
  );
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply({ astroliftNvkeMetricsAuthority: METRICS_AUTHORITY }));
  const old = result.current!;
  rerender({ admin: false });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    expect(await old.onConfigure(values)).toBe(false);
    expect(await old.onAction("disable")).toBe(false);
  });
  expect(h.calls.some((call) => call.request.operationName?.startsWith("Configure"))).toBe(false);
  expect(result.current?.canManage).toBe(false);
  h.client.stop();
});
it("does not read metrics authority when caller or site reads are unavailable", () => {
  const h = harness(),
    a = args();
  const { result } = renderHook(
    () =>
      useNvkeMetrics({
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
  const { result } = renderHook(() => useNvkeMetrics(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply({ astroliftNvkeMetricsAuthority: METRICS_AUTHORITY }));
  let submitted: Promise<boolean>;
  act(() => {
    submitted = result.current!.onConfigure(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  expect(h.calls[1].request.variables.input).toMatchObject({
    siteId: SITE.id,
    expectedSiteVersion: SITE.version,
    expectedVersion: METRICS_AUTHORITY.version,
    expectedActorId: 41,
    credentials: { token: "PRIVATE_METRICS_TOKEN" },
  });
  expect(h.calls[1].request.getContext()).toMatchObject({
    redactDiagnostics: true,
    queryDeduplication: false,
    expectedNvkeOrganization: SITE.id,
  });
  await act(async () => {
    h.calls[1].reply({
      configureNvkeMetricsAuthority: {
        ok: true,
        errors: [],
        data: {
          ...METRICS_AUTHORITY,
          version: METRICS_AUTHORITY.version + 1,
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
  const { result } = renderHook(() => useNvkeMetrics(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply({ astroliftNvkeMetricsAuthority: METRICS_AUTHORITY }));
  let submitted: Promise<boolean>;
  act(() => {
    submitted = result.current!.onAction("disable");
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    h.calls[1].reply({
      setNvkeMetricsAuthorityEnabled: { ok: false, errors: [{ code: "PRIVATE_SERVER_ERROR" }] },
    });
    expect(await submitted!).toBe(false);
  });
  expect(result.current?.error).toBe(messages.nvke.configurationFailed);
  await act(async () => expect(await result.current!.onAction("disable")).toBe(false));
  expect(h.calls).toHaveLength(2);
  act(() => result.current!.onRefresh());
  await waitFor(() => expect(h.calls).toHaveLength(3));
  expect(result.current?.canManage).toBe(false);
  await act(async () => h.calls[2].reply({ astroliftNvkeMetricsAuthority: METRICS_AUTHORITY }));
  expect(result.current?.canManage).toBe(true);
  h.client.stop();
});
it("ignores old writes and stale callbacks across A to B to A caller visits", async () => {
  const h = harness();
  const { result, rerender } = renderHook(({ actor }) => useNvkeMetrics(args(actor)), {
    wrapper: h.wrapper,
    initialProps: { actor: 41 },
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => h.calls[0].reply({ astroliftNvkeMetricsAuthority: METRICS_AUTHORITY }));
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
      setNvkeMetricsAuthorityEnabled: {
        ok: true,
        data: {
          ...METRICS_AUTHORITY,
          version: 3,
          siteVersion: 8,
          isEnabled: false,
          verifiedAt: null,
          verificationCode: "",
        },
      },
    });
    expect(await submitted!).toBe(false);
    h.calls[2].reply({ astroliftNvkeMetricsAuthority: METRICS_AUTHORITY });
  });
  expect(result.current?.authority).toBeNull();
  await act(async () => h.calls[3].reply({ astroliftNvkeMetricsAuthority: METRICS_AUTHORITY }));
  await act(async () => expect(await old.onAction("remove")).toBe(false));
  expect(h.calls).toHaveLength(4);
  expect(result.current?.authority?.id).toBe(METRICS_AUTHORITY.id);
  h.client.stop();
});
