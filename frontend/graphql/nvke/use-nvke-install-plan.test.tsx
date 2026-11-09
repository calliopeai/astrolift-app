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
import { INSTALL_PLAN_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanPanel.stories";
import { OPERATOR_JOB_PROPS } from "@/components/screens/nvke/NvkeOperatorJobPanel.stories";
import {
  readInstallPlanJournal,
  readSelectedInstallPlan,
  saveInstallPlanJournal,
} from "@/lib/nvke/install-plan-recovery";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { useNvkeInstallPlan } from "./use-nvke-install-plan";
const plan = fixture.plan!;
const values = {
  planId: plan.id,
  requestText: ` { "format": 1, "planId": "${plan.id}", "request": {"private": "precise native request"}, "maximumInputBytes": "4096" }\n`,
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
    source: { ...OPERATOR_JOB_PROPS, operation: fixture.operation },
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
  PrepareNvkeInstallPlan: "prepareNvkeOperatorInstallPlan",
  ReviewNvkeInstallPlan: "reviewNvkeOperatorInstallPlan",
  RetryNvkeInstallPlan: "recoverNvkeOperatorInstallPlanPreparation",
} as const;
function reply(h: ReturnType<typeof harness>, action: keyof typeof fields, value: unknown = plan) {
  h.calls
    .findLast((c) => c.request.operationName === action)!
    .reply({ [fields[action]]: { ok: true, errors: [], data: value } });
}
function pages(h: ReturnType<typeof harness>) {
  for (const call of h.calls.filter((c) =>
    (c.request.operationName ?? "").startsWith("GetNvkeInstallPlan")
  )) {
    const items = (call.request.operationName ?? "").includes("Item");
    call.reply({
      [items
        ? "astroliftNvkeOperatorInstallPlanItemPage"
        : "astroliftNvkeOperatorInstallPlanWorkloadPage"]: {
        ok: true,
        errors: [],
        data: {
          planId: plan.id,
          planDigest: plan.plan.planDigest,
          view: items ? "items" : "workloads",
          items: items ? fixture.itemTable.rows : fixture.workloadTable.rows,
          totalCount: items ? 3 : 2,
          nextCursor: null,
        },
      },
    });
  }
}
async function ready(r: { result: { readonly current: ReturnType<typeof useNvkeInstallPlan> } }) {
  await waitFor(() => expect(r.result.current?.canReview).toBe(true));
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
it("journals exact private text before preparation, pins the original source and bypasses all caches", async () => {
  const h = harness(),
    a = args(),
    r = renderHook(() => useNvkeInstallPlan(a), { wrapper: h.wrapper });
  await ready(r);
  expect(h.calls).toHaveLength(0);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onPrepare(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  const journal = readInstallPlanJournal(window.localStorage, source(), plan.id)!;
  expect(journal.inputSha256).toBe(await nativeTextDigest(values.requestText));
  expect(JSON.stringify({ ...window.localStorage })).not.toContain("precise native request");
  expect(readSelectedInstallPlan(window.localStorage, source(), plan.operationId)).toBe(plan.id);
  expect(h.calls[0].request.variables.input).toEqual({
    operationId: plan.operationId,
    expectedActorId: 41,
    expectedSiteVersion: plan.siteVersion,
    expectedAuthorityVersion: plan.authorityVersion,
    expectedOperationVersion: plan.operationVersion,
    requestText: values.requestText,
  });
  const ctx = h.calls[0].request.getContext();
  expect(ctx.redactDiagnostics).toBe(true);
  expect(ctx.fetchOptions).toMatchObject({ redirect: "error", cache: "no-store" });
  expect(ctx.expectedNvkeOrganization).toBe(a.context.scope.organizationId);
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlan");
    expect(await pending).toBe(true);
  });
  await waitFor(() => expect(h.calls).toHaveLength(3));
  act(() => pages(h));
  await waitFor(() => expect(r.result.current!.itemTable.rows).toHaveLength(3));
  expect(r.result.current!.workloadTable.rows[0].generation).toBe("9007199254740993");
  expect(h.cache.extract()).toEqual({});
});
it("restores only identity after remount, then reads the saved result without resubmitting", async () => {
  saveInstallPlanJournal(
    window.localStorage,
    source(),
    plan.id,
    await nativeTextDigest(values.requestText)
  );
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
  await ready(r);
  expect(r.result.current!.retainedPlanId).toBe(plan.id);
  expect(h.calls).toHaveLength(0);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview(plan.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlan");
    expect(await pending).toBe(true);
  });
  expect(h.calls.filter((c) => c.request.operationName === "PrepareNvkeInstallPlan")).toHaveLength(
    0
  );
  expect(r.result.current!.plan?.id).toBe(plan.id);
});
it("requires explicit review after a lost reply, refuses changed private text, and permits exact original resubmission", async () => {
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
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
  expect(r.result.current!.allowOriginalPreparation).toBe(false);
  await act(async () => expect(await r.result.current!.onPrepare(values)).toBe(false));
  expect(h.calls).toHaveLength(1);
  act(() => {
    pending = r.result.current!.onReview(plan.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    h.calls[1].reply({
      reviewNvkeOperatorInstallPlan: { ok: false, errors: [{ code: "NOT_FOUND" }], data: null },
    });
    expect(await pending).toBe(false);
  });
  expect(r.result.current!.allowOriginalPreparation).toBe(true);
  expect(r.result.current!.onChoosePlan).toBeTypeOf("function");
  await act(async () =>
    expect(
      await r.result.current!.onPrepare({ ...values, requestText: values.requestText + " " })
    ).toBe(false)
  );
  expect(h.calls).toHaveLength(2);
  act(() => {
    pending = r.result.current!.onPrepare(values);
  });
  await waitFor(() => expect(h.calls).toHaveLength(3));
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlan");
    expect(await pending).toBe(true);
  });
  expect(h.calls[2].request.variables.input.requestText).toBe(values.requestText);
});
it("retries only retained server intent and requires the local original journal", async () => {
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
  await ready(r);
  await act(async () => expect(await r.result.current!.onRetryPreparation(plan.id)).toBe(false));
  expect(h.calls).toHaveLength(0);
  saveInstallPlanJournal(
    window.localStorage,
    source(),
    plan.id,
    await nativeTextDigest(values.requestText)
  );
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onRetryPreparation(plan.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  expect(h.calls[0].request.variables.input).not.toHaveProperty("requestText");
  await act(async () => {
    reply(h, "RetryNvkeInstallPlan");
    expect(await pending).toBe(true);
  });
});
it.each(["actor", "permission", "source", "visit"])(
  "discards late responses after %s changes and returning to the original scope",
  async (kind) => {
    const h = harness(),
      a = args(),
      r = renderHook((p) => useNvkeInstallPlan(p), { wrapper: h.wrapper, initialProps: a });
    await ready(r);
    let pending!: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onPrepare(values);
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    const changed = {
      ...a,
      context: { ...a.context, scope: { ...a.context.scope }, access: { ...a.context.access } },
      source: { ...a.source, operation: { ...a.source.operation } },
    };
    if (kind === "actor") changed.context.scope.actorId++;
    if (kind === "permission") changed.context.access.admin = false;
    if (kind === "source") changed.source.operation.version++;
    if (kind === "visit") changed.context.scopeKey = "new-visit";
    r.rerender(changed);
    r.rerender(a);
    await act(async () => {
      reply(h, "PrepareNvkeInstallPlan");
      expect(await pending).toBe(false);
    });
    expect(r.result.current?.plan).toBeNull();
    expect(
      h.calls.filter((c) => (c.request.operationName ?? "").startsWith("GetNvkeInstallPlan"))
    ).toHaveLength(0);
  }
);
it("fails closed before private submission when storage or cross-tab locking is unavailable", async () => {
  vi.stubGlobal("navigator", {});
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
  await ready(r);
  await act(async () => expect(await r.result.current!.onPrepare(values)).toBe(false));
  expect(h.calls).toHaveLength(0);
});
it("permits read-only inspection without browser locks and never grants private retry to a different reviewer", async () => {
  vi.stubGlobal("navigator", {});
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
  await ready(r);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview(plan.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlan");
    expect(await pending).toBe(true);
  });
  expect(r.result.current!.canRetry).toBe(false);
});
it("refuses a response from a changed agent and rejects a digest-swapped page", async () => {
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
  await ready(r);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview(plan.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlan", {
      ...plan,
      plan: { ...plan.plan, source: { ...plan.plan.source, instanceId: plan.id } },
    });
    expect(await pending).toBe(false);
  });
  expect(r.result.current!.plan).toBeNull();
  act(() => {
    pending = r.result.current!.onReview(plan.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlan");
    expect(await pending).toBe(true);
  });
  await waitFor(() => expect(h.calls).toHaveLength(4));
  const item = h.calls.findLast((c) => c.request.operationName === "GetNvkeInstallPlanItemPage")!;
  act(() =>
    item.reply({
      astroliftNvkeOperatorInstallPlanItemPage: {
        ok: true,
        errors: [],
        data: {
          planId: plan.id,
          planDigest: "a".repeat(64),
          view: "items",
          items: fixture.itemTable.rows,
          totalCount: 3,
          nextCursor: null,
        },
      },
    })
  );
  await waitFor(() => expect(r.result.current!.itemTable.state).toBe("error"));
  expect(r.result.current!.itemTable.rows).toHaveLength(0);
});

it("preserves earlier journals when choosing a new proposal and performs no implicit preparation", async () => {
  saveInstallPlanJournal(
    window.localStorage,
    source(),
    plan.id,
    await nativeTextDigest(values.requestText)
  );
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
  await ready(r);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview(plan.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlan");
    await pending;
  });
  await waitFor(() => expect(h.calls).toHaveLength(3));
  await act(async () => {
    await r.result.current!.onChoosePlan?.();
  });
  await ready(r);
  expect(r.result.current!.plan).toBeNull();
  expect(r.result.current!.retainedPlanId).toBeNull();
  expect(readInstallPlanJournal(window.localStorage, source(), plan.id)).not.toBeNull();
  expect(h.calls.filter((c) => c.request.operationName === "PrepareNvkeInstallPlan")).toHaveLength(
    0
  );
});
it("captures private input before awaiting hashing or the browser lock", async () => {
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
  await ready(r);
  const mutable = { ...values };
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onPrepare(mutable);
  });
  mutable.requestText = "changed after submit";
  await waitFor(() => expect(h.calls).toHaveLength(1));
  expect(h.calls[0].request.variables.input.requestText).toBe(values.requestText);
  await act(async () => {
    reply(h, "PrepareNvkeInstallPlan");
    expect(await pending).toBe(true);
  });
});
it("checks selection again after acquiring a contended cross-tab lock", async () => {
  let release!: () => void;
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: unknown, run: () => Promise<unknown>) => {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return run();
      },
    },
  });
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
  await ready(r);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onPrepare(values);
  });
  await waitFor(() => expect(release).toBeTypeOf("function"));
  saveInstallPlanJournal(
    window.localStorage,
    source(),
    plan.plan.source.nativeRunId,
    "a".repeat(64)
  );
  await act(async () => {
    release();
    expect(await pending).toBe(false);
  });
  expect(h.calls).toHaveLength(0);
});
it("keeps read-only inspection available when browser storage is blocked", async () => {
  vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.stubGlobal("navigator", {});
  const h = harness(),
    r = renderHook(() => useNvkeInstallPlan(args()), { wrapper: h.wrapper });
  await ready(r);
  let pending!: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview(plan.id);
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reply(h, "ReviewNvkeInstallPlan");
    expect(await pending).toBe(true);
  });
  expect(r.result.current!.canRetry).toBe(false);
});
