import {
  ApolloClient,
  ApolloLink,
  InMemoryCache,
  Observable,
  type Operation,
} from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  ACTIONS,
  COLLECTION,
  DIAGNOSTIC_OBSERVATION,
  DIAGNOSTIC_ROW,
  INVENTORY_ITEM,
  OPERATION,
  SITE,
  MAINTENANCE_OBSERVATION,
} from "@/components/screens/nvke/fixtures";
import { nvkeScopeKey, readRecovery } from "@/lib/nvke/recovery";
import { useNvkeContext } from "./nvke.hooks";
import { useNvkeResourceActions } from "./use-nvke-resource-actions";
import { useNvkeSite } from "./use-nvke-site";
import { useNvkeOperation } from "./use-nvke-operation";
import { useNvkeInventory, useNvkeResource } from "./use-nvke-inventory";

const navigation = vi.hoisted(() => ({ push: vi.fn() }));
const caller = vi.hoisted(() => ({ actorId: 41, manage: true }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("./nvke.hooks", async (original) => {
  const actual = await original<typeof import("./nvke.hooks")>();
  return {
    ...actual,
    useNvkeContext: () => {
      const scope = {
        server: "https://astro.example.test/app/gql/config/",
        organizationId: "123e4567-e89b-12d3-a456-426614174000",
        actorId: caller.actorId,
      };
      return {
        scope,
        scopeKey: JSON.stringify([scope.server, scope.organizationId, scope.actorId]),
        access: { read: true, manage: caller.manage, admin: true, loading: false },
        loading: false,
        failed: false,
      };
    },
  };
});
const scope = {
  server: "https://astro.example.test/app/gql/config/",
  organizationId: "123e4567-e89b-12d3-a456-426614174000",
  actorId: 41,
};
const values = {
  operationKey: "createCluster",
  timeoutSeconds: 7200,
  parametersText: '{"body":{"count":9007199254740993,"token":"native-private"}}',
};
beforeEach(() => {
  localStorage.clear();
  caller.actorId = 41;
  caller.manage = true;
  navigation.push.mockReset();
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: unknown, run: () => Promise<unknown>) => run(),
    },
  });
});
afterEach(() => vi.unstubAllGlobals());
function harness(
  respond: (operation: Operation) => Record<string, unknown> | Promise<Record<string, unknown>>,
  collection = COLLECTION,
  site = SITE
) {
  const calls: Operation[] = [];
  const cache = new InMemoryCache();
  const client = new ApolloClient({
    cache,
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          calls.push(operation);
          void Promise.resolve()
            .then(() => {
              if (operation.operationName === "GetNvkeSite")
                return {
                  astroliftNvkeSite: {
                    ...site,
                    createdAt: OPERATION.createdAt,
                    updatedAt: OPERATION.updatedAt,
                  },
                };
              if (operation.operationName === "GetNvkeContract")
                return {
                  astroliftNvkeContract: {
                    revision: SITE.upstreamRevision,
                    actions: Array.from(
                      new Map(
                        [...ACTIONS, ...collection.actions].map((action) => [action.key, action])
                      ).values()
                    ),
                    collections: [collection],
                    controllerOpenapi: {},
                    nativeWorkflows: [],
                    resources: [],
                  },
                };
              if (operation.operationName === "ListNvkeOperations")
                return { astroliftNvkeOperations: { items: [], nextCursor: null, totalCount: 0 } };
              if (operation.operationName === "GetNvkeMaintenance")
                return {
                  astroliftNvkeMaintenanceObservation: {
                    ...MAINTENANCE_OBSERVATION,
                    siteId: operation.variables.siteId,
                    operationId: operation.variables.operationId,
                    operationVersion: operation.variables.expectedVersion,
                  },
                };
              if (operation.operationName === "ListNvkeAccess")
                return {
                  astroliftNvkeWorkloadAccessBindings: {
                    items: [],
                    nextCursor: null,
                    totalCount: 0,
                  },
                };
              return respond(operation);
            })
            .then((data) => {
              observer.next({ data });
              observer.complete();
            })
            .catch((error) => observer.error(error));
        })
    ),
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  return { wrapper, calls, client, cache };
}

it("admits inventory through the durable inventory mutation without fetching hidden history", async () => {
  const { wrapper, calls } = harness((request) => {
    if (request.operationName === "RequestNvkeInventory") {
      const input = request.variables.input;
      return {
        requestNvkeInventory: {
          ok: true,
          errors: [],
          data: {
            ...OPERATION,
            isInventory: true,
            operationKey: COLLECTION.key,
            requestId: input.requestId,
          },
        },
      };
    }
    throw new Error("Unexpected inventory request");
  });
  const { result } = renderHook(() => useNvkeInventory(SITE.id, COLLECTION.key, null), { wrapper });
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  let admitted = false;
  await act(async () => {
    admitted = await result.current.onRequest({ ...values, operationKey: COLLECTION.key });
  });
  expect(admitted).toBe(true);
  expect(calls.filter((c) => c.operationName === "RequestNvkeInventory")).toHaveLength(1);
  expect(
    calls.some((c) =>
      ["ListNvkeOperations", "ListNvkeInventoryPage", "RequestNvkeOperationText"].includes(
        c.operationName ?? ""
      )
    )
  ).toBe(false);
  expect(navigation.push).toHaveBeenCalledWith(
    `/nvke/${SITE.id}/resources/${COLLECTION.key}?snapshot=${OPERATION.id}`
  );
  expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
});

it("keeps same-visit inventory source mounted during refresh and clears it on caller change", async () => {
  const { wrapper } = harness(() => {
    throw new Error("Unexpected source request");
  });
  const { result, rerender } = renderHook(() => useNvkeInventory(SITE.id, COLLECTION.key, null), {
    wrapper,
  });
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  expect(result.current.site?.id).toBe(SITE.id);
  act(() => result.current.onRefresh());
  expect(result.current.loading).toBe(true);
  expect(result.current.site?.id).toBe(SITE.id);
  expect(result.current.collection?.key).toBe(COLLECTION.key);
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  caller.actorId = 42;
  rerender();
  expect(result.current.site).toBeNull();
  expect(result.current.collection).toBeNull();
  caller.actorId = 41;
  rerender();
  expect(result.current.site).toBeNull();
  expect(result.current.collection).toBeNull();
  await waitFor(() => expect(result.current.site?.id).toBe(SITE.id));
});

it.each(["RUNNING", "SUCCEEDED", "WRONG_SITE"])(
  "only pages a successful matching inventory snapshot: %s",
  async (state) => {
    const { wrapper, calls, cache } = harness((request) => {
      if (request.operationName === "GetNvkeOperation")
        return {
          astroliftNvkeOperation: {
            ...OPERATION,
            isInventory: true,
            operationKey: COLLECTION.key,
            siteId: state === "WRONG_SITE" ? "123e4567-e89b-12d3-a456-426614174099" : SITE.id,
            status: state === "RUNNING" ? "RUNNING" : "SUCCEEDED",
          },
        };
      if (request.operationName === "ListNvkeInventoryPage")
        return {
          astroliftNvkeInventoryPage: {
            items: [INVENTORY_ITEM],
            nextCursor: null,
            totalCount: 501,
            operationId: OPERATION.id,
            observedAt: OPERATION.updatedAt,
            resourceVersion: "snapshot-rv",
            nativePageCount: 2,
            collection: COLLECTION,
          },
        };
      throw new Error("Unexpected snapshot request");
    });
    const { result } = renderHook(() => useNvkeInventory(SITE.id, COLLECTION.key, OPERATION.id), {
      wrapper,
    });
    await waitFor(() => expect(result.current.catalogLoading).toBe(false));
    if (state === "SUCCEEDED") {
      await waitFor(() => expect(result.current.table.totalCount).toBe(501));
      expect(result.current.table.rows[0].desiredCount).toBe("9007199254740993");
      expect(result.current.table.searchEnabled).toBe(true);
      const page = calls.find((c) => c.operationName === "ListNvkeInventoryPage")!;
      expect(page.variables).toMatchObject({
        expectedActorId: 41,
        operationId: OPERATION.id,
        search: null,
      });
    } else {
      await waitFor(() =>
        expect(calls.some((c) => c.operationName === "GetNvkeOperation")).toBe(true)
      );
      expect(calls.some((c) => c.operationName === "ListNvkeInventoryPage")).toBe(false);
      expect(result.current.table.rows).toEqual([]);
    }
    expect(calls.some((c) => c.operationName === "RequestNvkeInventory")).toBe(false);
    expect(cache.extract()).toEqual({});
  }
);

it("loads only safe resource metadata and downloads exact text only on explicit request", async () => {
  const raw = '{"desiredCount":9007199254740993,"token":"PRIVATE_NATIVE_DOCUMENT"}';
  const create = vi.fn(() => "blob:local-test"),
    revoke = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    }
  );
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const { wrapper, calls, cache } = harness((request) => {
    if (request.operationName === "GetNvkeInventoryResource")
      return {
        astroliftNvkeInventoryResource: {
          siteId: SITE.id,
          operationId: OPERATION.id,
          operationVersion: OPERATION.version,
          observedAt: OPERATION.updatedAt,
          collection: COLLECTION,
          item: INVENTORY_ITEM,
        },
      };
    if (request.operationName === "GetNvkeInventoryDocumentText")
      return { astroliftNvkeInventoryDocumentText: { ok: true, errors: [], data: raw } };
    throw new Error("Unexpected detail request");
  });
  const { result } = renderHook(
    () => useNvkeResource(SITE.id, COLLECTION.key, OPERATION.id, INVENTORY_ITEM.key),
    { wrapper }
  );
  await waitFor(() => expect(result.current.item?.key).toBe(INVENTORY_ITEM.key));
  expect(calls.some((c) => c.operationName === "GetNvkeInventoryDocumentText")).toBe(false);
  await act(async () => {
    await result.current.onDownload();
  });
  expect(calls.filter((c) => c.operationName === "GetNvkeInventoryDocumentText")).toHaveLength(1);
  expect(create).toHaveBeenCalledTimes(1);
  const blob = create.mock.calls[0] as unknown as [Blob];
  expect(await blob[0].text()).toBe(raw);
  expect(click).toHaveBeenCalledTimes(1);
  expect(cache.extract()).toEqual({});
  await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:local-test"), { timeout: 2000 });
  click.mockRestore();
});

it("loads an explicit resource edit and admits its exact reviewed source through the shared journal", async () => {
  const source = {
    operationId: OPERATION.id,
    version: OPERATION.version,
    resourceKey: INVENTORY_ITEM.key,
  };
  const text = '{"body":{"controlPlaneConfig":{"desiredCount":3}}}';
  const issuedId = "123e4567-e89b-12d3-a456-426614174088";
  const { wrapper, calls, cache } = harness((request) => {
    if (request.operationName === "GetNvkeInventoryResource")
      return {
        astroliftNvkeInventoryResource: {
          siteId: SITE.id,
          operationId: OPERATION.id,
          operationVersion: OPERATION.version,
          observedAt: OPERATION.updatedAt,
          collection: COLLECTION,
          item: INVENTORY_ITEM,
        },
      };
    if (request.operationName === "GetNvkeResourceEditor")
      return {
        astroliftNvkeResourceEditor: {
          ok: true,
          errors: [],
          data: {
            operationKey: "updateCluster",
            parametersText: text,
            preparationActionKey: null,
            concurrency: "observed_only",
            source: { ...source, __typename: "NvkeResourceSourceType" },
          },
        },
      };
    if (request.operationName === "RequestNvkeResourceOperation")
      return {
        requestNvkeResourceOperation: {
          ok: true,
          errors: [],
          data: {
            ...OPERATION,
            id: issuedId,
            operationKey: "updateCluster",
            requestId: request.variables.input.requestId,
            resourceSource: { ...source, __typename: "NvkeResourceSourceType" },
          },
        },
      };
    throw new Error("Unexpected resource edit request");
  });
  const { result } = renderHook(
    () => useNvkeResource(SITE.id, COLLECTION.key, OPERATION.id, INVENTORY_ITEM.key),
    { wrapper }
  );
  await waitFor(() => expect(result.current.resourceActions?.sourceReady).toBe(true));
  expect(calls.some((call) => call.operationName === "GetNvkeResourceEditor")).toBe(false);
  await act(async () => {
    await result.current.resourceActions!.onLoad();
  });
  expect(result.current.resourceActions?.editor?.parametersText).toBe(text);
  const loadedEditor = result.current.resourceActions?.editor;
  act(() => result.current.onRefresh());
  expect(result.current.resourceActions?.editor).toEqual(loadedEditor);
  expect(result.current.resourceActions?.sourceReady).toBe(false);
  await waitFor(() => expect(result.current.resourceActions?.sourceReady).toBe(true));
  expect(result.current.resourceActions?.editor).toEqual(loadedEditor);
  await act(async () => {
    await result.current.resourceActions!.onRequest({
      operationKey: "updateCluster",
      parametersText: text,
      timeoutSeconds: 7200,
    });
  });
  const submitted = calls.find((call) => call.operationName === "RequestNvkeResourceOperation")!;
  expect(submitted.variables.input).toMatchObject({
    sourceOperationId: source.operationId,
    expectedSourceVersion: source.version,
    sourceResourceKey: source.resourceKey,
    parametersText: text,
    expectedActorId: 41,
  });
  expect(navigation.push).toHaveBeenCalledWith(`/nvke/${SITE.id}/operations/${issuedId}`);
  expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
  expect(Object.values(localStorage).join()).not.toContain("desiredCount");
  expect(cache.extract()).toEqual({});
});
it("recovers a lost admission reply by receipt without submitting the native request again", async () => {
  let accepted: typeof OPERATION | null = null;
  const h = harness((operation) => {
    if (operation.operationName === "GetNvkeRequest") return { astroliftNvkeRequest: accepted };
    if (operation.operationName === "RequestNvkeOperationText") {
      const input = operation.variables.input;
      expect(input.parametersText).toBe(values.parametersText);
      expect(input.expectedActorId).toBe(41);
      expect(readRecovery(localStorage, scope, SITE.id)?.requestId).toBe(input.requestId);
      accepted = { ...OPERATION, requestId: input.requestId };
      throw new Error("lost reply containing native-private");
    }
    throw new Error("unexpected operation");
  });
  const { result } = renderHook(() => useNvkeSite(SITE.id), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  await act(async () => {
    expect(await result.current.onRequest(values)).toBe(false);
  });
  expect(result.current.error).toBe("requestFailed");
  expect(result.current.recovery?.requestId).toBe(accepted!.requestId);
  expect(JSON.stringify(localStorage)).not.toContain("native-private");
  act(() => result.current.onRefresh());
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  await act(async () => {
    expect(await result.current.onRequest(values)).toBe(true);
  });
  expect(
    h.calls.filter((operation) => operation.operationName === "RequestNvkeOperationText")
  ).toHaveLength(1);
  expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
  expect(navigation.push).toHaveBeenCalledWith(`/nvke/${SITE.id}/operations/${OPERATION.id}`);
  expect(h.cache.extract()).toEqual({});
  h.client.stop();
});
it("never changes the body or nonce when an unresolved request has no visible receipt", async () => {
  const h = harness((operation) => {
    if (operation.operationName === "GetNvkeRequest") return { astroliftNvkeRequest: null };
    throw new Error("lost reply");
  });
  const { result } = renderHook(() => useNvkeSite(SITE.id), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  await act(async () => {
    await result.current.onRequest(values);
  });
  const original = readRecovery(localStorage, scope, SITE.id);
  await act(async () => {
    await result.current.onRequest({ ...values, parametersText: "{}" });
  });
  expect(result.current.error).toBe("recoveryMismatch");
  expect(readRecovery(localStorage, scope, SITE.id)).toEqual(original);
  expect(
    h.calls.filter((operation) => operation.operationName === "RequestNvkeOperationText")
  ).toHaveLength(1);
  h.client.stop();
});
it("allows corrected input only after a definitive validation rejection", async () => {
  const requestIds: string[] = [];
  const h = harness((operation) => {
    const input = operation.variables.input;
    requestIds.push(input.requestId);
    return {
      requestNvkeOperationText:
        requestIds.length === 1
          ? {
              ok: false,
              errors: [{ code: "INVALID_INPUT", message: "Invalid native request" }],
              data: null,
            }
          : { ok: true, errors: [], data: { ...OPERATION, requestId: input.requestId } },
    };
  });
  const { result } = renderHook(() => useNvkeSite(SITE.id), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  await act(async () => {
    expect(await result.current.onRequest(values)).toBe(false);
  });
  expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
  act(() => result.current.onRefresh());
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  await act(async () => {
    expect(await result.current.onRequest({ ...values, parametersText: "{}" })).toBe(true);
  });
  expect(requestIds).toHaveLength(2);
  expect(requestIds[0]).not.toBe(requestIds[1]);
  h.client.stop();
});
it.each(["preserve", "clear", "replace"] as const)(
  "sends version-guarded configuration with %s credentials",
  async (credentialMode) => {
    const h = harness((operation) => {
      expect(operation.operationName).toBe("ConfigureNvkeConnection");
      const input = operation.variables.input;
      expect(input.expectedSiteVersion).toBe(SITE.version);
      expect(input.expectedConnectionVersion).toBe(2);
      expect(input.credentials).toEqual(
        credentialMode === "preserve"
          ? null
          : {
              token: credentialMode === "replace" ? "private-token" : "",
              caPem: "",
              clientCertificatePem: "",
              clientKeyPem: "",
            }
      );
      return {
        configureNvkeConnection: {
          ok: true,
          errors: [],
          data: {
            ...SITE,
            version: SITE.version + 1,
            createdAt: OPERATION.createdAt,
            updatedAt: OPERATION.updatedAt,
          },
        },
      };
    });
    const { result } = renderHook(() => useNvkeSite(SITE.id), { wrapper: h.wrapper });
    await waitFor(() => expect(result.current.catalogLoading).toBe(false));
    await act(async () => {
      expect(
        await result.current.onConfigure({
          surface: "CONTROLLER",
          endpoint: SITE.connections[0].endpoint,
          timeoutSeconds: 30,
          allowHttp: false,
          credentialMode,
          token: "private-token",
          caPem: "",
          clientCertificatePem: "",
          clientKeyPem: "",
        })
      ).toBe(true);
    });
    expect(h.cache.extract()).toEqual({});
    expect(localStorage.length).toBe(0);
    h.client.stop();
  }
);
it("does not navigate or archive a late admission response after the caller changes", async () => {
  let release: (value: Record<string, unknown>) => void = () => {};
  let requestId = "";
  const h = harness((operation) => {
    requestId = operation.variables.input.requestId;
    return new Promise((resolve) => {
      release = resolve;
    });
  });
  const { result, rerender } = renderHook(() => useNvkeSite(SITE.id), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  let pending: Promise<boolean>;
  act(() => {
    pending = result.current.onRequest(values);
  });
  await waitFor(() => expect(requestId).not.toBe(""));
  caller.actorId = 42;
  rerender();
  await act(async () => {
    release({
      requestNvkeOperationText: { ok: true, errors: [], data: { ...OPERATION, requestId } },
    });
    expect(await pending).toBe(false);
  });
  expect(navigation.push).not.toHaveBeenCalled();
  expect(readRecovery(localStorage, scope, SITE.id)?.requestId).toBe(requestId);
  expect(result.current.pending).toBe(false);
  expect(nvkeScopeKey(scope)).not.toContain("native-private");
  h.client.stop();
});
it("binds an operation URL to its site and does not fetch results automatically", async () => {
  const h = harness(() => ({
    astroliftNvkeOperation: { ...OPERATION, siteId: scope.organizationId },
  }));
  const { result } = renderHook(() => useNvkeOperation(SITE.id, OPERATION.id), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.operation).toBeNull();
  expect(result.current.canAct).toBe(false);
  await act(async () => {
    await result.current.onDownload();
  });
  expect(h.calls.some((operation) => operation.operationName?.includes("Result"))).toBe(false);
  expect(h.cache.extract()).toEqual({});
  h.client.stop();
});

it("downloads explicit result text losslessly without putting it in the Apollo cache", async () => {
  const text = '{"nativeInteger":9007199254740993,"credential":"private-result"}';
  const create = vi.fn((_blob: Blob) => "blob:test"),
    revoke = vi.fn();
  Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: revoke, configurable: true });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const h = harness((operation) =>
    operation.operationName === "GetNvkeOperation"
      ? { astroliftNvkeOperation: OPERATION }
      : { astroliftNvkeOperationResultText: { ok: true, errors: [], data: text } }
  );
  try {
    const { result } = renderHook(() => useNvkeOperation(SITE.id, OPERATION.id), {
      wrapper: h.wrapper,
    });
    await waitFor(() => expect(result.current.canAct).toBe(true));
    expect(h.calls.some((operation) => operation.operationName?.includes("Result"))).toBe(false);
    await act(async () => {
      await result.current.onDownload();
    });
    expect(create).toHaveBeenCalledOnce();
    const blob = create.mock.calls[0][0];
    const content = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsText(blob);
    });
    expect(content).toBe(text);
    expect(click).toHaveBeenCalledOnce();
    expect(h.cache.extract()).toEqual({});
    expect(localStorage.length).toBe(0);
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:test"), { timeout: 2000 });
  } finally {
    click.mockRestore();
    h.client.stop();
    delete (URL as unknown as Record<string, unknown>).createObjectURL;
    delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
  }
});

const reviewedSource = {
  operationId: OPERATION.id,
  version: OPERATION.version,
  resourceKey: INVENTORY_ITEM.key,
};
const resourceText = '{"body":{"desiredCount":9007199254740993}}';
const useResourceActionArgs = () => ({
  context: useNvkeContext(),
  site: SITE,
  contract: {
    revision: SITE.upstreamRevision,
    actions: ACTIONS,
    collections: [COLLECTION],
    controllerOpenapi: {},
    nativeWorkflows: [],
    resources: [],
  },
  collection: COLLECTION,
  source: reviewedSource,
  target: INVENTORY_ITEM.name,
  sourceReady: true,
});
const editorReply = (
  source: typeof reviewedSource | { operationId: string; version: number; resourceKey: null },
  preparationActionKey: string | null = null
) => ({
  astroliftNvkeResourceEditor: {
    ok: true,
    errors: [],
    data: {
      operationKey: "updateCluster",
      parametersText: preparationActionKey ? null : resourceText,
      preparationActionKey,
      concurrency: "native_version",
      source,
    },
  },
});
it("prepares a versioned edit from the confirmed native GET without resubmitting preparation", async () => {
  const preparedId = "123e4567-e89b-12d3-a456-426614174089";
  const preparedSource = { operationId: preparedId, version: 8, resourceKey: null };
  let interrupted = false;
  const h = harness((request) => {
    if (request.operationName === "GetNvkeResourceEditor")
      return editorReply(
        request.variables.sourceOperationId === preparedId ? preparedSource : reviewedSource,
        request.variables.sourceOperationId === preparedId ? null : "getCluster"
      );
    if (request.operationName === "RequestNvkeResourceOperation")
      return {
        requestNvkeResourceOperation: {
          ok: true,
          errors: [],
          data: {
            ...OPERATION,
            id: preparedId,
            operationKey: "getCluster",
            requestId: request.variables.input.requestId,
            resourceSource: reviewedSource,
          },
        },
      };
    if (request.operationName === "GetNvkeOperation" && !interrupted) {
      interrupted = true;
      throw new Error("Preparation status temporarily unavailable");
    }
    if (request.operationName === "GetNvkeOperation")
      return {
        astroliftNvkeOperation: {
          ...OPERATION,
          id: preparedId,
          version: preparedSource.version,
          operationKey: "getCluster",
          status: "SUCCEEDED",
          resourceSource: reviewedSource,
        },
      };
    throw new Error("Unexpected preparation request");
  });
  const { result } = renderHook(() => useNvkeResourceActions(useResourceActionArgs()), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(result.current?.sourceReady).toBe(true));
  await act(async () => {
    expect(await result.current!.onLoad()).toBe(true);
  });
  expect(result.current?.editor?.parametersText).toBeNull();
  await act(async () => {
    expect(await result.current!.onPrepare()).toBe(false);
  });
  expect(result.current?.error).toBe("requestFailed");
  act(() => result.current!.onRefresh());
  await waitFor(() => expect(result.current?.sourceReady).toBe(true));
  await act(async () => {
    expect(await result.current!.onPrepare()).toBe(true);
  });
  expect(result.current?.editor?.parametersText).toBe(resourceText);
  expect(result.current?.editor?.source).toEqual(preparedSource);
  expect(h.calls.filter((c) => c.operationName === "RequestNvkeResourceOperation")).toHaveLength(1);
  expect(
    h.calls.find((c) => c.operationName === "RequestNvkeResourceOperation")!.variables.input
  ).toMatchObject({
    operationKey: "getCluster",
    sourceOperationId: reviewedSource.operationId,
    expectedSourceVersion: reviewedSource.version,
    sourceResourceKey: reviewedSource.resourceKey,
  });
  expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
  expect(h.cache.extract()).toEqual({});
});
it("recovers a resource admission lost reply by receipt without issuing a second request", async () => {
  let accepted: Record<string, unknown> | null = null;
  const h = harness((request) => {
    if (request.operationName === "GetNvkeResourceEditor") return editorReply(reviewedSource);
    if (request.operationName === "GetNvkeRequest") return { astroliftNvkeRequest: accepted };
    if (request.operationName === "RequestNvkeResourceOperation") {
      accepted = {
        ...OPERATION,
        operationKey: "updateCluster",
        requestId: request.variables.input.requestId,
        resourceSource: reviewedSource,
      };
      throw new Error("Lost reply with private native fields");
    }
    throw new Error("Unexpected recovery request");
  });
  const { result } = renderHook(() => useNvkeResourceActions(useResourceActionArgs()), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(result.current?.sourceReady).toBe(true));
  await act(async () => {
    await result.current!.onLoad();
  });
  await act(async () => {
    expect(
      await result.current!.onRequest({
        operationKey: "updateCluster",
        parametersText: resourceText,
        timeoutSeconds: 7200,
      })
    ).toBe(false);
  });
  expect(result.current?.recovery).toMatchObject({
    format: 3,
    mode: "resource",
    source: reviewedSource,
  });
  expect(JSON.stringify(localStorage)).not.toContain("desiredCount");
  await act(async () => {
    expect(await result.current!.onRecover()).toBe(true);
  });
  expect(h.calls.filter((c) => c.operationName === "RequestNvkeResourceOperation")).toHaveLength(1);
  expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
  expect(navigation.push).toHaveBeenCalledWith(`/nvke/${SITE.id}/operations/${OPERATION.id}`);
});
it("discards a late resource editor across an actor A to B to A change", async () => {
  let finish!: (value: Record<string, unknown>) => void;
  const h = harness(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      })
  );
  const { result, rerender } = renderHook(() => useNvkeResourceActions(useResourceActionArgs()), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(result.current?.sourceReady).toBe(true));
  let loading!: Promise<boolean>;
  act(() => {
    loading = result.current!.onLoad();
  });
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  caller.actorId = 42;
  rerender();
  caller.actorId = 41;
  rerender();
  await act(async () => {
    finish(editorReply(reviewedSource));
    expect(await loading).toBe(false);
  });
  expect(result.current?.editor).toBeNull();
  expect(navigation.push).not.toHaveBeenCalled();
});

it.each(["CONTROLLER", "TENANT", "OPERATOR", "DATASTORE"] as const)(
  "creates a %s collection resource through native admission while retaining exact text",
  async (surface) => {
    const action = {
      ...COLLECTION.actions.find((a) => a.key === "createCluster")!,
      key: surface === "DATASTORE" ? "datastore.createNVKECluster" : "createCluster",
      surface,
      verb: surface === "DATASTORE" ? "create" : "POST",
    };
    const collection = {
      ...COLLECTION,
      surface,
      actions: [{ ...COLLECTION.actions[0], surface }, action],
    };
    const h = harness(
      (request) => {
        if (request.operationName === "RequestNvkeOperationText") {
          const pending = readRecovery(localStorage, scope, SITE.id);
          expect(pending).toMatchObject({ format: 2, mode: "native", operationKey: action.key });
          return {
            requestNvkeOperationText: {
              ok: true,
              errors: [],
              data: {
                ...OPERATION,
                isInventory: false,
                operationKey: action.key,
                requestId: request.variables.input.requestId,
              },
            },
          };
        }
        throw new Error("Creation must use native admission only");
      },
      collection,
      { ...SITE, connections: [{ ...SITE.connections[0], surface }] }
    );
    const { result } = renderHook(() => useNvkeInventory(SITE.id, collection.key, null), {
      wrapper: h.wrapper,
    });
    await waitFor(() => expect(result.current.catalogLoading).toBe(false));
    expect(result.current.actions.map((a) => a.key)).toEqual([collection.key, action.key]);
    const text =
      surface === "DATASTORE"
        ? '{"document":{"metadata":{"name":"new-cluster"},"spec":{"count":9007199254740993}}}'
        : '{"body":{"name":"new-cluster","count":9007199254740993}}';
    await act(async () => {
      expect(
        await result.current.onRequest({
          operationKey: action.key,
          parametersText: text,
          timeoutSeconds: 7200,
        })
      ).toBe(true);
    });
    const mutation = h.calls.find((c) => c.operationName === "RequestNvkeOperationText")!;
    expect(mutation.variables.input).toMatchObject({
      parametersText: text,
      expectedActorId: 41,
      expectedSiteVersion: SITE.version,
    });
    expect(navigation.push).toHaveBeenCalledWith(`/nvke/${SITE.id}/operations/${OPERATION.id}`);
    expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
    expect(h.cache.extract()).toEqual({});
  }
);
it("refuses collection edits and unpermitted creation before transmitting a mutation", async () => {
  const h = harness(() => {
    throw new Error("Must not transmit");
  });
  const { result, rerender } = renderHook(() => useNvkeInventory(SITE.id, COLLECTION.key, null), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(result.current.catalogLoading).toBe(false));
  await act(async () => {
    expect(await result.current.onRequest({ ...values, operationKey: "updateCluster" })).toBe(
      false
    );
  });
  caller.manage = false;
  rerender();
  await act(async () => {
    expect(await result.current.onRequest(values)).toBe(false);
  });
  expect(h.calls.some((c) => c.operationName?.startsWith("Request"))).toBe(false);
  expect(localStorage.length).toBe(0);
});
it.each([false, true])(
  "recovers an original creation before changing to inventory mode, receipt visible: %s",
  async (visible) => {
    let accepted: Record<string, unknown> | null = null;
    const h = harness((request) => {
      if (request.operationName === "GetNvkeRequest")
        return { astroliftNvkeRequest: visible ? accepted : null };
      if (request.operationName === "RequestNvkeOperationText") {
        accepted = {
          ...OPERATION,
          operationKey: values.operationKey,
          requestId: request.variables.input.requestId,
        };
        throw new Error("Lost creation reply");
      }
      throw new Error("Must retain original creation");
    });
    const { result } = renderHook(() => useNvkeInventory(SITE.id, COLLECTION.key, null), {
      wrapper: h.wrapper,
    });
    await waitFor(() => expect(result.current.catalogLoading).toBe(false));
    await act(async () => {
      expect(await result.current.onRequest(values)).toBe(false);
    });
    const original = readRecovery(localStorage, scope, SITE.id);
    await act(async () => {
      expect(
        await result.current.onRequest({
          ...values,
          operationKey: COLLECTION.key,
          parametersText: "{}",
        })
      ).toBe(visible);
    });
    expect(h.calls.filter((c) => c.operationName === "RequestNvkeOperationText")).toHaveLength(1);
    expect(h.calls.some((c) => c.operationName === "RequestNvkeInventory")).toBe(false);
    expect(readRecovery(localStorage, scope, SITE.id)).toEqual(visible ? null : original);
  }
);

it.each(["getClusterControlPlane", "getClusterControlPlaneConfigHistory", "getClusterSpireServer"])(
  "admits a fixed-source diagnostic read: %s",
  async (actionKey) => {
    const h = harness((request) => {
      if (request.operationName === "RequestNvkeResourceOperation")
        return {
          requestNvkeResourceOperation: {
            ok: true,
            errors: [],
            data: {
              ...OPERATION,
              operationKey: actionKey,
              requestId: request.variables.input.requestId,
              resourceSource: reviewedSource,
            },
          },
        };
      throw new Error("Unexpected diagnostic read");
    });
    const { result } = renderHook(() => useNvkeResourceActions(useResourceActionArgs()), {
      wrapper: h.wrapper,
    });
    await waitFor(() => expect(result.current?.readReady).toBe(true));
    await act(async () => {
      expect(await result.current!.onRead!(actionKey)).toBe(true);
    });
    expect(
      h.calls.find((c) => c.operationName === "RequestNvkeResourceOperation")!.variables.input
    ).toMatchObject({
      operationKey: actionKey,
      parametersText: "{}",
      sourceOperationId: reviewedSource.operationId,
      expectedSourceVersion: reviewedSource.version,
      sourceResourceKey: reviewedSource.resourceKey,
    });
    expect(h.calls.some((c) => c.operationName === "GetNvkeResourceEditor")).toBe(false);
    expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
    expect(h.cache.extract()).toEqual({});
  }
);
it("keeps bound reads available on a read-only native resource", async () => {
  const read = COLLECTION.actions.find((a) => a.key === "getClusterControlPlane")!;
  const h = harness(() => {
    throw new Error("No automatic reads");
  });
  const { result } = renderHook(
    () =>
      useNvkeResourceActions({
        ...useResourceActionArgs(),
        collection: { ...COLLECTION, actions: [COLLECTION.actions[0], read] },
      }),
    { wrapper: h.wrapper }
  );
  await waitFor(() => expect(result.current?.readReady).toBe(true));
  expect(result.current?.actions).toEqual([]);
  expect(result.current?.reads).toEqual([read]);
  expect(h.calls).toHaveLength(0);
});
it("prepares SPIRE and admits its edit against the successful bound SPIRE read", async () => {
  const preparedId = "123e4567-e89b-12d3-a456-426614174091";
  const prepared = { operationId: preparedId, version: 9, resourceKey: null };
  const text = '{"body":{"address":"spire.old.test","port":30440}}';
  const h = harness((request) => {
    if (request.operationName === "GetNvkeResourceEditor")
      return {
        astroliftNvkeResourceEditor: {
          ok: true,
          errors: [],
          data: {
            operationKey: "updateClusterSpireServer",
            source: request.variables.sourceOperationId === preparedId ? prepared : reviewedSource,
            parametersText: request.variables.sourceOperationId === preparedId ? text : null,
            preparationActionKey:
              request.variables.sourceOperationId === preparedId ? null : "getClusterSpireServer",
            concurrency: "observed_only",
          },
        },
      };
    if (request.operationName === "RequestNvkeResourceOperation")
      return {
        requestNvkeResourceOperation: {
          ok: true,
          errors: [],
          data: {
            ...OPERATION,
            id:
              request.variables.input.operationKey === "getClusterSpireServer"
                ? preparedId
                : OPERATION.id,
            operationKey: request.variables.input.operationKey,
            requestId: request.variables.input.requestId,
            resourceSource:
              request.variables.input.operationKey === "getClusterSpireServer"
                ? reviewedSource
                : prepared,
          },
        },
      };
    if (request.operationName === "GetNvkeOperation")
      return {
        astroliftNvkeOperation: {
          ...OPERATION,
          id: preparedId,
          version: prepared.version,
          status: "SUCCEEDED",
          operationKey: "getClusterSpireServer",
          resourceSource: reviewedSource,
        },
      };
    throw new Error("Unexpected SPIRE request");
  });
  const { result } = renderHook(() => useNvkeResourceActions(useResourceActionArgs()), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(result.current?.sourceReady).toBe(true));
  act(() => result.current!.onSelect("updateClusterSpireServer"));
  await act(async () => {
    expect(await result.current!.onLoad()).toBe(true);
  });
  expect(result.current?.editor?.preparationActionKey).toBe("getClusterSpireServer");
  await act(async () => {
    expect(await result.current!.onPrepare()).toBe(true);
  });
  expect(result.current?.editor?.source).toEqual(prepared);
  expect(result.current?.editor?.parametersText).toBe(text);
  await act(async () => {
    expect(
      await result.current!.onRequest({
        operationKey: "updateClusterSpireServer",
        timeoutSeconds: 7200,
        parametersText: '{"body":{"address":"spire.new.test","port":30441}}',
      })
    ).toBe(true);
  });
  const writes = h.calls.filter((c) => c.operationName === "RequestNvkeResourceOperation");
  expect(writes).toHaveLength(2);
  expect(writes[1].variables.input).toMatchObject({
    sourceOperationId: prepared.operationId,
    expectedSourceVersion: prepared.version,
    sourceResourceKey: null,
  });
  expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
});

it("restores SPIRE preparation recovery after a reload without issuing another request", async () => {
  let accepted: Record<string, unknown> | null = null;
  const preparedId = "123e4567-e89b-12d3-a456-426614174092";
  const h = harness((request) => {
    if (request.operationName === "GetNvkeResourceEditor")
      return {
        astroliftNvkeResourceEditor: {
          ok: true,
          errors: [],
          data: {
            operationKey: "updateClusterSpireServer",
            source: reviewedSource,
            parametersText: null,
            preparationActionKey: "getClusterSpireServer",
            concurrency: "observed_only",
          },
        },
      };
    if (request.operationName === "RequestNvkeResourceOperation") {
      accepted = {
        ...OPERATION,
        id: preparedId,
        operationKey: "getClusterSpireServer",
        requestId: request.variables.input.requestId,
        resourceSource: reviewedSource,
      };
      throw new Error("Lost preparation admission reply");
    }
    if (request.operationName === "GetNvkeRequest") return { astroliftNvkeRequest: accepted };
    throw new Error("No implicit native preparation retry");
  });
  const first = renderHook(() => useNvkeResourceActions(useResourceActionArgs()), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(first.result.current?.sourceReady).toBe(true));
  act(() => first.result.current!.onSelect("updateClusterSpireServer"));
  await act(async () => {
    await first.result.current!.onLoad();
  });
  await act(async () => {
    expect(await first.result.current!.onPrepare()).toBe(false);
  });
  first.unmount();
  const second = renderHook(() => useNvkeResourceActions(useResourceActionArgs()), {
    wrapper: h.wrapper,
  });
  await waitFor(() =>
    expect(second.result.current?.recovery?.operationKey).toBe("getClusterSpireServer")
  );
  expect(second.result.current?.selectedKey).toBe("updateClusterSpireServer");
  await act(async () => {
    expect(await second.result.current!.onRecover()).toBe(true);
  });
  expect(h.calls.filter((c) => c.operationName === "RequestNvkeResourceOperation")).toHaveLength(1);
  expect(navigation.push).toHaveBeenCalledWith(`/nvke/${SITE.id}/operations/${preparedId}`);
  expect(readRecovery(localStorage, scope, SITE.id)).toBeNull();
});

const diagnosticOperation = {
  ...OPERATION,
  operationKey: "getClusterSpireServer",
  status: "SUCCEEDED",
  resourceSource: {
    operationId: "123e4567-e89b-12d3-a456-426614174095",
    version: 7,
    resourceKey: "signed-resource",
  },
};
const diagnosticPage = {
  ...DIAGNOSTIC_OBSERVATION,
  items: [DIAGNOSTIC_ROW],
  nextCursor: null,
  totalCount: 1,
};
it("renders a successful bound diagnostic read through no-cache metadata queries", async () => {
  const h = harness((request) => {
    if (request.operationName === "GetNvkeOperation")
      return { astroliftNvkeOperation: diagnosticOperation };
    if (request.operationName === "GetNvkeDiagnosticSummary")
      return { astroliftNvkeDiagnosticPage: DIAGNOSTIC_OBSERVATION };
    if (request.operationName === "ListNvkeDiagnosticRows")
      return { astroliftNvkeDiagnosticPage: diagnosticPage };
    throw new Error("No native reads or private results on diagnostic view");
  });
  const { result } = renderHook(() => useNvkeOperation(SITE.id, OPERATION.id), {
    wrapper: h.wrapper,
  });
  await waitFor(() =>
    expect(result.current.diagnostics?.table.rows[0]?.name).toBe(DIAGNOSTIC_ROW.name)
  );
  expect(
    h.calls.find((c) => c.operationName === "ListNvkeDiagnosticRows")!.variables
  ).toMatchObject({
    expectedActorId: 41,
    expectedVersion: OPERATION.version,
    siteId: SITE.id,
    operationId: OPERATION.id,
    limit: 25,
  });
  expect(
    h.calls.some(
      (c) => c.operationName?.startsWith("Request") || c.operationName?.includes("ResultText")
    )
  ).toBe(false);
  expect(h.cache.extract()).toEqual({});
  expect(localStorage.length).toBe(0);
});
it.each(["RUNNING", "FAILED", "UNBOUND"])(
  "does not project incomplete or unbound native reads: %s",
  async (mode) => {
    const h = harness((request) =>
      request.operationName === "GetNvkeOperation"
        ? {
            astroliftNvkeOperation: {
              ...diagnosticOperation,
              status: mode === "UNBOUND" ? "SUCCEEDED" : mode,
              resourceSource: mode === "UNBOUND" ? null : diagnosticOperation.resourceSource,
            },
          }
        : (() => {
            throw new Error("Must not query diagnostics");
          })()
    );
    const { result } = renderHook(() => useNvkeOperation(SITE.id, OPERATION.id), {
      wrapper: h.wrapper,
    });
    await waitFor(() => expect(result.current.canAct).toBe(true));
    expect(result.current.diagnostics).toBeNull();
    expect(h.calls.some((c) => c.operationName?.includes("Diagnostic"))).toBe(false);
  }
);
it("rejects diagnostic pages belonging to another reviewed source version", async () => {
  const h = harness((request) => {
    if (request.operationName === "GetNvkeOperation")
      return { astroliftNvkeOperation: diagnosticOperation };
    return {
      astroliftNvkeDiagnosticPage:
        request.operationName === "GetNvkeDiagnosticSummary"
          ? DIAGNOSTIC_OBSERVATION
          : { ...diagnosticPage, operationVersion: OPERATION.version + 1 },
    };
  });
  const { result } = renderHook(() => useNvkeOperation(SITE.id, OPERATION.id), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(result.current.diagnostics?.error).toBe("readFailed"));
  expect(result.current.diagnostics?.table.rows).toEqual([]);
  expect(result.current.diagnostics?.table.state).toBe("error");
});
it("suppresses a late diagnostic summary across actor A to B to A", async () => {
  let finish!: (value: Record<string, unknown>) => void;
  let summaries = 0;
  const h = harness((request) => {
    if (request.operationName === "GetNvkeOperation")
      return { astroliftNvkeOperation: diagnosticOperation };
    if (request.operationName === "GetNvkeDiagnosticSummary") {
      if (++summaries === 1)
        return new Promise((resolve) => {
          finish = resolve;
        });
      return {
        astroliftNvkeDiagnosticPage: {
          ...DIAGNOSTIC_OBSERVATION,
          summary: { ...DIAGNOSTIC_OBSERVATION.summary, requestedAddress: "new-visit.test" },
        },
      };
    }
    return { astroliftNvkeDiagnosticPage: diagnosticPage };
  });
  const { result, rerender } = renderHook(() => useNvkeOperation(SITE.id, OPERATION.id), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  caller.actorId = 42;
  rerender();
  caller.actorId = 41;
  rerender();
  await waitFor(() =>
    expect(result.current.diagnostics?.observation?.summary.requestedAddress).toBe("new-visit.test")
  );
  await act(async () => {
    finish({ astroliftNvkeDiagnosticPage: DIAGNOSTIC_OBSERVATION });
  });
  expect(result.current.diagnostics?.observation?.summary.requestedAddress).toBe("new-visit.test");
  expect(h.cache.extract()).toEqual({});
});
