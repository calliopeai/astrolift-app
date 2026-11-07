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
import { ACTIONS, OPERATION, SITE } from "@/components/screens/nvke/fixtures";
import { nvkeScopeKey, readRecovery } from "@/lib/nvke/recovery";
import { useNvkeSite } from "./use-nvke-site";
import { useNvkeOperation } from "./use-nvke-operation";

const navigation = vi.hoisted(() => ({ push: vi.fn() }));
const caller = vi.hoisted(() => ({ actorId: 41 }));
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
        access: { read: true, manage: true, admin: true, loading: false },
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
  navigation.push.mockReset();
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: unknown, run: () => Promise<unknown>) => run(),
    },
  });
});
afterEach(() => vi.unstubAllGlobals());
function harness(
  respond: (operation: Operation) => Record<string, unknown> | Promise<Record<string, unknown>>
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
                    ...SITE,
                    createdAt: OPERATION.createdAt,
                    updatedAt: OPERATION.updatedAt,
                  },
                };
              if (operation.operationName === "GetNvkeContract")
                return {
                  astroliftNvkeContract: {
                    revision: SITE.upstreamRevision,
                    actions: ACTIONS,
                    controllerOpenapi: {},
                    resources: [],
                  },
                };
              if (operation.operationName === "ListNvkeOperations")
                return { astroliftNvkeOperations: { items: [], nextCursor: null, totalCount: 0 } };
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
