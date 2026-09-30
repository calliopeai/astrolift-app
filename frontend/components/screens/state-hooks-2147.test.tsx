import { act, renderHook, waitFor } from "@testing-library/react";
import { getOperationAST } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import * as React from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { useListState } from "@/components/list/use-list-state";
import { RUNS_LIST } from "./tasks/runs-list";
import { appDeploymentsList } from "./apps/deployments/app-deployments-list";
import {
  useAgentDiscoveryStep,
  type AgentDiscoveryFields,
} from "./agents/new/use-agent-discovery-step";
import {
  useDeregisterPending,
  recordDeregisterPending,
  clearDeregisterPending,
  DEREGISTER_GRACE_MS,
  deregisterPendingKey,
} from "./apps/overview/use-deregister-pending";
import { useCommandRunner } from "./apps/tools/use-command-runner";
import { useAppSecrets } from "./apps/secrets/use-app-secrets";
import type { AppSecret } from "./apps/secrets/secrets.types";
import { useProjectResources } from "./projects/use-project-resources";
import { RESOURCES } from "./projects/projects-detail.fixtures";
import { usePlayground } from "./playground/use-playground";
import { encodeShareHash, saveSession, type SavedSession } from "./playground/saved-sessions";
import { useLogin } from "./auth/use-login";

const mocks = vi.hoisted(() => ({
  scan: vi.fn(),
  mutate: vi.fn(),
  refetch: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn(), message: vi.fn() },
  search: "",
  queries: {} as Record<string, unknown>,
  variables: [] as { name: string; variables: Record<string, unknown> }[],
}));
vi.mock("sonner", () => ({ toast: mocks.toast }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: (url: string) => {
      mocks.search = new URL(url, "https://example.com").search;
    },
  }),
  useSearchParams: () => new URLSearchParams(mocks.search),
  usePathname: () => "/tasks",
}));
vi.mock("@/hooks/use-confirm", () => ({ useConfirm: () => vi.fn().mockResolvedValue(true) }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1" } }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));
vi.mock("@apollo/client/react", () => ({
  useMutation: () => [mocks.mutate, { loading: false }],
  useLazyQuery: () => [mocks.scan, { loading: false }],
  useQuery: (
    doc: Parameters<typeof getOperationAST>[0],
    options?: { variables?: Record<string, unknown> }
  ) => {
    const name = getOperationAST(doc)?.name?.value ?? "";
    mocks.variables.push({ name, variables: options?.variables ?? {} });
    const containers =
      options?.variables?.workloadSlug === "new"
        ? [{ id: "new-c", name: "new-container", isPrimary: true }]
        : [{ id: "old-c", name: "old-container", isPrimary: true }];
    return {
      data: name === "ListContainers" ? { astroliftContainers: containers } : mocks.queries[name],
      loading: false,
      refetch: mocks.refetch,
    };
  },
}));
function wrapper({ children }: React.PropsWithChildren) {
  return (
    <NextIntlClientProvider locale="en" messages={messages}>
      {children}
    </NextIntlClientProvider>
  );
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
const agent = (slug: string) => ({
  manifestPath: `${slug}.toml`,
  alreadyRegistered: false,
  name: slug,
  slug,
});
const initial: AgentDiscoveryFields = {
  sourceKind: "github",
  sourceRepo: "org/old",
  defaultBranch: "main",
  ref: "",
  scanned: false,
  scanError: null,
  discoveredAgents: [],
  selectedManifestPaths: [],
};
function result(agents: unknown[]) {
  return { data: { scanAgentManifests: { ok: true, error: null, agents } } };
}
const secret = {
  id: "secret-1",
  key: "TOKEN",
  source: "literal",
  environmentName: "production",
  bundleSlug: "",
  managedServiceKind: "",
  isMasked: true,
  scope: "all",
} satisfies AppSecret;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mocks.search = "";
  mocks.variables = [];
  mocks.queries = {};
  mocks.refetch.mockResolvedValue({ data: {} });
  mocks.mutate.mockResolvedValue({ data: {} });
  window.history.replaceState(null, "", "/playground");
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("browser state hydration", () => {
  it("hydrates shared playground and pending deregistration state after the server snapshot", async () => {
    const shared: SavedSession = {
      schema: 1,
      id: "shared-session",
      title: "Shared after hydration",
      model: "gpt-4o",
      messages: [],
      createdAt: "2026-09-29T00:00:00Z",
      updatedAt: "2026-09-29T00:00:00Z",
      starred: false,
    };
    window.history.replaceState(null, "", `/playground?mode=test${encodeShareHash(shared)}`);
    recordDeregisterPending("app", "workflow");
    function Probe() {
      const playground = usePlayground();
      const pending = useDeregisterPending("app");
      return (
        <p>
          {playground.title}|{pending.msRemaining === null ? "none" : "pending"}
        </p>
      );
    }
    const tree = (
      <NextIntlClientProvider locale="en" messages={messages}>
        <Probe />
      </NextIntlClientProvider>
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(tree);
    expect(container.textContent).not.toContain(shared.title);
    expect(container.textContent).toContain("none");
    document.body.append(container);
    const recover = vi.fn();
    let root!: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, tree, { onRecoverableError: recover });
    });
    expect(container.textContent).toBe(`${shared.title}|pending`);
    expect(recover).not.toHaveBeenCalled();
    expect(window.location.search).toBe("?mode=test");
    await act(async () => root.unmount());
    container.remove();
  });
});

describe("scan request ownership", () => {
  it("ignores an older auto-scan after a manual rescan completes", async () => {
    const old = deferred<ReturnType<typeof result>>();
    const fresh = deferred<ReturnType<typeof result>>();
    mocks.scan.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    const valid = vi.fn();
    const hook = renderHook(
      () => {
        const [state, setState] = React.useState(initial);
        return { state, discovery: useAgentDiscoveryStep(state, setState, valid) };
      },
      { wrapper }
    );
    act(() => hook.result.current.discovery.rescan());
    await act(async () => fresh.resolve(result([agent("fresh")])));
    await act(async () => old.resolve(result([agent("stale")])));
    expect(hook.result.current.state.selectedManifestPaths).toEqual(["fresh.toml"]);
    expect(hook.result.current.discovery.fetchState).toBe("found");
  });
  it("ignores results from a previous repository, including its late error", async () => {
    const old = deferred<ReturnType<typeof result>>();
    const fresh = deferred<ReturnType<typeof result>>();
    mocks.scan.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    const valid = vi.fn();
    const hook = renderHook(
      () => {
        const [state, setState] = React.useState(initial);
        return { state, setState, discovery: useAgentDiscoveryStep(state, setState, valid) };
      },
      { wrapper }
    );
    act(() =>
      hook.result.current.setState((s) => ({ ...s, sourceRepo: "org/new", scanned: false }))
    );
    await act(async () => fresh.resolve(result([agent("new-repo")])));
    await act(async () => old.reject(new Error("old repo failed")));
    expect(hook.result.current.state.selectedManifestPaths).toEqual(["new-repo.toml"]);
    expect(hook.result.current.state.scanError).toBeNull();
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });
  it("reports a current transport failure and lets the next scan retry", async () => {
    mocks.scan
      .mockRejectedValueOnce(new Error("Offline"))
      .mockResolvedValueOnce(result([agent("retried")]));
    const valid = vi.fn();
    const hook = renderHook(
      () => {
        const [state, setState] = React.useState(initial);
        return { state, discovery: useAgentDiscoveryStep(state, setState, valid) };
      },
      { wrapper }
    );
    await waitFor(() => expect(hook.result.current.discovery.fetchState).toBe("error"));
    act(() => hook.result.current.discovery.rescan());
    await waitFor(() => expect(hook.result.current.discovery.fetchState).toBe("found"));
    expect(hook.result.current.state.scanError).toBeNull();
  });
});

describe("storage grace state", () => {
  it("uses the new app's entry immediately and responds to same-tab clear", () => {
    recordDeregisterPending("first", "wf-first");
    recordDeregisterPending("second", "wf-second");
    const hook = renderHook(({ slug }) => useDeregisterPending(slug), {
      initialProps: { slug: "first" },
      wrapper,
    });
    expect(hook.result.current.msRemaining).toBeGreaterThan(0);
    hook.rerender({ slug: "second" });
    act(() => clearDeregisterPending("first"));
    expect(hook.result.current.msRemaining).toBeGreaterThan(0);
    act(() => clearDeregisterPending("second"));
    expect(hook.result.current.msRemaining).toBeNull();
  });
  it("expires the banner and removes its stored entry", () => {
    vi.useFakeTimers();
    recordDeregisterPending("api", "wf-api");
    const hook = renderHook(() => useDeregisterPending("api"), { wrapper });
    act(() => vi.advanceTimersByTime(DEREGISTER_GRACE_MS + 1000));
    expect(hook.result.current.msRemaining).toBeNull();
    expect(localStorage.getItem(deregisterPendingKey("api"))).toBeNull();
  });
  it("retains pending state on a failed cancellation, then clears it on retry success", async () => {
    recordDeregisterPending("api", "wf-api");
    mocks.mutate.mockRejectedValueOnce(new Error("Offline")).mockResolvedValueOnce({
      data: {
        cancelAstroliftDeregister: {
          ok: true,
          errors: [],
          data: { workflowId: "wf-api", signalDelivered: true },
        },
      },
    });
    const hook = renderHook(() => useDeregisterPending("api"), { wrapper });
    await act(() => hook.result.current.onCancel());
    expect(hook.result.current.msRemaining).toBeGreaterThan(0);
    await act(() => hook.result.current.onCancel());
    expect(hook.result.current.msRemaining).toBeNull();
  });
});

describe("separate selections", () => {
  it("chooses a valid new workload container before the next command", async () => {
    mocks.queries.ListWorkloads = {
      astroliftWorkloads: [
        { id: "old-w", slug: "old", isPublic: true },
        { id: "new-w", slug: "new" },
      ],
    };
    const hook = renderHook(() => useCommandRunner("api"), { wrapper });
    expect(hook.result.current.containerName).toBe("old-container");
    act(() => hook.result.current.onWorkloadChange("new"));
    expect(hook.result.current.containerName).toBe("new-container");
    expect(
      hook.result.current.containers.some((c) => c.name === hook.result.current.containerName)
    ).toBe(true);
  });
  it("leaves the resource cluster unchanged when a bundle chooses another cluster", async () => {
    mocks.queries.ListProjects = {
      astroliftProjects: [{ ...RESOURCES.project, organization: { id: "org-1" } }],
    };
    mocks.queries.ListProjectResources = { astroliftProjectResourceClusters: RESOURCES.clusters };
    const hook = renderHook(() => useProjectResources(RESOURCES.slug), { wrapper });
    const resourceCluster = hook.result.current.effectiveClusterId;
    act(() => hook.result.current.onBundleClusterChange("bundle-cluster"));
    expect(hook.result.current.effectiveClusterId).toBe(resourceCluster);
    expect(hook.result.current.effectiveBundleClusterId).toBe("bundle-cluster");
    mocks.mutate.mockResolvedValue({
      data: { createProjectSecretBundle: { ok: true, errors: [], data: { id: "bundle-1" } } },
    });
    await act(() => hook.result.current.onCreateBundle("Bundle", "bundle"));
    expect(mocks.mutate.mock.calls[0][0].variables.input.clusterId).toBe("bundle-cluster");
  });
  it("hiding a revealed literal exits both edit and rotate modes", async () => {
    mocks.mutate.mockResolvedValue({
      data: { revealAppSecret: { ok: true, errors: [], data: { value: "plaintext" } } },
    });
    const hook = renderHook(() => useAppSecrets("api"), { wrapper });
    await act(() => hook.result.current.onToggleReveal(secret));
    act(() => hook.result.current.onStartRotate(secret));
    expect(hook.result.current.rotatingId).toBe(secret.id);
    await act(() => hook.result.current.onToggleReveal(secret));
    expect(hook.result.current.editingId).toBeNull();
    expect(hook.result.current.rotatingId).toBeNull();
  });
});

describe("hydrated playground and live URL filters", () => {
  it("hydrates saved and shared sessions once while keeping the URL query", () => {
    const saved = saveSession({ id: "saved-1", title: "Saved", model: "Genesis", messages: [] });
    const shared = {
      ...saved,
      title: "Shared",
      model: "Explorer",
      messages: [{ role: "user" as const, content: "Shared message" }],
    };
    window.history.replaceState(null, "", `/playground?mode=test${encodeShareHash(shared)}`);
    const hook = renderHook(usePlayground);
    expect(hook.result.current.savedSessions).toHaveLength(1);
    expect(hook.result.current.title).toBe("Shared");
    expect(hook.result.current.messages).toEqual(shared.messages);
    expect(window.location.search).toBe("?mode=test");
    expect(window.location.hash).toBe("");
    act(() => hook.result.current.setTitle("Local edit"));
    hook.rerender();
    expect(hook.result.current.title).toBe("Local edit");
  });
  it("task status follows back/forward URL snapshots instead of a mount-only filter", () => {
    mocks.search = "status=failed";
    const hook = renderHook(() => useListState(RUNS_LIST));
    expect(hook.result.current.filters.status).toBe("failed");
    mocks.search = "status=completed";
    hook.rerender();
    expect(hook.result.current.filters.status).toBe("completed");
    mocks.search = "status=failed";
    hook.rerender();
    expect(hook.result.current.filters.status).toBe("failed");
  });
  it("deployment cursor resets through the URL filter action", () => {
    mocks.search = "after=old-page";
    const hook = renderHook(() =>
      useListState(appDeploymentsList(["production"], { previews: true }))
    );
    expect(hook.result.current.state.after).toBe("old-page");
    act(() => hook.result.current.setFilter("env", "production"));
    hook.rerender();
    expect(hook.result.current.state.after).toBeNull();
  });
  it("reads the login return target when submitting, after the URL changed", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 403, json: async () => ({ detail: "Denied" }) });
    vi.stubGlobal("fetch", fetch);
    const hook = renderHook(useLogin);
    window.history.replaceState(null, "", "/login?next=%2Fapp%2Fcli%2Fauth%2Fdevice%2Fnew-code%2F");
    await act(() => hook.result.current.submitLocal("user", "password"));
    const [, options] = fetch.mock.calls.find(([url]) => String(url).endsWith("local-login"))!;
    expect(JSON.parse(options.body).next).toBe("/app/cli/auth/device/new-code/");
  });
});
