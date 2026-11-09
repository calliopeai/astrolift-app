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
import { INSTALL_CHARTS_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanChartsPanel.stories";
import {
  readInstallPlanChartsJournal,
  readSelectedInstallPlanCharts,
  saveInstallPlanChartsJournal,
} from "@/lib/nvke/install-plan-charts-recovery";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { useNvkeInstallPlanCharts } from "./use-nvke-install-plan-charts";
import { INSTALL_PLAN_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanPanel.stories";
import { installPlanChartsRequest } from "@/lib/nvke/operator-install-plan-charts";
const plan = fixture.plan!;
const artifact = fixture.artifacts!;
const values = {
  artifactId: artifact.id,
  maximumBytes: "9007199254740993",
  maximumExpandedBytes: "9223372036854775807",
  timeoutSeconds: "300",
  allowPlainHttp: false,
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
    source: { ...INSTALL_PLAN_PROPS },
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
  PrepareNvkeInstallPlanCharts: "prepareNvkeOperatorInstallPlanCharts",
  ReviewNvkeInstallPlanCharts: "reviewNvkeOperatorInstallPlanCharts",
  RetryNvkeInstallPlanCharts: "recoverNvkeOperatorInstallPlanChartsPreparation",
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
async function ready(r: { result: { current: ReturnType<typeof useNvkeInstallPlanCharts> } }) {
  await waitFor(() => expect(r.result.current?.canReview).toBe(true));
}
it("durably binds every chart option before sending and avoids cache/diagnostic retention", async () => {
  const h = harness(),
    a = args(),
    r = renderHook(() => useNvkeInstallPlanCharts(a), { wrapper: h.wrapper });
  await ready(r);
  expect(h.calls).toHaveLength(0);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onPrepare(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  const body = JSON.stringify(installPlanChartsRequest(values, plan));
  expect(readInstallPlanChartsJournal(localStorage, source(), artifact.id)?.inputSha256).toBe(
    await nativeTextDigest(body)
  );
  expect(readSelectedInstallPlanCharts(localStorage, source(), plan.operationId, plan.id)).toBe(
    artifact.id
  );
  expect(h.calls[0].request.variables.input).toEqual({
    operationId: plan.operationId,
    expectedActorId: 41,
    expectedSiteVersion: plan.siteVersion,
    expectedAuthorityVersion: plan.authorityVersion,
    expectedOperationVersion: plan.operationVersion,
    planId: plan.id,
    expectedPlanVersion: plan.version,
    planDigest: plan.plan.planDigest,
    requestText: body,
  });
  expect(h.calls[0].request.getContext()).toMatchObject({
    redactDiagnostics: true,
    expectedNvkeOrganization: a.context.scope.organizationId,
    fetchOptions: { redirect: "error", cache: "no-store" },
  });
  expect(JSON.stringify(localStorage)).not.toContain("maximumBytes");
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlanCharts");
    expect(await pending).toBe(true);
  });
  expect(r.result.current?.artifacts?.bytes).toBe("9007199254740993");
  expect(h.cache.extract()).toEqual({});
});
it("reloads metadata without RPC and recovers a lost reply by explicit read", async () => {
  const h = harness(),
    a = args(),
    r = renderHook(() => useNvkeInstallPlanCharts(a), { wrapper: h.wrapper });
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
  const next = renderHook(() => useNvkeInstallPlanCharts(a), { wrapper: h.wrapper });
  await ready(next);
  expect(h.calls).toHaveLength(1);
  expect(next.result.current?.canRetry).toBe(true);
  act(() => {
    pending = next.result.current!.onReview(artifact.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  expect(h.calls[1].request.variables.input.requestText).toBeUndefined();
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlanCharts");
    expect(await pending).toBe(true);
  });
  expect(next.result.current?.artifacts).toEqual(artifact);
});
it("requires a failed explicit read before re-entering the exact original settings", async () => {
  const body = JSON.stringify(installPlanChartsRequest(values, plan));
  saveInstallPlanChartsJournal(localStorage, source(), artifact.id, await nativeTextDigest(body));
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlanCharts(args()), { wrapper: h.wrapper });
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
    expect(await r.result.current!.onPrepare({ ...values, timeoutSeconds: "301" })).toBe(false);
  });
  expect(h.calls).toHaveLength(1);
  act(() => {
    pending = r.result.current!.onPrepare(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlanCharts");
    expect(await pending).toBe(true);
  });
});
it("requires locks for preparation while allowing read-only recovery without locks", async () => {
  vi.stubGlobal("navigator", {});
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlanCharts(args()), { wrapper: h.wrapper });
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
    reply(h, "ReviewNvkeInstallPlanCharts");
    expect(await pending).toBe(true);
  });
});
it("refuses a silently dropped journal write before contacting the server", async () => {
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlanCharts(args()), { wrapper: h.wrapper });
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
    r = renderHook(() => useNvkeInstallPlanCharts(args()), { wrapper: h.wrapper });
  await ready(r);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview(artifact.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlanCharts");
    expect(await pending).toBe(true);
  });
  expect(r.result.current?.artifacts).toEqual(artifact);
  expect(r.result.current?.canRetry).toBe(false);
});
it.each(["authorizing", "foreign", "overbudget"])("refuses a %s result", async (kind) => {
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlanCharts(args()), { wrapper: h.wrapper });
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
        : { ...artifact, bytes: "9007199254740994" };
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlanCharts", bad);
    expect(await pending).toBe(false);
  });
  expect(r.result.current?.artifacts).toBeNull();
  expect(r.result.current?.error).not.toContain("private");
});
it("discards an A to B to A reply and holds pending until the original request settles", async () => {
  const h = harness(),
    a = args(),
    r = renderHook((value) => useNvkeInstallPlanCharts(value), {
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
    reply(h, "PrepareNvkeInstallPlanCharts");
    expect(await pending).toBe(false);
  });
  expect(r.result.current?.artifacts).toBeNull();
  expect(r.result.current?.pending).toBe(false);
});
it.each(["actor", "parent", "authority", "permission"])(
  "invalidates state after a %s change",
  async (kind) => {
    const h = harness(),
      a = args(),
      r = renderHook((value) => useNvkeInstallPlanCharts(value), {
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
      reply(h, "ReviewNvkeInstallPlanCharts");
      await pending;
    });
    const b = {
      ...a,
      context: { ...a.context, scope: { ...a.context.scope }, access: { ...a.context.access } },
      source: { ...a.source },
    };
    if (kind === "actor") b.context.scope.actorId++;
    if (kind === "parent") b.source.plan = { ...plan, version: plan.version + 1 };
    if (kind === "authority") b.source.authority = { ...b.source.authority!, isEnabled: false };
    if (kind === "permission") b.context.access.admin = false;
    r.rerender(b);
    expect(r.result.current?.artifacts).toBeNull();
    expect(r.result.current?.retainedArtifactId).toBeNull();
  }
);
