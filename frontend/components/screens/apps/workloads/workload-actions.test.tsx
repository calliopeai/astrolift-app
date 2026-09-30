import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider, useQuery } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useWorkloadOps } from "../controls/use-controls-section";
import { WORKLOAD_WEB } from "../controls/app-controls.fixtures";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { useScaleWorkload } from "./use-scale-workload";
import { useWorkloadScaling } from "./use-workload-scaling";

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast }));
vi.mock("next-intl", () => ({
  useTranslations: (namespace: string) => (key: string, values?: { env?: string }) =>
    namespace === "apps.workloadActions"
      ? (
          {
            primary: "Primary environment",
            unavailable: "Current permissions unavailable",
            permissionDenied: "Permission denied",
            selectedEnvironmentUnsupported: "Selected environment unsupported",
          } as Record<string, string>
        )[key]
      : `${key}${values?.env ? `: ${values.env}` : ""}`,
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true }),
}));

const allowed = { allowed: true, code: "", reason: "" };
const denied = {
  allowed: false,
  code: "PERMISSION_DENIED",
  reason: "Sibling app is outside the grant",
};

function setup(options: { error?: string; failRefresh?: boolean; transportError?: boolean } = {}) {
  const requests: { name: string; variables: Record<string, unknown> }[] = [];
  let workload = { ...WORKLOAD_WEB, version: 7 };
  let reads = 0;
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push({ name: operation.operationName ?? "", variables: operation.variables });
          setTimeout(() => {
            const name = operation.operationName;
            if (name === "ListWorkloads") {
              reads += 1;
              if (reads > 1 && options.failRefresh) {
                observer.error(new Error("Refresh unavailable"));
                return;
              }
              observer.next({ data: { astroliftWorkloads: [workload] } });
            } else if (name === "GetWorkloadScalingStatus") {
              observer.next({ data: { astroliftWorkloadScalingStatus: null } });
            } else {
              if (options.transportError) {
                observer.error(new Error("Transport interrupted"));
                return;
              }
              if (options.error)
                workload = {
                  ...workload,
                  viewerCan: {
                    restart: { ...denied, reason: "Grant was revoked" },
                    scale: { ...denied, reason: "Grant was revoked" },
                  },
                };
              const field =
                name === "RestartAstroliftWorkload"
                  ? "restartAstroliftWorkload"
                  : "scaleAstroliftWorkload";
              observer.next({
                data: {
                  [field]: {
                    ok: !options.error,
                    errors: options.error
                      ? [{ code: options.error, message: "Grant was revoked", field: null }]
                      : [],
                    data: options.error
                      ? null
                      : {
                          workloadId: workload.id,
                          newRevision: null,
                          desiredReplicas: 4,
                          readyReplicas: 4,
                        },
                  },
                },
              });
            }
            observer.complete();
          }, 0);
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  function useLoadedOps() {
    const query = useQuery<{ astroliftWorkloads: AstroliftWorkload[] }>(LIST_WORKLOADS, {
      variables: { appSlug: "storefront" },
      fetchPolicy: "cache-and-network",
    });
    const fallback = { ...WORKLOAD_WEB, viewerCan: undefined } as unknown as AstroliftWorkload;
    return {
      ...useWorkloadOps((query.data ?? query.previousData)?.astroliftWorkloads[0] ?? fallback),
      loaded: Boolean(query.data),
    };
  }
  return { requests, wrapper, useLoadedOps, workload };
}

beforeEach(() => vi.clearAllMocks());

describe("primary-target workload actions", () => {
  it("keeps sibling denials despite a union app.deploy capability and refuses handlers", async () => {
    const { wrapper, requests } = setup();
    const workload = { ...WORKLOAD_WEB, viewerCan: { restart: denied, scale: denied } };
    const { result } = renderHook(() => useWorkloadOps(workload), { wrapper });
    await act(async () => {
      expect(await result.current.onRestart()).toBe(false);
      expect(await result.current.onApply(4)).toBe(false);
    });
    expect(requests).toEqual([]);
    expect(result.current.restartPermission.reason).toBe(denied.reason);
    expect(toast.error).toHaveBeenCalledWith(denied.reason);
  });

  it("checks restart and scale independently", async () => {
    const { wrapper, requests } = setup();
    const workload = { ...WORKLOAD_WEB, viewerCan: { restart: denied, scale: allowed } };
    const { result } = renderHook(() => useWorkloadOps(workload), { wrapper });
    await act(async () => {
      expect(await result.current.onRestart()).toBe(false);
      expect(await result.current.onApply(4)).toBe(true);
    });
    expect(requests.map((r) => r.name)).toEqual(["ScaleAstroliftWorkload"]);
  });

  it("sends the current workload version as a top-level argument without selected env claims", async () => {
    const { wrapper, requests, workload } = setup();
    const { result } = renderHook(() => useWorkloadOps(workload), { wrapper });
    await act(async () => {
      expect(await result.current.onRestart()).toBe(true);
      expect(await result.current.onApply(4)).toBe(true);
    });
    expect(requests.map((r) => r.variables)).toEqual([
      { input: { workloadId: workload.id }, ifMatchVersion: 7 },
      { input: { workloadId: workload.id, replicas: 4 }, ifMatchVersion: 7 },
    ]);
    expect(toast.success).toHaveBeenCalledWith("toastRestartIssued: Primary environment");
  });

  it.each(["PERMISSION_DENIED", "CONFLICT", "PRECONDITION"])(
    "refreshes actual authority after structured %s and refuses the next click",
    async (error) => {
      const { wrapper, requests, useLoadedOps } = setup({ error });
      const { result } = renderHook(useLoadedOps, { wrapper });
      await waitFor(() => expect(result.current.restartPermission.allowed).toBe(true));
      await act(async () => {
        expect(await result.current.onRestart()).toBe(false);
      });
      await waitFor(() => expect(result.current.restartPermission.allowed).toBe(false));
      expect(requests.filter((r) => r.name === "ListWorkloads")).toHaveLength(2);
      await act(async () => {
        expect(await result.current.onRestart()).toBe(false);
      });
      expect(requests.filter((r) => r.name === "RestartAstroliftWorkload")).toHaveLength(1);
      if (error === "PERMISSION_DENIED")
        expect(toast.error).toHaveBeenCalledWith("Permission denied", {
          description: "Grant was revoked",
        });
    }
  );

  it("keeps a stale allowed cache blocked when authority refresh fails", async () => {
    const { wrapper, requests, useLoadedOps } = setup({
      error: "PERMISSION_DENIED",
      failRefresh: true,
    });
    const { result } = renderHook(useLoadedOps, { wrapper });
    await waitFor(() => expect(result.current.restartPermission.allowed).toBe(true));
    await act(async () => {
      expect(await result.current.onRestart()).toBe(false);
    });
    expect(result.current.restartPermission.allowed).toBe(false);
    expect(toast.warning).toHaveBeenCalled();
    await act(async () => {
      expect(await result.current.onRestart()).toBe(false);
    });
    expect(requests.filter((r) => r.name === "RestartAstroliftWorkload")).toHaveLength(1);
  });

  it.each(["permission", "version"])(
    "disables inline scale when %s is unknown",
    async (missing) => {
      const { wrapper, requests } = setup();
      const workload = {
        ...WORKLOAD_WEB,
        ...(missing === "permission" ? { viewerCan: undefined } : { version: undefined }),
      } as unknown as AstroliftWorkload;
      const { result } = renderHook(() => useScaleWorkload(workload, 3), { wrapper });
      await act(async () => {
        expect(await result.current.apply("4")).toBe(false);
      });
      expect(requests).toEqual([]);
      expect(result.current.permission.allowed).toBe(false);
    }
  );

  it("preserves inline scale version preconditions", async () => {
    const { wrapper, requests, workload } = setup();
    const { result } = renderHook(() => useScaleWorkload(workload, 3), { wrapper });
    await act(async () => {
      expect(await result.current.apply("4")).toBe(true);
    });
    expect(requests[0].variables).toEqual({
      input: { workloadId: workload.id, replicas: 4 },
      ifMatchVersion: 7,
    });
  });

  it("refuses selected-environment scale even with an allowed primary decision", async () => {
    const { wrapper, requests } = setup();
    const { result } = renderHook(
      () =>
        useWorkloadScaling({
          workloadId: WORKLOAD_WEB.id,
          version: 7,
          appSlug: "storefront",
          workloadSlug: "web",
          environmentName: "production",
          permission: allowed,
        }),
      { wrapper }
    );
    await act(async () => {
      expect(await result.current.onApply(4)).toBe(false);
    });
    expect(result.current.canDeploy).toBe(false);
    expect(result.current.permissionReason).toBe("Selected environment unsupported");
    expect(requests.some((r) => r.name === "ScaleAstroliftWorkload")).toBe(false);
  });

  it("handles detail scale transport failures without reporting success", async () => {
    const { wrapper } = setup({ transportError: true });
    const { result } = renderHook(
      () =>
        useWorkloadScaling({
          workloadId: WORKLOAD_WEB.id,
          version: 7,
          appSlug: "storefront",
          workloadSlug: "web",
          environmentName: null,
          permission: allowed,
        }),
      { wrapper }
    );
    await act(async () => {
      expect(await result.current.onApply(4)).toBe(false);
    });
    expect(toast.error).toHaveBeenCalledWith("Transport interrupted");
    expect(toast.success).not.toHaveBeenCalled();
  });
});
