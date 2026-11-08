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
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { SITE, OPERATION } from "@/components/screens/nvke/fixtures";
import { OPERATOR_AGENT_AUTHORITY } from "@/components/screens/nvke/NvkeOperatorAgentPanel.stories";
import fixture from "@/components/screens/nvke/operator-contract.fixture.json";
import { readRecovery, type NvkeRecoveryRecord } from "@/lib/nvke/recovery";
import { useNvkeOperatorWorkbench } from "./use-nvke-operator-workbench";
const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
function harness() {
  const calls: {
      request: Operation;
      reply: (data: Record<string, unknown>) => void;
      fail: () => void;
    }[] = [],
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
            fail: () => observer.error(new Error("PRIVATE_TRANSPORT_DETAILS")),
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
    scopeKey: "parent-visit",
    site: SITE,
    authority: OPERATOR_AGENT_AUTHORITY as typeof OPERATOR_AGENT_AUTHORITY | null,
    enabled: true,
    onRefresh: vi.fn(),
    onJournalChange: vi.fn(),
  };
}
const values = {
  operationKey: "tooling.version",
  requestText: JSON.stringify({
    format: 1,
    siteId: SITE.id,
    revision: fixture.revision,
    command: "version",
    positionals: [],
    options: {},
    files: { "private-file": "PRIVATE_NATIVE_TEXT" },
  }),
  timeoutSeconds: 600,
  acknowledgeInfrastructureChange: false,
};
const row = (requestId: string) => ({
  ...OPERATION,
  siteId: SITE.id,
  requestId,
  operationKey: "tooling.version",
  isInventory: false,
  resourceSource: null,
  providerSource: null,
});
async function ready(
  h: ReturnType<typeof harness>,
  r: { result: { readonly current: ReturnType<typeof useNvkeOperatorWorkbench> } }
) {
  await waitFor(() => expect(h.calls).toHaveLength(1));
  act(() => h.calls[0].reply({ astroliftNvkeOperatorContract: fixture }));
  await waitFor(() => expect(r.result.current?.canManage).toBe(true));
}
beforeEach(() => {
  window.localStorage.clear();
  push.mockClear();
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: object, run: () => Promise<unknown>) => run(),
    },
  });
});
afterEach(() => vi.unstubAllGlobals());

it("binds original native text to caller/site/agent versions and bypasses caches and redirects", async () => {
  const h = harness(),
    a = args(),
    r = renderHook(() => useNvkeOperatorWorkbench(a), { wrapper: h.wrapper });
  await ready(h, r);
  let promise: Promise<boolean>;
  act(() => {
    promise = r.result.current!.onRequest(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  const input = h.calls[1].request.variables.input,
    saved = readRecovery(window.localStorage, a.context.scope, SITE.id);
  expect(input).toEqual({
    siteId: SITE.id,
    expectedActorId: 41,
    expectedSiteVersion: SITE.version,
    expectedAuthorityVersion: OPERATOR_AGENT_AUTHORITY.version,
    requestId: saved!.requestId,
    requestText: values.requestText,
    timeoutSeconds: 600,
    acknowledgeInfrastructureChange: false,
  });
  expect(h.calls[1].request.getContext()).toMatchObject({
    queryDeduplication: false,
    redactDiagnostics: true,
    expectedNvkeOrganization: SITE.id,
    fetchOptions: { redirect: "error", cache: "no-store" },
  });
  expect(JSON.stringify(saved)).not.toMatch(/PRIVATE_NATIVE_TEXT|private-file/);
  await act(async () => {
    h.calls[1].reply({
      requestNvkeOperatorJob: { ok: true, errors: [], data: row(input.requestId) },
    });
    expect(await promise!).toBe(true);
  });
  expect(push).toHaveBeenCalledWith(`/nvke/${SITE.id}/operations/${OPERATION.id}`);
  expect(readRecovery(window.localStorage, a.context.scope, SITE.id)).toBeNull();
  expect(JSON.stringify(h.cache.extract())).not.toMatch(
    /PRIVATE_NATIVE_TEXT|private-file|tooling.version/
  );
});

it.each(["actor", "organization", "permission", "loading", "parent", "site", "authority"])(
  "refuses saved callbacks after %s changes, including return to the original caller",
  async (change) => {
    const h = harness(),
      a = args(),
      r = renderHook((input) => useNvkeOperatorWorkbench(input), {
        initialProps: a,
        wrapper: h.wrapper,
      });
    await ready(h, r);
    const saved = r.result.current!.onRequest;
    const next = {
      ...a,
      context: { ...a.context, scope: { ...a.context.scope }, access: { ...a.context.access } },
    };
    if (change === "actor") next.context.scope.actorId = 42;
    if (change === "organization") next.context.scope.organizationId = OPERATION.id;
    if (change === "permission") next.context.access.admin = false;
    if (change === "loading") next.context.access.loading = true;
    if (change === "parent") next.scopeKey = "new-parent";
    if (change === "site") next.site = { ...SITE, version: SITE.version + 1 };
    if (change === "authority") next.authority = { ...OPERATOR_AGENT_AUTHORITY, version: 99 };
    r.rerender(next);
    r.rerender(a);
    expect(await saved(values)).toBe(false);
    expect(
      h.calls.filter((call) => call.request.operationName === "RequestNvkeOperatorJob")
    ).toHaveLength(0);
  }
);

it.each(["read", "access-loading", "scope-loading", "disabled"])(
  "does not read or request under %s",
  async (condition) => {
    const h = harness(),
      a = args();
    if (condition === "read") a.context.access.read = false;
    if (condition === "access-loading") a.context.access.loading = true;
    if (condition === "scope-loading") a.context.loading = true;
    if (condition === "disabled") a.enabled = false;
    const r = renderHook(() => useNvkeOperatorWorkbench(a), { wrapper: h.wrapper });
    expect(r.result.current).toBeNull();
    expect(h.calls).toHaveLength(0);
  }
);

it.each([
  null,
  { ...fixture, revision: "a".repeat(40) },
  { ...fixture, commands: [] },
  { ...fixture, commands: [fixture.commands[0], fixture.commands[0]] },
])("refuses malformed or mismatched contracts", async (contract) => {
  const h = harness(),
    r = renderHook(() => useNvkeOperatorWorkbench(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  act(() => h.calls[0].reply({ astroliftNvkeOperatorContract: contract }));
  await waitFor(() => expect(r.result.current?.error).toBe(messages.nvke.readFailed));
  expect(await r.result.current!.onRequest(values)).toBe(false);
  expect(h.calls).toHaveLength(1);
});

it("blocks retained request callbacks after uncertainty and reads the original receipt after explicit refresh", async () => {
  const h = harness(),
    a = args(),
    r = renderHook(() => useNvkeOperatorWorkbench(a), { wrapper: h.wrapper });
  await ready(h, r);
  const saved = r.result.current!.onRequest;
  let pending: Promise<boolean>;
  act(() => {
    pending = saved(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  const requestId = h.calls[1].request.variables.input.requestId;
  await act(async () => {
    h.calls[1].fail();
    expect(await pending!).toBe(false);
  });
  expect(r.result.current?.error).toBe(messages.nvke.requestFailed);
  expect(await saved(values)).toBe(false);
  act(() => r.result.current!.onRefresh());
  expect(await saved(values)).toBe(false);
  await waitFor(() => expect(h.calls).toHaveLength(3));
  act(() => h.calls[2].reply({ astroliftNvkeOperatorContract: fixture }));
  await waitFor(() => expect(r.result.current?.canRecover).toBe(true));
  act(() => {
    pending = r.result.current!.onRecover();
  });
  await waitFor(() => expect(h.calls).toHaveLength(4));
  expect(h.calls[3].request.operationName).toBe("GetNvkeRequest");
  expect(h.calls[3].request.variables).toEqual({ siteId: SITE.id, requestId });
  await act(async () => {
    h.calls[3].reply({ astroliftNvkeRequest: row(requestId) });
    expect(await pending!).toBe(true);
  });
  expect(
    h.calls.filter((call) => call.request.operationName === "RequestNvkeOperatorJob")
  ).toHaveLength(1);
});

it("recovers an original receipt with no agent authority, even if contract reads fail", async () => {
  const h = harness(),
    a = args();
  a.authority = null;
  a.context.access.admin = false;
  const record: NvkeRecoveryRecord = {
    ...a.context.scope,
    format: 7,
    mode: "tooling",
    siteId: SITE.id,
    expectedSiteVersion: 1,
    authorityVersion: 1,
    revision: fixture.revision,
    operationKey: "tooling.version",
    timeoutSeconds: 600,
    acknowledgeInfrastructureChange: false,
    requestId: "123e4567-e89b-12d3-a456-426614174093",
    parametersDigest: "a".repeat(64),
    operationId: null,
  };
  const { saveRecovery } = await import("@/lib/nvke/recovery");
  saveRecovery(window.localStorage, record);
  const r = renderHook(() => useNvkeOperatorWorkbench(a), { wrapper: h.wrapper });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  act(() => h.calls[0].fail());
  await waitFor(() => expect(r.result.current?.canRecover).toBe(true));
  let pending: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onRecover();
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    h.calls[1].reply({ astroliftNvkeRequest: row(record.requestId) });
    expect(await pending!).toBe(true);
  });
  expect(h.calls[1].request.operationName).toBe("GetNvkeRequest");
});

it("refuses a mismatched receipt and retains the original recovery identity", async () => {
  const h = harness(),
    a = args(),
    r = renderHook(() => useNvkeOperatorWorkbench(a), { wrapper: h.wrapper });
  await ready(h, r);
  let pending: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onRequest(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  const requestId = h.calls[1].request.variables.input.requestId;
  await act(async () => {
    h.calls[1].reply({
      requestNvkeOperatorJob: {
        ok: true,
        errors: [],
        data: { ...row(requestId), operationKey: "tooling.bootstrap.site" },
      },
    });
    expect(await pending!).toBe(false);
  });
  expect(push).not.toHaveBeenCalled();
  expect(readRecovery(window.localStorage, a.context.scope, SITE.id)?.requestId).toBe(requestId);
});
