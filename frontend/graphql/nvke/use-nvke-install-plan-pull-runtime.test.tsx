import {
  ApolloClient,
  ApolloLink,
  InMemoryCache,
  Observable,
  type Operation,
} from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  INSTALL_PULL_RUNTIME_PROPS as fixture,
  PULL_RUNTIME_RESULT,
} from "@/components/screens/nvke/NvkeOperatorInstallPlanPullRuntimePanel.stories";
import { INSTALL_PULL_SECRET_ANALYSIS_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullSecretAnalysisPanel.stories";
import { INSTALL_PULL_AUTHORITY_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullAuthorityPanel.stories";
import { INSTALL_NODE_PLATFORMS_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanNodePlatformsPanel.stories";
import { INSTALL_TREE_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanTreePanel.stories";
import { INSTALL_WORKLOAD_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanWorkloadPanel.stories";
import { INSTALL_IMAGE_BINDING_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanImageBindingPanel.stories";
import { useNvkeInstallPlanPullRuntime } from "./use-nvke-install-plan-pull-runtime";

const args = () => ({
  context: {
    scope: {
      server: "https://astro.example.test/app/gql/config/",
      organizationId: fixture.site.id,
      actorId: 41,
    },
    scopeKey: "caller-visit",
    access: { read: true, manage: false, admin: true, loading: false },
    loading: false,
    failed: false,
  },
  source: { ...INSTALL_TREE_PROPS },
  workload: { ...INSTALL_WORKLOAD_PROPS },
  imageBinding: { ...INSTALL_IMAGE_BINDING_PROPS },
  pullAuthority: { ...INSTALL_PULL_AUTHORITY_PROPS },
  analysis: { ...INSTALL_PULL_SECRET_ANALYSIS_PROPS },
  nodePlatforms: { ...INSTALL_NODE_PLATFORMS_PROPS },
  pending: false,
});
const envelope = (value: unknown = PULL_RUNTIME_RESULT) => ({
  reviewNvkeOperatorInstallPlanPullRuntime: { ok: true, errors: [], data: value },
});
function peer(reply: (operation: Operation) => object | Promise<object> = () => envelope()) {
  const calls: Operation[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          calls.push(operation);
          Promise.resolve()
            .then(() => reply(operation))
            .then(
              (data) => {
                observer.next({ data });
                observer.complete();
              },
              () => observer.error(new Error("PRIVATE_TRANSPORT_DETAILS"))
            );
        })
    ),
  });
  return {
    client,
    calls,
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <ApolloProvider client={client}>{children}</ApolloProvider>
    ),
  };
}
beforeEach(() => {
  vi.stubGlobal("navigator", { locks: { request: vi.fn((_key, _options, run) => run()) } });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

it("reads explicitly with exact private index digests and leaves results out of Apollo cache", async () => {
  const p = peer(),
    hook = renderHook(() => useNvkeInstallPlanPullRuntime(args()), { wrapper: p.wrapper });
  expect(p.calls).toHaveLength(0);
  await act(async () => {
    expect(await hook.result.current!.onReview()).toBe(true);
  });
  expect(p.calls).toHaveLength(1);
  const op = p.calls[0];
  expect(op.operationName).toBe("ReviewNvkeInstallPlanPullRuntime");
  expect(op.variables.input).toMatchObject({
    expectedActorId: 41,
    analysisId: fixture.analysis!.id,
    expectedAnalysisVersion: fixture.analysis!.version,
    analysisDigest: fixture.analysis!.authorityDigest,
    nodePlatformId: fixture.nodePlatforms!.id,
    expectedNodePlatformVersion: fixture.nodePlatforms!.version,
    nodePlatformDigest: fixture.nodePlatforms!.platformDigest,
  });
  expect(op.variables.input).not.toHaveProperty("requestText");
  expect(op.variables.input.analysisDigest).not.toBe(fixture.analysis!.artifactDigest);
  expect(op.getContext()).toMatchObject({
    expectedNvkeOrganization: fixture.site.id,
    redactDiagnostics: true,
    queryDeduplication: false,
  });
  expect(hook.result.current!.correspondence).toEqual(PULL_RUNTIME_RESULT);
  expect(JSON.stringify(p.client.cache.extract())).not.toContain(
    PULL_RUNTIME_RESULT.correspondenceDigest
  );
});
it.each([
  "analysis",
  "nodePlatforms",
  "pullAuthority",
  "imageBinding",
  "workload",
  "source",
] as const)("requires available %s metadata before I/O", async (parent) => {
  const p = peer(),
    values = args();
  values[parent].canReview = false;
  const hook = renderHook(() => useNvkeInstallPlanPullRuntime(values), { wrapper: p.wrapper });
  await act(async () => {
    expect(await hook.result.current!.onReview()).toBe(false);
  });
  expect(p.calls).toHaveLength(0);
});
it("refuses changed authority pins before I/O", async () => {
  const p = peer(),
    values = args();
  values.source.authority = { ...values.source.authority!, version: 99 };
  const hook = renderHook(() => useNvkeInstallPlanPullRuntime(values), { wrapper: p.wrapper });
  await act(async () => {
    expect(await hook.result.current!.onReview()).toBe(false);
  });
  expect(p.calls).toHaveLength(0);
});
it.each([
  "actor",
  "server",
  "organization",
  "analysis",
  "node",
  "authority",
  "permission",
  "pending",
])("discards a late response after %s changes", async (change) => {
  let finish!: (value: object) => void;
  const p = peer(
    () =>
      new Promise<object>((resolve) => {
        finish = resolve;
      })
  );
  const values = args(),
    hook = renderHook((input) => useNvkeInstallPlanPullRuntime(input), {
      initialProps: values,
      wrapper: p.wrapper,
    });
  let read!: Promise<boolean>;
  await act(async () => {
    read = hook.result.current!.onReview();
    await Promise.resolve();
  });
  expect(p.calls).toHaveLength(1);
  const next = args();
  if (change === "actor") next.context.scope.actorId++;
  if (change === "server") next.context.scope.server = "https://other.example.test/app/gql/config/";
  if (change === "organization") next.context.scope.organizationId = "other-org";
  if (change === "analysis") next.analysis.artifacts = { ...next.analysis.artifacts!, version: 99 };
  if (change === "node")
    next.nodePlatforms.artifacts = { ...next.nodePlatforms.artifacts!, version: 99 };
  if (change === "authority") next.source.authority = { ...next.source.authority!, version: 99 };
  if (change === "permission") next.context.access.admin = false;
  if (change === "pending") next.pending = true;
  hook.rerender(next);
  await act(async () => {
    finish(envelope());
    expect(await read).toBe(false);
  });
  expect(hook.result.current!.correspondence).toBeNull();
  expect(p.calls).toHaveLength(1);
});
it("does not restore a result when returning to an earlier source visit", async () => {
  const p = peer(),
    values = args(),
    hook = renderHook((input) => useNvkeInstallPlanPullRuntime(input), {
      initialProps: values,
      wrapper: p.wrapper,
    });
  await act(async () => {
    await hook.result.current!.onReview();
  });
  expect(hook.result.current!.correspondence).not.toBeNull();
  hook.rerender({ ...values, context: { ...values.context, scopeKey: "other-visit" } });
  hook.rerender(values);
  expect(hook.result.current!.correspondence).toBeNull();
  expect(p.calls).toHaveLength(1);
});
it("never retries a lost read or stores private transport details", async () => {
  const p = peer(() => {
    throw new Error("PRIVATE_TRANSPORT_DETAILS");
  });
  const hook = renderHook(() => useNvkeInstallPlanPullRuntime(args()), { wrapper: p.wrapper });
  await act(async () => {
    expect(await hook.result.current!.onReview()).toBe(false);
  });
  expect(p.calls).toHaveLength(1);
  expect(hook.result.current!.error).toBe("UNCONFIRMED");
  expect(hook.result.current!.correspondence).toBeNull();
});
it("does not issue a queued read after its source changes", async () => {
  let enter!: () => Promise<boolean>;
  vi.stubGlobal("navigator", {
    locks: {
      request: vi.fn(
        (_key, _options, run) =>
          new Promise((resolve) => {
            enter = async () => {
              const value = await run();
              resolve(value);
              return value;
            };
          })
      ),
    },
  });
  const p = peer(),
    values = args(),
    hook = renderHook((input) => useNvkeInstallPlanPullRuntime(input), {
      initialProps: values,
      wrapper: p.wrapper,
    });
  let read!: Promise<boolean>;
  await act(async () => {
    read = hook.result.current!.onReview();
    await Promise.resolve();
  });
  hook.rerender({ ...values, context: { ...values.context, scopeKey: "other-visit" } });
  await act(async () => {
    await enter();
    expect(await read).toBe(false);
  });
  expect(p.calls).toHaveLength(0);
});
it("rejects changed or continuation-bearing responses before publishing", async () => {
  for (const change of [
    { continuationAuthorized: true },
    { nodeCount: 99 },
    { analysis: { ...PULL_RUNTIME_RESULT.analysis, version: 99 } },
  ]) {
    const p = peer(() => envelope({ ...PULL_RUNTIME_RESULT, ...change }));
    const hook = renderHook(() => useNvkeInstallPlanPullRuntime(args()), { wrapper: p.wrapper });
    await act(async () => {
      expect(await hook.result.current!.onReview()).toBe(false);
    });
    expect(hook.result.current!.correspondence).toBeNull();
    expect(p.calls).toHaveLength(1);
    hook.unmount();
  }
});

it("refuses a read when the shared operation lock is unavailable", async () => {
  vi.stubGlobal("navigator", {});
  const p = peer(),
    hook = renderHook(() => useNvkeInstallPlanPullRuntime(args()), { wrapper: p.wrapper });
  await act(async () => {
    expect(await hook.result.current!.onReview()).toBe(false);
  });
  expect(p.calls).toHaveLength(0);
  expect(hook.result.current!.error).toBe("UNCONFIRMED");
});
it("sends one read for duplicate callbacks while a result is pending", async () => {
  let finish!: (value: object) => void;
  const p = peer(
    () =>
      new Promise<object>((resolve) => {
        finish = resolve;
      })
  );
  const hook = renderHook(() => useNvkeInstallPlanPullRuntime(args()), { wrapper: p.wrapper });
  let read!: Promise<boolean>;
  await act(async () => {
    read = hook.result.current!.onReview();
    expect(await hook.result.current!.onReview()).toBe(false);
    await Promise.resolve();
  });
  expect(p.calls).toHaveLength(1);
  await act(async () => {
    finish(envelope());
    expect(await read).toBe(true);
  });
  expect(p.calls).toHaveLength(1);
});
it("does not publish a result after the hook unmounts", async () => {
  let finish!: (value: object) => void;
  const p = peer(
    () =>
      new Promise<object>((resolve) => {
        finish = resolve;
      })
  );
  const hook = renderHook(() => useNvkeInstallPlanPullRuntime(args()), { wrapper: p.wrapper });
  let read!: Promise<boolean>;
  await act(async () => {
    read = hook.result.current!.onReview();
    await Promise.resolve();
  });
  hook.unmount();
  await act(async () => {
    finish(envelope());
    expect(await read).toBe(false);
  });
  expect(p.calls).toHaveLength(1);
});
