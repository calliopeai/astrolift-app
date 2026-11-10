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
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { INSTALL_VERIFICATION_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanVerificationPanel.stories";
import {
  readInstallPlanVerificationJournal,
  readSelectedInstallPlanVerification,
  saveInstallPlanVerificationJournal,
} from "@/lib/nvke/install-plan-verification-recovery";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { useNvkeInstallPlanVerification } from "./use-nvke-install-plan-verification";
import { INSTALL_CONTEXT_PROPS as SOURCE_CONTEXT_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanContextPanel.stories";
import { installPlanVerificationRequest } from "@/lib/nvke/operator-install-plan-verification";
const plan = fixture.plan!;
const charts = fixture.charts!;
const tree = fixture.tree!;
const artifact = fixture.artifacts!;
const captured = fixture.context!;
const selection = { item: artifact.item, ordinal: artifact.ordinal };
const choice = { artifactId: artifact.id };
const values = {
  artifactId: artifact.id,
  maximumBytes: "16777216",
  maximumCaptureAgeSeconds: "3600",
  maximumCalls: "512",
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
    source: { ...SOURCE_CONTEXT_PROPS },
    pending: false,
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
    contextId: captured.id,
    contextVersion: captured.version,
    contextDigest: captured.contextDigest,
    item: captured.item,
    ordinal: captured.ordinal,
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
const fields = {
  PrepareNvkeInstallPlanVerification: "prepareNvkeOperatorInstallPlanVerification",
  ReviewNvkeInstallPlanVerification: "reviewNvkeOperatorInstallPlanVerification",
  RetryNvkeInstallPlanVerification: "recoverNvkeOperatorInstallPlanVerificationPreparation",
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
  result: { current: ReturnType<typeof useNvkeInstallPlanVerification> };
}) {
  await waitFor(() => expect(r.result.current?.canReview).toBe(true));
}
it("durably binds every chart option before sending and avoids cache/diagnostic retention", async () => {
  const h = harness(),
    a = args(),
    r = renderHook(() => useNvkeInstallPlanVerification(a), { wrapper: h.wrapper });
  await ready(r);
  expect(h.calls).toHaveLength(0);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onPrepare(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  const body = JSON.stringify(installPlanVerificationRequest(values, plan, charts, tree, captured));
  expect(readInstallPlanVerificationJournal(localStorage, source(), choice)?.inputSha256).toBe(
    await nativeTextDigest(body)
  );
  expect(readSelectedInstallPlanVerification(localStorage, source())).toEqual(choice);
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
    ...selection,
    contextId: captured.id,
    expectedContextVersion: captured.version,
    contextDigest: captured.contextDigest,
    requestText: body,
  });
  expect(h.calls[0].request.getContext()).toMatchObject({
    redactDiagnostics: true,
    expectedNvkeOrganization: a.context.scope.organizationId,
    fetchOptions: { redirect: "error", cache: "no-store" },
  });
  expect(JSON.stringify(localStorage)).not.toContain("maximumBytes");
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlanVerification");
    expect(await pending).toBe(true);
  });
  expect(r.result.current?.artifacts?.contextDigest).toBe("3".repeat(64));
  expect(h.cache.extract()).toEqual({});
});
it("reloads metadata without RPC and recovers a lost reply by explicit read", async () => {
  const h = harness(),
    a = args(),
    r = renderHook(() => useNvkeInstallPlanVerification(a), { wrapper: h.wrapper });
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
  const next = renderHook(() => useNvkeInstallPlanVerification(a), { wrapper: h.wrapper });
  await ready(next);
  expect(h.calls).toHaveLength(1);
  expect(next.result.current?.canRetry).toBe(true);
  act(() => {
    pending = next.result.current!.onReview(artifact.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  expect(h.calls[1].request.variables.input.requestText).toBeUndefined();
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlanVerification");
    expect(await pending).toBe(true);
  });
  expect(next.result.current?.artifacts).toEqual(artifact);
});
it("requires a failed explicit read before re-entering the exact original settings", async () => {
  const body = JSON.stringify(installPlanVerificationRequest(values, plan, charts, tree, captured));
  saveInstallPlanVerificationJournal(localStorage, source(), choice, await nativeTextDigest(body));
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlanVerification(args()), { wrapper: h.wrapper });
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
    expect(await r.result.current!.onPrepare({ ...values, maximumBytes: "16777217" })).toBe(false);
  });
  expect(h.calls).toHaveLength(1);
  act(() => {
    pending = r.result.current!.onPrepare(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlanVerification");
    expect(await pending).toBe(true);
  });
});
it("requires locks for preparation while allowing read-only recovery without locks", async () => {
  vi.stubGlobal("navigator", {});
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlanVerification(args()), { wrapper: h.wrapper });
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
    reply(h, "ReviewNvkeInstallPlanVerification");
    expect(await pending).toBe(true);
  });
});
it("refuses a silently dropped journal write before contacting the server", async () => {
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlanVerification(args()), { wrapper: h.wrapper });
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
    r = renderHook(() => useNvkeInstallPlanVerification(args()), { wrapper: h.wrapper });
  await ready(r);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview(artifact.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlanVerification");
    expect(await pending).toBe(true);
  });
  expect(r.result.current?.artifacts).toEqual(artifact);
  expect(r.result.current?.canRetry).toBe(false);
});
it.each(["authorizing", "foreign", "wrong-item"])("refuses a %s result", async (kind) => {
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlanVerification(args()), { wrapper: h.wrapper });
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
        : { ...artifact, item: "other" };
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlanVerification", bad);
    expect(await pending).toBe(false);
  });
  expect(r.result.current?.artifacts).toBeNull();
  expect(r.result.current?.error).not.toContain("private");
});
it("discards an A to B to A reply and holds pending until the original request settles", async () => {
  const h = harness(),
    a = args(),
    r = renderHook((value) => useNvkeInstallPlanVerification(value), {
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
    reply(h, "PrepareNvkeInstallPlanVerification");
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
  "context-version",
  "context-digest",
  "context-missing",
  "context-selection",
])("invalidates state after a %s change", async (kind) => {
  const h = harness(),
    a = args(),
    r = renderHook((value) => useNvkeInstallPlanVerification(value), {
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
    reply(h, "ReviewNvkeInstallPlanVerification");
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
  if (kind === "tree-version") b.source.tree = { ...tree, version: tree.version + 1 };
  if (kind === "tree-digest") b.source.tree = { ...tree, treeDigest: "a".repeat(64) };
  if (kind === "tree-id") b.source.tree = { ...tree, id: artifact.id };
  if (kind === "tree-missing") b.source.tree = null;
  if (kind === "context-version")
    b.source.artifacts = { ...captured, version: captured.version + 1 };
  if (kind === "context-digest")
    b.source.artifacts = { ...captured, contextDigest: "f".repeat(64) };
  if (kind === "context-missing") b.source.artifacts = null;
  if (kind === "context-selection") b.source.retainedSelection = { item: "controller", ordinal: 2 };
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
    r = renderHook(() => useNvkeInstallPlanVerification(args()), { wrapper: h.wrapper });
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
  expect(h.calls[1].request.operationName).toBe("RetryNvkeInstallPlanVerification");
  expect(h.calls[1].request.variables.input.requestText).toBeUndefined();
  expect(h.calls[1].request.variables.input).toMatchObject({
    chartArtifactId: charts.id,
    expectedChartArtifactVersion: charts.version,
    chartArtifactDigest: charts.artifactDigest,
    treeId: tree.id,
    expectedTreeVersion: tree.version,
    treeDigest: tree.treeDigest,
    ...selection,
    contextId: captured.id,
    expectedContextVersion: captured.version,
    contextDigest: captured.contextDigest,
    artifactId: artifact.id,
  });
  await act(async () => {
    reply(h, "RetryNvkeInstallPlanVerification");
    expect(await pending).toBe(true);
  });
  expect(h.calls).toHaveLength(2);
});
it("permits public review but refuses private retry without the original browser journal", async () => {
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlanVerification(args()), { wrapper: h.wrapper });
  await ready(r);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview(artifact.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlanVerification");
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
    r = renderHook((value) => useNvkeInstallPlanVerification(value), {
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
      tree: {
        ...tree,
        id: "2d98e41e-ff9c-4290-bc01-9fce74037cdc",
        chartArtifactVersion: charts.version + 1,
        treeDigest: "e".repeat(64),
      },
      artifacts: {
        ...captured,
        id: "7d98e41e-ff9c-4290-bc01-9fce74037cdc",
        chartArtifactVersion: charts.version + 1,
        treeId: "2d98e41e-ff9c-4290-bc01-9fce74037cdc",
        treeDigest: "e".repeat(64),
        contextDigest: "f".repeat(64),
      },
    },
  });
  await ready(r);
  expect(r.result.current?.pending).toBe(true);
  expect(r.result.current?.retainedArtifactId).toBeNull();
  r.rerender(a);
  await ready(r);
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlanVerification");
    expect(await pending).toBe(false);
  });
  expect(r.result.current?.artifacts).toBeNull();
  expect(r.result.current?.pending).toBe(false);
  expect(h.calls).toHaveLength(1);
});

it.each([{ requests: 513 }, { bytes: "16777217" }])(
  "refuses verification beyond explicitly reviewed limits %j",
  async (change) => {
    const h = harness(),
      r = renderHook(() => useNvkeInstallPlanVerification(args()), { wrapper: h.wrapper });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      reply(h, "PrepareNvkeInstallPlanVerification", { ...artifact, ...change });
      expect(await pending).toBe(false);
    });
    expect(r.result.current?.artifacts).toBeNull();
    expect(r.result.current?.retainedArtifactId).toBe(artifact.id);
  }
);
