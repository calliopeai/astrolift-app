import {
  ApolloClient,
  ApolloLink,
  InMemoryCache,
  Observable,
  type Operation,
} from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { SITE, OPERATION } from "@/components/screens/nvke/fixtures";
import type { NvkeConnectionView } from "@/components/screens/nvke/types";
import { PROVIDER_REQUEST_PROPS } from "@/components/screens/nvke/NvkeProviderRequestPanel.stories";
import { readRecovery } from "@/lib/nvke/recovery";
import { useNvkeProviderRequest } from "./use-nvke-provider-request";
const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
const connection: NvkeConnectionView = {
  ...PROVIDER_REQUEST_PROPS.connection!,
  surface: "DATASTORE",
  isEnabled: true,
  allowHttp: false,
  hasCredentials: true,
  timeoutSeconds: 30,
  verificationCode: "",
};
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
    site: { ...SITE, connections: [connection] },
    contractRevision: SITE.upstreamRevision,
    enabled: true,
    onRefresh: vi.fn(),
    onJournalChange: vi.fn(),
  };
}
const receipt = (requestId: string) => ({
  ...OPERATION,
  id: "123e4567-e89b-12d3-a456-426614174098",
  requestId,
  operationKey: "infrastructure.providers",
  isInventory: false,
  resourceSource: null,
  providerSource: {
    connectionId: connection.id,
    connectionVersion: connection.version,
    siteVersion: SITE.version,
    upstreamRevision: SITE.upstreamRevision,
  },
});
function harness(
  respond: (request: Operation) => Record<string, unknown> | Promise<Record<string, unknown>>
) {
  const calls: Operation[] = [],
    cache = new InMemoryCache();
  const client = new ApolloClient({
    cache,
    link: new ApolloLink(
      (request) =>
        new Observable((observer) => {
          calls.push(request);
          void Promise.resolve()
            .then(() => respond(request))
            .then(
              (data) => {
                observer.next({ data });
                observer.complete();
              },
              (error) => observer.error(error)
            );
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  return { calls, cache, client, wrapper };
}
beforeEach(() => {
  localStorage.clear();
  navigation.push.mockReset();
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: object, run: () => Promise<unknown>) => run(),
    },
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it("persists the reviewed connection before sending the exact dedicated mutation", async () => {
  const a = args();
  const h = harness((request) => {
    expect(request.operationName).toBe("RequestNvkeProviderObservation");
    const saved = readRecovery(localStorage, a.context.scope, SITE.id)!;
    expect(saved).toMatchObject({
      format: 6,
      mode: "providers",
      source: { connectionId: connection.id, connectionVersion: connection.version },
    });
    expect(request.variables.input).toEqual({
      siteId: SITE.id,
      expectedSiteVersion: SITE.version,
      expectedActorId: 41,
      connectionId: connection.id,
      expectedConnectionVersion: connection.version,
      requestId: saved.requestId,
      timeoutSeconds: 600,
    });
    return {
      requestNvkeProviderObservation: { ok: true, errors: [], data: receipt(saved.requestId) },
    };
  });
  const { result } = renderHook(() => useNvkeProviderRequest(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  await act(async () => expect(await result.current!.onRequest(600)).toBe(true));
  expect(navigation.push).toHaveBeenCalledWith(`/nvke/${SITE.id}/operations/${receipt("").id}`);
  expect(readRecovery(localStorage, a.context.scope, SITE.id)).toBeNull();
  expect(a.onJournalChange).toHaveBeenCalledTimes(2);
  expect(h.cache.extract()).toEqual({});
  expect(h.calls[0].getContext()).toMatchObject({
    queryDeduplication: false,
    redactDiagnostics: true,
    expectedNvkeOrganization: SITE.id,
  });
  h.client.stop();
});
it.each([
  "admin",
  "read",
  "disabled-site",
  "disabled-connection",
  "missing-connection",
  "revision",
  "storage",
])("does not admit without the reviewed %s requirement", async (change) => {
  const a = args();
  if (change === "admin") a.context.access.admin = false;
  if (change === "read") a.context.access.read = false;
  if (change === "disabled-site") a.site = { ...a.site, isActive: false };
  if (change === "disabled-connection")
    a.site = { ...a.site, connections: [{ ...connection, isEnabled: false }] };
  if (change === "missing-connection") a.site = { ...a.site, connections: [] };
  if (change === "revision") a.contractRevision = "0".repeat(40);
  if (change === "storage")
    vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw Error("unavailable");
    });
  const h = harness(() => {
    throw Error("UNEXPECTED_ADMISSION");
  });
  const { result } = renderHook(() => useNvkeProviderRequest(a), { wrapper: h.wrapper });
  if (change !== "read") await waitFor(() => expect(result.current?.loading).toBe(false));
  await act(async () => {
    if (result.current) expect(await result.current.onRequest(600)).toBe(false);
  });
  expect(h.calls).toHaveLength(0);
  vi.restoreAllMocks();
  h.client.stop();
});
it("recovers a lost reply with READ access after connection removal and site changes, without another mutation", async () => {
  let accepted: ReturnType<typeof receipt> | null = null;
  const a = args();
  const h = harness((request) => {
    if (request.operationName === "RequestNvkeProviderObservation") {
      accepted = receipt(request.variables.input.requestId);
      throw Error("PRIVATE_LOST_REPLY");
    }
    return { astroliftNvkeRequest: accepted };
  });
  const { result, rerender } = renderHook(
    ({ admin, removed }) => {
      const next = {
        ...a,
        context: { ...a.context, access: { ...a.context.access, admin } },
        site: removed ? { ...a.site, version: 99, connections: [] } : a.site,
      };
      return useNvkeProviderRequest(next);
    },
    { wrapper: h.wrapper, initialProps: { admin: true, removed: false } }
  );
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  await act(async () => expect(await result.current!.onRequest(600)).toBe(false));
  const saved = readRecovery(localStorage, a.context.scope, SITE.id)!;
  expect(result.current?.error).toBe("requestFailed");
  rerender({ admin: false, removed: true });
  await waitFor(() => expect(result.current?.canRecover).toBe(true));
  expect(result.current?.canManage).toBe(false);
  await act(async () => expect(await result.current!.onRecover()).toBe(true));
  expect(
    h.calls.filter((call) => call.operationName === "RequestNvkeProviderObservation")
  ).toHaveLength(1);
  expect(h.calls.find((call) => call.operationName === "GetNvkeRequest")!.variables.requestId).toBe(
    saved.requestId
  );
  expect(readRecovery(localStorage, a.context.scope, SITE.id)).toBeNull();
  expect(h.cache.extract()).toEqual({});
  h.client.stop();
});
it("retries a missing receipt with the same UUID while source changes block a different intent", async () => {
  const a = args();
  let count = 0;
  const h = harness((request) => {
    if (request.operationName === "GetNvkeRequest") return { astroliftNvkeRequest: null };
    if (++count === 1) throw Error("LOST");
    return {
      requestNvkeProviderObservation: {
        ok: true,
        errors: [],
        data: receipt(request.variables.input.requestId),
      },
    };
  });
  const { result } = renderHook(() => useNvkeProviderRequest(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  await act(async () => expect(await result.current!.onRequest(600)).toBe(false));
  const saved = readRecovery(localStorage, a.context.scope, SITE.id)!;
  await act(async () => expect(await result.current!.onRequest(601)).toBe(false));
  expect(count).toBe(1);
  await act(async () => expect(await result.current!.onRequest(600)).toBe(true));
  expect(
    h.calls
      .filter((call) => call.operationName === "RequestNvkeProviderObservation")
      .map((call) => call.variables.input.requestId)
  ).toEqual([saved.requestId, saved.requestId]);
  h.client.stop();
});
it("does not navigate or archive a late mutation reply after an actor visit changes", async () => {
  const a = args();
  let release!: (value: Record<string, unknown>) => void;
  let requestId = "";
  const h = harness((request) => {
    requestId = request.variables.input.requestId;
    return new Promise((resolve) => {
      release = resolve;
    });
  });
  const { result, rerender } = renderHook(({ actor }) => useNvkeProviderRequest(args(actor)), {
    wrapper: h.wrapper,
    initialProps: { actor: 41 },
  });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  let pending!: Promise<boolean>;
  act(() => {
    pending = result.current!.onRequest(600);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  rerender({ actor: 42 });
  await waitFor(() => expect(result.current?.loading).toBe(false));
  await act(async () => {
    release({ requestNvkeProviderObservation: { ok: true, errors: [], data: receipt(requestId) } });
    expect(await pending).toBe(false);
  });
  expect(navigation.push).not.toHaveBeenCalled();
  expect(readRecovery(localStorage, a.context.scope, SITE.id)?.requestId).toBe(requestId);
  h.client.stop();
});
