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
import { SITE, OPERATION, MAINTENANCE_OBSERVATION } from "@/components/screens/nvke/fixtures";
import { METRICS_AUTHORITY } from "@/components/screens/nvke/NvkeMetricsPanel.stories";
import { readRecovery } from "@/lib/nvke/recovery";
import { useNvkeCredentialRequest } from "./use-nvke-credential-request";
const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
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
    site: SITE,
    operation: { ...OPERATION, operationKey: "getCluster", status: "SUCCEEDED" as const },
    maintenance: {
      observation: {
        ...MAINTENANCE_OBSERVATION,
        operationId: OPERATION.id,
        operationVersion: OPERATION.version,
        sourceSiteVersion: SITE.version,
      },
      loading: false,
      error: null,
      onRefresh: vi.fn(),
    },
    enabled: true,
    onRefresh: vi.fn(),
  };
}
const receipt = (requestId: string) => ({
  ...OPERATION,
  id: "123e4567-e89b-12d3-a456-426614174098",
  requestId,
  operationKey: "metrics.credentials",
  isInventory: false,
  resourceSource: null,
});
function harness(
  respond: (request: Operation) => Record<string, unknown> | Promise<Record<string, unknown>>,
  authority: () => typeof METRICS_AUTHORITY | null = () => METRICS_AUTHORITY
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
            .then(() =>
              request.operationName === "GetNvkeMetricsAuthority"
                ? { astroliftNvkeMetricsAuthority: authority() }
                : respond(request)
            )
            .then((data) => {
              observer.next({ data });
              observer.complete();
            })
            .catch((error) => observer.error(error));
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
      request: async (_name: string, _options: object, run: () => Promise<unknown>) => run(),
    },
  });
});
afterEach(() => vi.unstubAllGlobals());
it("uses the source-bound mutation with durable private metadata and no cache", async () => {
  const h = harness((request) => {
    const saved = readRecovery(localStorage, args().context.scope, SITE.id)!;
    expect(saved).toMatchObject({
      format: 5,
      source: { operationId: OPERATION.id, version: OPERATION.version, resourceKey: null },
      authorityVersion: METRICS_AUTHORITY.version,
    });
    expect(request.variables.input).toMatchObject({
      expectedActorId: 41,
      expectedSiteVersion: SITE.version,
      expectedAuthorityVersion: METRICS_AUTHORITY.version,
      expectedSourceVersion: OPERATION.version,
      requestId: saved.requestId,
      timeoutSeconds: 600,
    });
    return {
      requestNvkeCredentialObservation: { ok: true, errors: [], data: receipt(saved.requestId) },
    };
  });
  const { result } = renderHook(() => useNvkeCredentialRequest(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  await act(async () => expect(await result.current!.onRequest(600)).toBe(true));
  expect(navigation.push).toHaveBeenCalledWith(
    `/nvke/${SITE.id}/operations/123e4567-e89b-12d3-a456-426614174098`
  );
  expect(h.cache.extract()).toEqual({});
  expect(h.calls[1].getContext()).toMatchObject({
    redactDiagnostics: true,
    queryDeduplication: false,
    expectedNvkeOrganization: SITE.id,
  });
  expect(Object.values(localStorage).join()).not.toContain(METRICS_AUTHORITY.endpoint);
  h.client.stop();
});
it("recovers after a lost reply with READ access despite removed authority and stale source", async () => {
  let originalId = "",
    removed = false,
    writes = 0;
  const h = harness(
    (request) => {
      if (request.operationName === "RequestNvkeCredentialObservation") {
        writes++;
        originalId = request.variables.input.requestId;
        throw Error("PRIVATE_LOST_REPLY");
      }
      return { astroliftNvkeRequest: receipt(originalId) };
    },
    () => (removed ? null : METRICS_AUTHORITY)
  );
  const { result, rerender } = renderHook(
    ({ admin }) => useNvkeCredentialRequest(args(41, admin)),
    { wrapper: h.wrapper, initialProps: { admin: true } }
  );
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  await act(async () => expect(await result.current!.onRequest(600)).toBe(false));
  expect(result.current?.error).toBe("requestFailed");
  expect(result.current?.recovery?.requestId).toBe(originalId);
  removed = true;
  rerender({ admin: false });
  await waitFor(() => expect(result.current?.canRecover).toBe(true));
  expect(result.current?.canManage).toBe(false);
  await act(async () => expect(await result.current!.onRecover()).toBe(true));
  expect(writes).toBe(1);
  expect(readRecovery(localStorage, args().context.scope, SITE.id)).toBeNull();
  expect(navigation.push).toHaveBeenCalledOnce();
  h.client.stop();
});
it("does not interpret an omitted receipt field as permission to readmit", async () => {
  let writes = 0;
  const h = harness((request) => {
    if (request.operationName === "RequestNvkeCredentialObservation") {
      writes++;
      throw Error("LOST");
    }
    return {};
  });
  const { result } = renderHook(() => useNvkeCredentialRequest(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  await act(async () => expect(await result.current!.onRequest(600)).toBe(false));
  const saved = readRecovery(localStorage, args().context.scope, SITE.id);
  await act(async () => expect(await result.current!.onRequest(600)).toBe(false));
  expect(writes).toBe(1);
  expect(readRecovery(localStorage, args().context.scope, SITE.id)).toEqual(saved);
  h.client.stop();
});
it("ignores late admission across A to B to A while preserving the unknown original", async () => {
  let release!: (value: Record<string, unknown>) => void,
    originalId = "";
  const late = new Promise<Record<string, unknown>>((resolve) => {
    release = resolve;
  });
  const h = harness((request) => {
    originalId = request.variables.input.requestId;
    return late;
  });
  const { result, rerender } = renderHook(({ actor }) => useNvkeCredentialRequest(args(actor)), {
    wrapper: h.wrapper,
    initialProps: { actor: 41 },
  });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  const stale = result.current!;
  let submitted!: Promise<boolean>;
  act(() => {
    submitted = stale.onRequest(600);
  });
  await waitFor(() => expect(originalId).not.toBe(""));
  rerender({ actor: 42 });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  rerender({ actor: 41 });
  await waitFor(() => expect(result.current?.canRecover).toBe(true));
  await act(async () => {
    release({
      requestNvkeCredentialObservation: { ok: true, errors: [], data: receipt(originalId) },
    });
    expect(await submitted).toBe(false);
    expect(await stale.onRequest(600)).toBe(false);
  });
  expect(navigation.push).not.toHaveBeenCalled();
  expect(readRecovery(localStorage, args().context.scope, SITE.id)?.requestId).toBe(originalId);
  h.client.stop();
});
it("blocks fresh collection on stale source or permission loss while retaining READ recovery", async () => {
  const h = harness(() => ({ astroliftNvkeRequest: null }));
  const a = args();
  a.maintenance.observation.sourceSiteVersion = SITE.version - 1;
  const { result } = renderHook(() => useNvkeCredentialRequest(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.loading).toBe(false));
  expect(result.current?.canManage).toBe(false);
  await act(async () => expect(await result.current!.onRequest(600)).toBe(false));
  expect(h.calls).toHaveLength(1);
  h.client.stop();
});
