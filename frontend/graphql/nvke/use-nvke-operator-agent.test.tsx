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
import { OPERATOR_AGENT_AUTHORITY } from "@/components/screens/nvke/NvkeOperatorAgentPanel.stories";
import type { NvkeOperatorAgentValues } from "@/components/screens/nvke/NvkeOperatorAgentPanel";
import { useNvkeOperatorAgent } from "./use-nvke-operator-agent";
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
const values: NvkeOperatorAgentValues = {
  endpoint: OPERATOR_AGENT_AUTHORITY.endpoint,
  instanceId: OPERATOR_AGENT_AUTHORITY.instanceId,
  bundleManifestSha256: OPERATOR_AGENT_AUTHORITY.bundleManifestSha256,
  timeoutSeconds: 30,
  credentialMode: "replace",
  caPem: "",
  clientCertificatePem: "PRIVATE_AGENT_CERT",
  clientKeyPem: "PRIVATE_AGENT_KEY",
};
it("does not read native authority when caller or site reads are unavailable", () => {
  const h = harness(),
    a = args();
  const { result } = renderHook(
    () =>
      useNvkeOperatorAgent({
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
  const { result } = renderHook(() => useNvkeOperatorAgent(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () =>
    h.calls[0].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY })
  );
  let submitted: Promise<boolean>;
  act(() => {
    submitted = result.current!.onConfigure(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  expect(h.calls[1].request.variables.input).toMatchObject({
    siteId: SITE.id,
    expectedSiteVersion: SITE.version,
    expectedVersion: OPERATOR_AGENT_AUTHORITY.version,
    expectedActorId: 41,
    credentials: { clientKeyPem: "PRIVATE_AGENT_KEY" },
  });
  expect(h.calls[1].request.getContext()).toMatchObject({
    redactDiagnostics: true,
    queryDeduplication: false,
    fetchOptions: { redirect: "error", cache: "no-store" },
    expectedNvkeOrganization: SITE.id,
  });
  await act(async () => {
    h.calls[1].reply({
      configureNvkeOperatorAuthority: {
        ok: true,
        errors: [],
        data: {
          ...OPERATOR_AGENT_AUTHORITY,
          version: OPERATOR_AGENT_AUTHORITY.version + 1,
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
  const { result } = renderHook(() => useNvkeOperatorAgent(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () =>
    h.calls[0].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY })
  );
  const originalCallbacks = result.current!;
  let submitted: Promise<boolean>;
  act(() => {
    submitted = result.current!.onAction("disable");
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    h.calls[1].reply({
      setNvkeOperatorAuthorityEnabled: { ok: false, errors: [{ code: "PRIVATE_SERVER_ERROR" }] },
    });
    expect(await submitted!).toBe(false);
  });
  expect(result.current?.error).toBe(messages.nvke.configurationFailed);
  await act(async () => expect(await originalCallbacks.onAction("disable")).toBe(false));
  await act(async () => expect(await result.current!.onAction("disable")).toBe(false));
  expect(h.calls).toHaveLength(2);
  act(() => result.current!.onRefresh());
  await waitFor(() => expect(h.calls).toHaveLength(3));
  expect(result.current?.canManage).toBe(false);
  await act(async () =>
    h.calls[2].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY })
  );
  expect(result.current?.canManage).toBe(true);
  h.client.stop();
});
it("ignores old writes and stale callbacks across A to B to A caller visits", async () => {
  const h = harness();
  const { result, rerender } = renderHook(({ actor }) => useNvkeOperatorAgent(args(actor)), {
    wrapper: h.wrapper,
    initialProps: { actor: 41 },
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () =>
    h.calls[0].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY })
  );
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
      setNvkeOperatorAuthorityEnabled: {
        ok: true,
        data: {
          ...OPERATOR_AGENT_AUTHORITY,
          version: 3,
          siteVersion: 8,
          isEnabled: false,
          verifiedAt: null,
          verificationCode: "",
        },
      },
    });
    expect(await submitted!).toBe(false);
    h.calls[2].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY });
  });
  expect(result.current?.authority).toBeNull();
  await act(async () =>
    h.calls[3].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY })
  );
  await act(async () => expect(await old.onAction("remove")).toBe(false));
  expect(h.calls).toHaveLength(4);
  expect(result.current?.authority?.id).toBe(OPERATOR_AGENT_AUTHORITY.id);
  h.client.stop();
});

it("refuses a saved write callback after admin revocation and regrant", async () => {
  const h = harness();
  const { result, rerender } = renderHook(
    ({ admin }) => {
      const a = args();
      return useNvkeOperatorAgent({
        ...a,
        context: { ...a.context, access: { ...a.context.access, admin } },
      });
    },
    { wrapper: h.wrapper, initialProps: { admin: true } }
  );
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () =>
    h.calls[0].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY })
  );
  const old = result.current!;
  rerender({ admin: false });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => expect(await old.onConfigure(values)).toBe(false));
  await act(async () =>
    h.calls[1].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY })
  );
  expect(result.current?.canManage).toBe(false);
  rerender({ admin: true });
  await waitFor(() => expect(h.calls).toHaveLength(3));
  await act(async () =>
    h.calls[2].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY })
  );
  await act(async () => expect(await old.onAction("remove")).toBe(false));
  expect(h.calls).toHaveLength(3);
  h.client.stop();
});
it.each([
  { endpoint: "https://user:PRIVATE@agent.test" },
  { instanceId: "PRIVATE_INVALID_INSTANCE" },
  { bundleManifestSha256: "PRIVATE_INVALID_BUNDLE" },
  { timeoutSeconds: 0 },
  { verifiedAt: "PRIVATE_INVALID_DATE" },
  { hasCredentials: false },
  { version: 0 },
])("refuses malformed authority metadata without exposing the response", async (changed) => {
  const h = harness(),
    a = args();
  const { result } = renderHook(() => useNvkeOperatorAgent(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () =>
    h.calls[0].reply({
      astroliftNvkeOperatorAuthority: { ...OPERATOR_AGENT_AUTHORITY, ...changed },
    })
  );
  expect(result.current?.authority).toBeNull();
  expect(result.current?.canManage).toBe(false);
  expect(result.current?.error).toBe(messages.nvke.readFailed);
  expect(h.cache.extract()).toEqual({});
  h.client.stop();
});
it.each([
  { endpoint: "https://other-agent.example.test" },
  { instanceId: "123e4567-e89b-12d3-a456-426614174099" },
  { bundleManifestSha256: "c".repeat(64) },
])("does not accept verification of a changed original agent identity", async (changed) => {
  const h = harness(),
    a = args();
  const { result } = renderHook(() => useNvkeOperatorAgent(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () =>
    h.calls[0].reply({ astroliftNvkeOperatorAuthority: OPERATOR_AGENT_AUTHORITY })
  );
  let submitted: Promise<boolean>;
  act(() => {
    submitted = result.current!.onAction("verify");
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    h.calls[1].reply({
      verifyNvkeOperatorAuthority: { ok: true, data: { ...OPERATOR_AGENT_AUTHORITY, ...changed } },
    });
    expect(await submitted!).toBe(false);
  });
  expect(result.current?.error).toBe(messages.nvke.configurationFailed);
  h.client.stop();
});

it("does not fetch agent metadata while permission resolution is pending", () => {
  const h = harness(),
    a = args();
  const { result } = renderHook(
    () =>
      useNvkeOperatorAgent({
        ...a,
        context: { ...a.context, access: { ...a.context.access, loading: true } },
      }),
    { wrapper: h.wrapper }
  );
  expect(result.current).toBeNull();
  expect(h.calls).toHaveLength(0);
  h.client.stop();
});

it("drops unrequested private fields from authority props without caching the response", async () => {
  const h = harness(),
    a = args();
  const { result } = renderHook(() => useNvkeOperatorAgent(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () =>
    h.calls[0].reply({
      astroliftNvkeOperatorAuthority: {
        ...OPERATOR_AGENT_AUTHORITY,
        clientKeyPem: "PRIVATE_SERVER_ECHO",
      },
    })
  );
  expect(result.current?.authority?.id).toBe(OPERATOR_AGENT_AUTHORITY.id);
  expect(JSON.stringify(result.current?.authority)).not.toContain("PRIVATE_SERVER_ECHO");
  expect(h.cache.extract()).toEqual({});
  h.client.stop();
});
