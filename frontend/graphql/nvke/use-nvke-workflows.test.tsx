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
import type { ReactNode } from "react";
import { SITE, OPERATION, MAINTENANCE_OBSERVATION } from "@/components/screens/nvke/fixtures";
import { TEMPORAL_AUTHORITY } from "@/components/screens/nvke/NvkeTemporalPanel.stories";
import { WORKFLOW_HISTORY } from "@/components/screens/nvke/NvkeWorkflowPanel.stories";
import type { GetNvkeContractQuery } from "@/graphql/__generated__/operations";
import { readRecovery } from "@/lib/nvke/recovery";
import type { NvkeNativeValues } from "@/lib/nvke/native-admission";
import { useNvkeWorkflows } from "./use-nvke-workflows";
const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
const schemaText =
  '{"type":"object","properties":{"thresholdDays":{"maximum":9223372036854775807}}}';
const contract: GetNvkeContractQuery["astroliftNvkeContract"] = {
  revision: SITE.upstreamRevision,
  actions: [],
  collections: [],
  resources: [],
  controllerOpenapi: {},
  nativeWorkflows: ["RenewK8sCertsV2", "RotateEtcdEncryptionKey", "RetireLostEtcdRotationNode"].map(
    (name) => ({
      name,
      inputType: name + "Input",
      source: "pinned.go",
      permission: "nvke.admin",
      inputSchema: { type: "object" },
      inputSchemaText: schemaText,
    })
  ),
};
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
      access: { read: true, admin: true, manage: false, loading: false },
      loading: false,
      failed: false,
    },
    scopeKey: JSON.stringify([scope, "visit"]),
    site: SITE,
    operation: { ...OPERATION, operationKey: "getCluster", status: "SUCCEEDED" as const },
    contract,
    maintenance: {
      observation: {
        ...MAINTENANCE_OBSERVATION,
        operationId: OPERATION.id,
        operationVersion: OPERATION.version,
        sourceSiteVersion: SITE.version,
        renewal: { ...MAINTENANCE_OBSERVATION.renewal!, workflowId: "renewal-owner" },
        etcdKeyRotation: {
          workflowId: WORKFLOW_HISTORY.workflowId,
          state: "failed",
          phase: "rewrite",
          startedAt: null,
          lastProgressAt: null,
          completedAt: null,
          failureReason: "operation_failed",
          retainedMutationLock: true,
        },
      },
      loading: false,
      error: null as string | null,
      onRefresh: vi.fn(),
    },
    enabled: true,
    onRefresh: vi.fn(),
  };
}
type Respond = (request: Operation) => Record<string, unknown> | Promise<Record<string, unknown>>;
function harness(
  respond?: Respond,
  temporal: () => typeof TEMPORAL_AUTHORITY | null = () => TEMPORAL_AUTHORITY
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
            .then(() => {
              if (request.operationName === "GetNvkeTemporal")
                return { astroliftNvkeTemporalAuthority: temporal() };
              if (request.operationName === "GetNvkeNativeHistory") {
                const page = {
                  ...WORKFLOW_HISTORY,
                  workflow: request.variables.workflow,
                  workflowId:
                    request.variables.workflow === "RenewK8sCertsV2"
                      ? "renewal-owner"
                      : WORKFLOW_HISTORY.workflowId,
                  items: WORKFLOW_HISTORY.items.slice(0, request.variables.limit),
                };
                return (
                  respond?.(request) ?? {
                    astroliftNvkeNativeHistory: { ok: true, errors: [], data: page },
                  }
                );
              }
              return respond?.(request) ?? { astroliftNvkeRequest: null };
            })
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
const values: NvkeNativeValues = {
  mode: "workflow",
  workflow: "RenewK8sCertsV2",
  parametersText:
    '{ "clusterID":"' +
    WORKFLOW_HISTORY.clusterId +
    '", "thresholdDays":9007199254740993, "force":true }',
  acknowledgeLostNodeTeardown: false,
  timeoutSeconds: 7200,
};
const accepted = (input: Record<string, unknown>, key = "temporal.RenewK8sCertsV2") => ({
  ...OPERATION,
  requestId: input.requestId,
  operationKey: key,
  isInventory: false,
  resourceSource: null,
});
beforeEach(() => {
  localStorage.clear();
  navigation.push.mockReset();
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: unknown, run: () => Promise<unknown>) => run(),
    },
  });
});
afterEach(() => vi.unstubAllGlobals());
it("adopts exact schema text and admits reviewed source/authority under no-cache scope", async () => {
  const a = args();
  const h = harness((request) =>
    request.operationName === "RequestNvkeNativeWorkflow"
      ? {
          requestNvkeNativeWorkflow: {
            ok: true,
            errors: [],
            data: accepted(request.variables.input),
          },
        }
      : { astroliftNvkeRequest: null }
  );
  const { result } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  expect(result.current?.workflows[0].schemaText).toBe(schemaText);
  expect(h.calls.some((call) => call.operationName === "GetNvkeNativeHistory")).toBe(false);
  await act(async () => {
    expect(await result.current!.onRequest(values)).toBe(true);
  });
  const request = h.calls.find((call) => call.operationName === "RequestNvkeNativeWorkflow")!;
  expect(request.variables.input).toMatchObject({
    expectedActorId: 41,
    expectedSiteVersion: SITE.version,
    expectedAuthorityVersion: TEMPORAL_AUTHORITY.version,
    sourceOperationId: OPERATION.id,
    expectedSourceVersion: OPERATION.version,
    parametersText: values.mode === "workflow" ? values.parametersText : "",
  });
  expect(request.variables.input).not.toHaveProperty("mode");
  expect(request.getContext()).toMatchObject({
    redactDiagnostics: true,
    expectedNvkeOrganization: SITE.id,
  });
  expect(navigation.push).toHaveBeenCalledWith(`/nvke/${SITE.id}/operations/${OPERATION.id}`);
  expect(h.cache.extract()).toEqual({});
  expect(Object.values(localStorage).join()).not.toContain("thresholdDays");
  h.client.stop();
});
it("fetches history only on explicit inspection and binds paged rows to the inspected run", async () => {
  const a = args();
  let wrong = false;
  const h = harness((request) =>
    request.operationName === "GetNvkeNativeHistory"
      ? {
          astroliftNvkeNativeHistory: {
            ok: true,
            errors: [],
            data: {
              ...WORKFLOW_HISTORY,
              runId: wrong && request.variables.limit !== 1 ? SITE.id : WORKFLOW_HISTORY.runId,
              items: WORKFLOW_HISTORY.items.slice(0, request.variables.limit),
            },
          },
        }
      : { astroliftNvkeRequest: null }
  );
  const { result } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canInspect).toBe(true));
  act(() => result.current!.onHistory("RotateEtcdEncryptionKey"));
  await waitFor(() => expect(result.current?.historyTable.state).toBe("ready"));
  expect(result.current?.retirementEligible).toBe(true);
  expect(result.current?.history?.runId).toBe(WORKFLOW_HISTORY.runId);
  wrong = true;
  act(() => result.current!.historyTable.refetch());
  await waitFor(() => expect(result.current?.historyError).toBe("readFailed"));
  expect(result.current?.historyTable.rows).toEqual([]);
  expect(result.current?.retirementEligible).toBe(false);
  h.client.stop();
});
it("lost replies recover the original receipt without private input or another admission", async () => {
  let row: ReturnType<typeof accepted> | null = null;
  const h = harness((request) => {
    if (request.operationName === "RequestNvkeNativeWorkflow") {
      row = accepted(request.variables.input);
      throw Error("PRIVATE_LOST_REPLY");
    }
    return { astroliftNvkeRequest: row };
  });
  const a = args();
  const { result } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  await act(async () => {
    expect(await result.current!.onRequest(values)).toBe(false);
  });
  expect(result.current?.error).toBe("requestFailed");
  expect(result.current?.recovery).not.toBeNull();
  await act(async () => {
    expect(await result.current!.onRecover()).toBe(true);
  });
  expect(h.calls.filter((call) => call.operationName === "RequestNvkeNativeWorkflow")).toHaveLength(
    1
  );
  expect(navigation.push).toHaveBeenCalledOnce();
  expect(readRecovery(localStorage, a.context.scope, SITE.id)).toBeNull();
  expect(JSON.stringify(localStorage)).not.toContain("PRIVATE");
  h.client.stop();
});
it.each(["PRECONDITION", "RESOURCE_BUSY", "UNSUPPORTED"])(
  "releases a definitively rejected first %s intent",
  async (code) => {
    const h = harness((request) =>
      request.operationName === "RequestNvkeNativeWorkflow"
        ? { requestNvkeNativeWorkflow: { ok: false, errors: [{ code }], data: null } }
        : { astroliftNvkeRequest: null }
    );
    const a = args();
    const { result } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
    await waitFor(() => expect(result.current?.canManage).toBe(true));
    await act(async () => {
      expect(await result.current!.onRequest(values)).toBe(false);
    });
    expect(result.current?.error).toBe("requestRejected");
    expect(readRecovery(localStorage, a.context.scope, SITE.id)).toBeNull();
    h.client.stop();
  }
);
it("does not publish a late mutation after actor A to B to A", async () => {
  let release!: (data: Record<string, unknown>) => void;
  let input!: Record<string, unknown>;
  const late = new Promise<Record<string, unknown>>((resolve) => {
    release = resolve;
  });
  const h = harness((request) => {
    if (request.operationName === "RequestNvkeNativeWorkflow") {
      input = request.variables.input;
      return late;
    }
    return { astroliftNvkeRequest: null };
  });
  let a = args();
  const original = a.context.scope;
  const { result, rerender } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  let pending!: Promise<boolean>;
  act(() => {
    pending = result.current!.onRequest(values);
  });
  await waitFor(() => expect(input).toBeDefined());
  a = args(42);
  rerender();
  a = args(41);
  rerender();
  await act(async () => {
    release({ requestNvkeNativeWorkflow: { ok: true, errors: [], data: accepted(input) } });
    expect(await pending).toBe(false);
  });
  expect(navigation.push).not.toHaveBeenCalled();
  expect(readRecovery(localStorage, original, SITE.id)).not.toBeNull();
  h.client.stop();
});
it("stale source site versions block native inspection and admission", async () => {
  const a = args();
  a.maintenance.observation.sourceSiteVersion = SITE.version - 1;
  const h = harness();
  const { result } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.loading).toBe(false));
  expect(result.current?.canManage).toBe(false);
  expect(result.current?.canInspect).toBe(false);
  await act(async () => {
    expect(await result.current!.onRequest(values)).toBe(false);
    result.current!.onHistory("RotateEtcdEncryptionKey");
  });
  expect(
    h.calls.some(
      (call) =>
        call.operationName?.startsWith("Request") || call.operationName === "GetNvkeNativeHistory"
    )
  ).toBe(false);
  expect(localStorage.length).toBe(0);
  h.client.stop();
});
it("storage failure blocks writes without preventing permitted history reads", async () => {
  vi.stubGlobal("navigator", {});
  const h = harness();
  const { result } = renderHook(() => useNvkeWorkflows(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.error).toBe("storageRequired"));
  expect(result.current?.canManage).toBe(false);
  expect(result.current?.canInspect).toBe(true);
  act(() => result.current!.onHistory("RotateEtcdEncryptionKey"));
  await waitFor(() => expect(result.current?.historyTable.state).toBe("ready"));
  h.client.stop();
});

it("submits a selected reset with flattened exact event strings and no private cache data", async () => {
  const h = harness((request) =>
    request.operationName === "RequestNvkeNativeReset"
      ? {
          requestNvkeNativeReset: {
            ok: true,
            errors: [],
            data: accepted(request.variables.input, "temporal.reset.RotateEtcdEncryptionKey"),
          },
        }
      : request.operationName === "GetNvkeNativeHistory"
        ? {
            astroliftNvkeNativeHistory: {
              ok: true,
              errors: [],
              data: {
                ...WORKFLOW_HISTORY,
                items: WORKFLOW_HISTORY.items.slice(0, request.variables.limit),
              },
            },
          }
        : { astroliftNvkeRequest: null }
  );
  const { result } = renderHook(() => useNvkeWorkflows(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canInspect).toBe(true));
  act(() => result.current!.onHistory("RotateEtcdEncryptionKey"));
  await waitFor(() => expect(result.current?.historyTable.state).toBe("ready"));
  const reset: NvkeNativeValues = {
    mode: "reset",
    timeoutSeconds: 7200,
    review: {
      workflowId: WORKFLOW_HISTORY.workflowId,
      baseRunId: WORKFLOW_HISTORY.runId,
      workflowTaskEventId: "9007199254740992",
      activityEventId: "9007199254740993",
      activityType: "StartEtcdEncryptionRewrite",
      phase: "rewrite",
      mode: "restart_rewrite",
      rationale: "PRIVATE_RESET_REASON",
      quiescenceEvidence: "PRIVATE_NODE_EVIDENCE",
      acknowledgeBoundaryReviewed: true,
      acknowledgeConflictingWorkStopped: true,
      retirementWorkflowId: "",
    },
  };
  await act(async () => {
    expect(await result.current!.onRequest(reset)).toBe(true);
  });
  const mutation = h.calls.find((request) => request.operationName === "RequestNvkeNativeReset")!;
  expect(mutation.variables.input).toMatchObject({
    workflowTaskEventId: "9007199254740992",
    activityEventId: "9007199254740993",
    mode: "restart_rewrite",
    rationale: "PRIVATE_RESET_REASON",
  });
  expect(mutation.variables.input).not.toHaveProperty("review");
  expect(h.cache.extract()).toEqual({});
  expect(JSON.stringify(localStorage)).not.toContain("PRIVATE");
  h.client.stop();
});
it("receipt lookup remains available without native authority or administrator capability", async () => {
  let authority: typeof TEMPORAL_AUTHORITY | null = TEMPORAL_AUTHORITY;
  let row: ReturnType<typeof accepted> | null = null;
  const h = harness(
    (request) => {
      if (request.operationName === "RequestNvkeNativeWorkflow") {
        row = accepted(request.variables.input);
        throw Error("LOST");
      }
      return { astroliftNvkeRequest: row };
    },
    () => authority
  );
  let a = args();
  const { result, rerender } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  await act(async () => {
    expect(await result.current!.onRequest(values)).toBe(false);
  });
  authority = null;
  a = { ...a, context: { ...a.context, access: { ...a.context.access, admin: false } } };
  rerender();
  await waitFor(() => expect(result.current?.canRecover).toBe(true));
  expect(result.current?.canManage).toBe(false);
  await act(async () => {
    expect(await result.current!.onRecover()).toBe(true);
  });
  expect(
    h.calls.filter((request) => request.operationName === "RequestNvkeNativeWorkflow")
  ).toHaveLength(1);
  expect(readRecovery(localStorage, a.context.scope, SITE.id)).toBeNull();
  h.client.stop();
});
it("a missing receipt field cannot be treated as an absent original request", async () => {
  let incomplete = false;
  const h = harness((request) => {
    if (request.operationName === "RequestNvkeNativeWorkflow") throw Error("LOST");
    return incomplete ? {} : { astroliftNvkeRequest: null };
  });
  const a = args();
  const { result } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  await act(async () => {
    expect(await result.current!.onRequest(values)).toBe(false);
  });
  const original = readRecovery(localStorage, a.context.scope, SITE.id);
  incomplete = true;
  await act(async () => {
    expect(await result.current!.onRequest(values)).toBe(false);
  });
  expect(
    h.calls.filter((request) => request.operationName === "RequestNvkeNativeWorkflow")
  ).toHaveLength(1);
  expect(readRecovery(localStorage, a.context.scope, SITE.id)).toEqual(original);
  h.client.stop();
});
it("read-only actors do not inspect native history or admit workflows", async () => {
  const a = args();
  a.context.access.admin = false;
  const h = harness();
  const { result } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.loading).toBe(false));
  act(() => result.current!.onHistory("RotateEtcdEncryptionKey"));
  await act(async () => {
    expect(await result.current!.onRequest(values)).toBe(false);
  });
  expect(
    h.calls.some(
      (request) =>
        request.operationName === "GetNvkeNativeHistory" ||
        request.operationName?.startsWith("Request")
    )
  ).toBe(false);
  h.client.stop();
});
it("uninspected retirement is refused before creating a request journal", async () => {
  const h = harness();
  const a = args();
  const { result } = renderHook(() => useNvkeWorkflows(a), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.canManage).toBe(true));
  expect(result.current?.retirementEligible).toBe(false);
  const retirement: NvkeNativeValues = {
    mode: "workflow",
    workflow: "RetireLostEtcdRotationNode",
    parametersText: JSON.stringify({
      clusterID: WORKFLOW_HISTORY.clusterId,
      nodeID: SITE.id,
      rotationWorkflowID: WORKFLOW_HISTORY.workflowId,
    }),
    acknowledgeLostNodeTeardown: true,
    timeoutSeconds: 7200,
  };
  await act(async () => {
    expect(await result.current!.onRequest(retirement)).toBe(false);
  });
  expect(readRecovery(localStorage, a.context.scope, SITE.id)).toBeNull();
  expect(h.calls.some((request) => request.operationName === "RequestNvkeNativeWorkflow")).toBe(
    false
  );
  h.client.stop();
});
