import { INSTALL_WORKLOAD_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanWorkloadPanel.stories";
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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { INSTALL_IMAGE_BINDING_PROPS as imageBindingFixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanImageBindingPanel.stories";
import {
  readInstallPlanImmutableJournal,
  readSelectedInstallPlanImmutable,
  saveInstallPlanImmutableJournal,
} from "@/lib/nvke/install-plan-immutable-recovery";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { useNvkeInstallPlanNodePlatforms } from "./use-nvke-install-plan-node-platforms";
import { INSTALL_TREE_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanTreePanel.stories";
import { installPlanNodePlatformsRequest } from "@/lib/nvke/operator-install-plan-node-platforms";
import { INSTALL_NODE_PLATFORMS_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanNodePlatformsPanel.stories";
describe("node platform recovery", () => {
  const plan = fixture.plan!;
  const charts = fixture.charts!;
  const tree = fixture.tree!;
  const artifact = fixture.artifacts!;
  const choice = { artifactId: artifact.id };
  const values = {
    artifactId: artifact.id,
    nodePlatforms: JSON.stringify({
      "private-node-a": {
        uid: "private-uid-a",
        runtimeProfile: "containerd/v2.3.0",
        platform: { os: "linux", architecture: "amd64" },
      },
      "private-node-b": {
        uid: "private-uid-b",
        runtimeProfile: "containerd/v1.7.31",
        platform: { os: "linux", architecture: "arm64", variant: "v8" },
      },
    }),
    maximumImageBytes: "10737418240",
    maximumMetadataBytes: "16777216",
    maximumBytes: "16777216",
    maximumObservationBytes: "16777216",
    maximumHelperBytes: "536870912",
    maximumCalls: "512",
    maximumPairs: "10000",
    timeoutSeconds: "300",
  };

  function args() {
    return {
      context: {
        scope: {
          server: "https://astro.example.test/app/gql/config/",
          organizationId: fixture.site.id,
          actorId: 41,
        },
        scopeKey: "actor-visit",
        access: { read: true, manage: false, admin: true, loading: false },
        loading: false,
        failed: false,
      },
      source: { ...INSTALL_TREE_PROPS },
      workload: { ...INSTALL_WORKLOAD_PROPS },
      pending: false,
      imageBinding: { ...imageBindingFixture },
    };
  }
  function source() {
    return {
      ...args().context.scope,
      siteId: plan.siteId,
      siteVersion: plan.siteVersion,
      operationId: plan.operationId,
      operationVersion: plan.operationVersion,
      authorityId: plan.authorityId,
      authorityVersion: plan.authorityVersion,
      instanceId: plan.plan.source.instanceId,
      bundleManifestSha256: plan.plan.source.bundleManifestSha256,
      nativeRevision: plan.plan.source.nativeRevision,
      planId: plan.id,
      planVersion: plan.version,
      planDigest: plan.plan.planDigest,
      chartArtifactId: charts.id,
      chartArtifactVersion: charts.version,
      chartArtifactDigest: charts.artifactDigest,
      treeId: tree.id,
      treeVersion: tree.version,
      treeDigest: tree.treeDigest,
      workloadId: fixture.workload!.id,
      workloadVersion: fixture.workload!.version,
      workloadDigest: fixture.workload!.workloadDigest,
      bindingId: fixture.binding!.id,
      bindingVersion: fixture.binding!.version,
      bindingDigest: fixture.binding!.bindingDigest,
      mode: "platform" as const,
    };
  }
  function harness() {
    const calls: {
      request: Operation;
      reply: (data: Record<string, unknown>) => void;
      fail: () => void;
    }[] = [];
    const cache = new InMemoryCache();
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
              fail: () => observer.error(new Error("private failure diagnostics")),
            });
          })
      ),
    });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
    return { calls, cache, wrapper };
  }
  const operation = (action: "Prepare" | "Review" | "Retry") =>
    action + "NvkeInstallPlanNodePlatforms";
  const fieldSuffix = "NodePlatforms";
  const fields = {
    [operation("Prepare")]: "prepareNvkeOperatorInstallPlan" + fieldSuffix,
    [operation("Review")]: "reviewNvkeOperatorInstallPlan" + fieldSuffix,
    [operation("Retry")]: "recoverNvkeOperatorInstallPlan" + fieldSuffix + "Preparation",
  } as const;

  function reply(
    h: ReturnType<typeof harness>,
    action: keyof typeof fields,
    value: unknown = artifact
  ) {
    h.calls
      .findLast((c) => c.request.operationName === action)!
      .reply({ [fields[action]]: { ok: true, errors: [], data: value } });
  }

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("navigator", {
      locks: {
        request: async (_key: string, _options: unknown, run: () => Promise<unknown>) => run(),
      },
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });
  async function ready(r: {
    result: { current: ReturnType<typeof useNvkeInstallPlanNodePlatforms> };
  }) {
    await waitFor(() => expect(r.result.current?.canReview).toBe(true));
  }
  it("durably binds every workload option before sending and avoids cache/diagnostic retention", async () => {
    const h = harness(),
      a = args(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(a), { wrapper: h.wrapper });
    await ready(r);
    expect(h.calls).toHaveLength(0);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    const body = JSON.stringify(
      installPlanNodePlatformsRequest(
        values,
        plan,
        charts,
        tree,
        fixture.workload!,
        fixture.binding!
      )
    );
    expect(readInstallPlanImmutableJournal(localStorage, source(), choice)?.inputSha256).toBe(
      await nativeTextDigest(body)
    );
    expect(readSelectedInstallPlanImmutable(localStorage, source())).toEqual(choice);
    expect(h.calls[0].request.variables.input).toEqual({
      operationId: plan.operationId,
      expectedActorId: 41,
      expectedSiteVersion: plan.siteVersion,
      expectedAuthorityVersion: plan.authorityVersion,
      expectedOperationVersion: plan.operationVersion,
      planId: plan.id,
      expectedPlanVersion: plan.version,
      planDigest: plan.plan.planDigest,
      chartArtifactId: charts.id,
      expectedChartArtifactVersion: charts.version,
      chartArtifactDigest: charts.artifactDigest,
      treeId: tree.id,
      expectedTreeVersion: tree.version,
      treeDigest: tree.treeDigest,
      workloadId: fixture.workload!.id,
      expectedWorkloadVersion: fixture.workload!.version,
      workloadDigest: fixture.workload!.workloadDigest,
      bindingId: fixture.binding!.id,
      expectedBindingVersion: fixture.binding!.version,
      bindingDigest: fixture.binding!.bindingDigest,
      requestText: body,
    });
    expect(h.calls[0].request.getContext()).toMatchObject({
      redactDiagnostics: true,
      expectedNvkeOrganization: a.context.scope.organizationId,
      fetchOptions: { redirect: "error", cache: "no-store" },
    });
    const stored = JSON.stringify(localStorage);
    for (const privateValue of [
      "maximumBytes",
      "nodePlatforms",
      "private-node-a",
      "private-uid-a",
      "containerd/v2.3.0",
    ])
      expect(stored).not.toContain(privateValue);
    await act(async () => {
      reply(h, operation("Prepare"));
      expect(await pending).toBe(true);
    });
    expect(r.result.current?.artifacts?.captureDigest).toBe("3".repeat(64));
    expect(h.cache.extract()).toEqual({});
  });
  it("reloads metadata without RPC and recovers a lost reply by explicit read", async () => {
    const h = harness(),
      a = args(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(a), { wrapper: h.wrapper });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      h.calls[0].fail();
      expect(await pending).toBe(false);
    });
    expect(r.result.current?.retainedArtifactId).toBe(artifact.id);
    expect(r.result.current?.onChooseArtifact).toBeUndefined();
    r.unmount();
    const next = renderHook(() => useNvkeInstallPlanNodePlatforms(a), { wrapper: h.wrapper });
    await ready(next);
    expect(h.calls).toHaveLength(1);
    expect(next.result.current?.canRetry).toBe(true);
    act(() => {
      pending = next.result.current!.onReview(artifact.id);
    });
    await waitFor(() => expect(h.calls).toHaveLength(2));
    expect(h.calls[1].request.variables.input.requestText).toBeUndefined();
    await act(async () => {
      reply(h, operation("Review"));
      expect(await pending).toBe(true);
    });
    expect(next.result.current?.artifacts).toEqual(artifact);
  });
  it("requires a failed explicit read before re-entering the exact original settings", async () => {
    const body = JSON.stringify(
      installPlanNodePlatformsRequest(
        values,
        plan,
        charts,
        tree,
        fixture.workload!,
        fixture.binding!
      )
    );
    saveInstallPlanImmutableJournal(localStorage, source(), choice, await nativeTextDigest(body));
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(args()), { wrapper: h.wrapper });
    await ready(r);
    await act(async () => {
      expect(await r.result.current!.onPrepare(values)).toBe(false);
    });
    expect(h.calls).toHaveLength(0);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onReview(artifact.id);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      h.calls[0].fail();
      await pending;
    });
    expect(r.result.current?.allowOriginalPreparation).toBe(true);
    await act(async () => {
      expect(await r.result.current!.onPrepare({ ...values, maximumBytes: "33554432" })).toBe(
        false
      );
    });
    expect(h.calls).toHaveLength(1);
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(2));
    await act(async () => {
      reply(h, operation("Prepare"));
      expect(await pending).toBe(true);
    });
  });
  it("requires locks for preparation while allowing read-only recovery without locks", async () => {
    vi.stubGlobal("navigator", {});
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(args()), { wrapper: h.wrapper });
    await ready(r);
    await act(async () => {
      expect(await r.result.current!.onPrepare(values)).toBe(false);
    });
    expect(h.calls).toHaveLength(0);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onReview(artifact.id);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      reply(h, operation("Review"));
      expect(await pending).toBe(true);
    });
  });
  it("refuses a silently dropped journal write before contacting the server", async () => {
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(args()), { wrapper: h.wrapper });
    await ready(r);
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {});
    await act(async () => {
      expect(await r.result.current!.onPrepare(values)).toBe(false);
    });
    expect(h.calls).toHaveLength(0);
    expect(r.result.current?.canRetry).toBe(false);
  });
  it("keeps inspection available when browser storage is unavailable", async () => {
    vi.stubGlobal("navigator", {});
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("private storage diagnostic");
    });
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(args()), { wrapper: h.wrapper });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onReview(artifact.id);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      reply(h, operation("Review"));
      expect(await pending).toBe(true);
    });
    expect(r.result.current?.artifacts).toEqual(artifact);
    expect(r.result.current?.canRetry).toBe(false);
  });
  it.each(["authorizing", "foreign", "wrong-root"])("refuses a %s result", async (kind) => {
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(args()), { wrapper: h.wrapper });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    const bad =
      kind === "authorizing"
        ? { ...artifact, continuationAuthorized: true }
        : kind === "foreign"
          ? { ...artifact, planDigest: "a".repeat(64) }
          : { ...artifact, workloadDigest: "invalid" };
    await act(async () => {
      reply(h, operation("Prepare"), bad);
      expect(await pending).toBe(false);
    });
    expect(r.result.current?.artifacts).toBeNull();
    expect(r.result.current?.error).not.toContain("private");
  });
  it("discards an A to B to A reply and holds pending until the original request settles", async () => {
    const h = harness(),
      a = args(),
      r = renderHook((value) => useNvkeInstallPlanNodePlatforms(value), {
        initialProps: a,
        wrapper: h.wrapper,
      });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    r.rerender({
      ...a,
      context: {
        ...a.context,
        scopeKey: "another-actor",
        scope: { ...a.context.scope, actorId: 42 },
      },
    });
    await ready(r);
    expect(r.result.current?.retainedArtifactId).toBeNull();
    expect(r.result.current?.pending).toBe(true);
    r.rerender(a);
    await ready(r);
    await act(async () => {
      reply(h, operation("Prepare"));
      expect(await pending).toBe(false);
    });
    expect(r.result.current?.artifacts).toBeNull();
    expect(r.result.current?.pending).toBe(false);
  });
  it.each([
    "actor",
    "parent",
    "authority",
    "permission",
    "chart-version",
    "chart-digest",
    "chart-id",
    "chart-missing",
    "tree-version",
    "tree-digest",
    "tree-id",
    "tree-missing",
    "binding-version",
    "binding-digest",
    "binding-id",
    "binding-missing",
  ])("invalidates state after a %s change", async (kind) => {
    const h = harness(),
      a = args(),
      r = renderHook((value) => useNvkeInstallPlanNodePlatforms(value), {
        initialProps: a,
        wrapper: h.wrapper,
      });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onReview(artifact.id);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      reply(h, operation("Review"));
      await pending;
    });
    const b = {
      ...a,
      context: { ...a.context, scope: { ...a.context.scope }, access: { ...a.context.access } },
      source: { ...a.source },
    };
    if (kind === "chart-version") b.source.charts = { ...charts, version: charts.version + 1 };
    if (kind === "chart-digest") b.source.charts = { ...charts, artifactDigest: "a".repeat(64) };
    if (kind === "chart-id") b.source.charts = { ...charts, id: artifact.id };
    if (kind === "chart-missing") b.source.charts = null;
    if (kind === "tree-version") b.source.artifacts = { ...tree, version: tree.version + 1 };
    if (kind === "tree-digest") b.source.artifacts = { ...tree, treeDigest: "a".repeat(64) };
    if (kind === "tree-id") b.source.artifacts = { ...tree, id: artifact.id };
    if (kind === "tree-missing") b.source.artifacts = null;
    if (kind === "binding-version")
      b.imageBinding = {
        ...b.imageBinding,
        artifacts: { ...fixture.binding!, version: fixture.binding!.version + 1 },
      };
    if (kind === "binding-digest")
      b.imageBinding = {
        ...b.imageBinding,
        artifacts: { ...fixture.binding!, bindingDigest: "f".repeat(64) },
      };
    if (kind === "binding-id")
      b.imageBinding = { ...b.imageBinding, artifacts: { ...fixture.binding!, id: artifact.id } };
    if (kind === "binding-missing") b.imageBinding = { ...b.imageBinding, artifacts: null };
    if (kind === "actor") b.context.scope.actorId++;
    if (kind === "parent") b.source.plan = { ...plan, version: plan.version + 1 };
    if (kind === "authority") b.source.authority = { ...b.source.authority!, isEnabled: false };
    if (kind === "permission") b.context.access.admin = false;
    r.rerender(b);
    expect(r.result.current?.artifacts).toBeNull();
    expect(r.result.current?.retainedArtifactId).toBeNull();
  });

  it("uses only the original retained private intent on explicit retry", async () => {
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(args()), { wrapper: h.wrapper });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      h.calls[0].fail();
      expect(await pending).toBe(false);
    });
    expect(r.result.current?.canRetry).toBe(true);
    act(() => {
      pending = r.result.current!.onRetryPreparation(artifact.id);
    });
    await waitFor(() => expect(h.calls).toHaveLength(2));
    expect(h.calls[1].request.operationName).toBe(operation("Retry"));
    expect(h.calls[1].request.variables.input.requestText).toBeUndefined();
    expect(h.calls[1].request.variables.input).toMatchObject({
      chartArtifactId: charts.id,
      expectedChartArtifactVersion: charts.version,
      chartArtifactDigest: charts.artifactDigest,
      treeId: tree.id,
      expectedTreeVersion: tree.version,
      treeDigest: tree.treeDigest,
      artifactId: artifact.id,
    });
    await act(async () => {
      reply(h, operation("Retry"));
      expect(await pending).toBe(true);
    });
    expect(h.calls).toHaveLength(2);
  });
  it("permits public review but refuses private retry without the original browser journal", async () => {
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(args()), { wrapper: h.wrapper });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onReview(artifact.id);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      reply(h, operation("Review"));
      expect(await pending).toBe(true);
    });
    expect(r.result.current?.canRetry).toBe(false);
    await act(async () => {
      expect(await r.result.current!.onRetryPreparation(artifact.id)).toBe(false);
    });
    expect(h.calls).toHaveLength(1);
  });

  it("discards a chart A to B to A reply rather than reviving an earlier publication epoch", async () => {
    const h = harness(),
      a = args(),
      r = renderHook((value) => useNvkeInstallPlanNodePlatforms(value), {
        initialProps: a,
        wrapper: h.wrapper,
      });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    r.rerender({
      ...a,
      source: {
        ...a.source,
        charts: { ...charts, version: charts.version + 1 },
        artifacts: {
          ...tree,
          id: "2d98e41e-ff9c-4290-bc01-9fce74037cdc",
          chartArtifactVersion: charts.version + 1,
          treeDigest: "e".repeat(64),
        },
      },
    });
    await waitFor(() => expect(r.result.current?.canReview).toBe(false));
    expect(r.result.current?.pending).toBe(true);
    expect(r.result.current?.retainedArtifactId).toBeNull();
    r.rerender(a);
    await ready(r);
    await act(async () => {
      reply(h, operation("Prepare"));
      expect(await pending).toBe(false);
    });
    expect(r.result.current?.artifacts).toBeNull();
    expect(r.result.current?.pending).toBe(false);
    expect(h.calls).toHaveLength(1);
  });

  it("validates the exact submitted node count even if caller scope changes during capture", async () => {
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(args()), { wrapper: h.wrapper });
    await ready(r);
    const mutable = { ...values };
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(mutable);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    mutable.nodePlatforms = JSON.stringify({
      ...JSON.parse(values.nodePlatforms),
      "private-node-c": {
        uid: "private-uid-c",
        runtimeProfile: "containerd/v2.3.7",
        platform: { os: "linux", architecture: "amd64" },
      },
    });
    expect(
      Object.keys(JSON.parse(h.calls[0].request.variables.input.requestText).nodePlatforms)
    ).toHaveLength(2);
    await act(async () => {
      reply(h, operation("Prepare"), { ...artifact, nodeCount: 3, pairCount: 3 });
      expect(await pending).toBe(false);
    });
    expect(r.result.current?.artifacts).toBeNull();
  });

  it.each(["binding", "binding-pending", "workload-pending", "tree-pending"])(
    "rejects an in-flight %s A-to-B-to-A response",
    async (kind) => {
      const h = harness(),
        a = args(),
        r = renderHook((v) => useNvkeInstallPlanNodePlatforms(v), {
          initialProps: a,
          wrapper: h.wrapper,
        });
      await ready(r);
      let pending!: Promise<boolean>;
      act(() => {
        pending = r.result.current!.onPrepare(values);
      });
      await waitFor(() => expect(h.calls).toHaveLength(1));
      if (kind === "binding")
        r.rerender({
          ...a,
          imageBinding: {
            ...a.imageBinding,
            artifacts: { ...fixture.binding!, version: fixture.binding!.version + 1 },
          },
        });
      else if (kind === "binding-pending")
        r.rerender({ ...a, imageBinding: { ...a.imageBinding, pending: true } });
      else if (kind === "tree-pending")
        r.rerender({ ...a, source: { ...a.source, pending: true } });
      else r.rerender({ ...a, workload: { ...a.workload, pending: true } });
      r.rerender(a);
      await ready(r);
      await act(async () => {
        reply(h, operation("Prepare"));
        expect(await pending).toBe(false);
      });
      expect(r.result.current?.artifacts).toBeNull();
      expect(h.calls).toHaveLength(1);
    }
  );
  it("rejects overlapping review, retry and preparation while the first request is pending", async () => {
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanNodePlatforms(args()), { wrapper: h.wrapper });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      expect(await r.result.current!.onPrepare(values)).toBe(false);
      expect(await r.result.current!.onReview(artifact.id)).toBe(false);
      expect(await r.result.current!.onRetryPreparation(artifact.id)).toBe(false);
    });
    expect(h.calls).toHaveLength(1);
    await act(async () => {
      reply(h, operation("Prepare"));
      expect(await pending).toBe(true);
    });
  });

  it.each(["operation", "source", "workload", "binding"])(
    "refuses requests while %s is pending",
    async (kind) => {
      const h = harness(),
        a = args();
      if (kind === "operation") a.pending = true;
      if (kind === "source") a.source.pending = true;
      if (kind === "workload") a.workload.pending = true;
      if (kind === "binding") a.imageBinding.pending = true;
      const r = renderHook(() => useNvkeInstallPlanNodePlatforms(a), { wrapper: h.wrapper });
      await ready(r);
      await act(async () => {
        expect(await r.result.current!.onPrepare(values)).toBe(false);
        expect(await r.result.current!.onReview(artifact.id)).toBe(false);
        expect(await r.result.current!.onRetryPreparation(artifact.id)).toBe(false);
      });
      expect(h.calls).toHaveLength(0);
    }
  );
});
