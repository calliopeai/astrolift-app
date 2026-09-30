import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useAppObservability } from "./use-app-observability";

const navigation = vi.hoisted(() => ({ search: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(navigation.search),
  usePathname: () => "/apps/web/logs-metrics",
  useRouter: () => ({ replace: navigation.replace }),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/components/observability/use-metric-scope-options", () => ({
  useMetricScopeOptions: () => ({}),
}));
vi.mock("@/components/observability/use-pod-resource-usage", () => ({
  usePodResourceUsage: () => ({}),
}));

const pods = ["web-first", "web-linked"].map((name) => ({
  name,
  namespace: "web",
  workload: "web",
  status: "Running",
  node: "node",
  restarts: 0,
  age: null,
  containerStatuses: ["web", "envoy"].map((container) => ({
    name: container,
    ready: true,
    restarts: 0,
    state: "running",
    reason: null,
    message: null,
  })),
}));

function source() {
  const requests: Array<{ name: string; variables: Record<string, unknown> }> = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push({ name: operation.operationName ?? "", variables: operation.variables });
          const data =
            operation.operationName === "ListAppPods"
              ? { astroliftAppPods: pods }
              : operation.operationName === "ListEvents"
                ? { astroliftEvents: [] }
                : operation.operationName === "GetApp"
                  ? { astroliftApp: null }
                  : operation.operationName === "ListManagedServices"
                    ? { astroliftManagedServices: [] }
                    : operation.operationName === "OnAppLog"
                      ? { astroliftOnAppLog: null }
                      : { astroliftOnAppLogs: null };
          queueMicrotask(() => {
            observer.next({ data });
            observer.complete();
          });
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  return { requests, client, wrapper };
}

describe("observability pod/container links", () => {
  it("uses the linked replica and sidecar for the actual stream, and honors later navigation", async () => {
    navigation.search = "section=metrics&panel=pods&pod=web-linked&container=envoy&env=prod";
    const server = source();
    const hook = renderHook(() => useAppObservability("web", "pods"), { wrapper: server.wrapper });
    await waitFor(() => expect(hook.result.current.selectedPod).toBe("web-linked"));
    expect(hook.result.current.selectedContainer).toBe("envoy");
    act(() => {
      hook.result.current.onToggleAllReplicas();
      hook.result.current.onToggleStreaming();
    });
    await waitFor(() =>
      expect(server.requests.some((request) => request.name === "OnAppLog")).toBe(true)
    );
    expect(server.requests.find((request) => request.name === "OnAppLog")?.variables).toMatchObject(
      { podName: "web-linked", container: "envoy" }
    );
    act(() => hook.result.current.onPickContainer("web"));
    expect(hook.result.current.selectedContainer).toBe("web");
    navigation.search = "panel=pods&pod=web-first&container=envoy";
    hook.rerender();
    expect(hook.result.current.selectedPod).toBe("web-first");
    expect(hook.result.current.selectedContainer).toBe("envoy");
    navigation.search = "panel=pods&pod=deleted&container=removed";
    hook.rerender();
    expect(hook.result.current.selectedPod).toBe("web-first");
    expect(hook.result.current.selectedContainer).toBe("web");
    hook.unmount();
    server.client.stop();
  });

  it("does not fetch a pod list or open a stream on inactive panels", async () => {
    navigation.search = "pod=web-linked&container=envoy";
    const server = source();
    const hook = renderHook(() => useAppObservability("web", "network"), {
      wrapper: server.wrapper,
    });
    await waitFor(() => expect(server.requests).toHaveLength(1));
    expect(server.requests[0].name).toBe("GetApp");
    expect(hook.result.current.selectedPod).toBeNull();
    expect(hook.result.current.selectedContainer).toBeNull();
    hook.unmount();
    server.client.stop();
  });
});
