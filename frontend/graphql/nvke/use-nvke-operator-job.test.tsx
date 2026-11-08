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
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { SITE } from "@/components/screens/nvke/fixtures";
import { OPERATOR_JOB_PROPS } from "@/components/screens/nvke/NvkeOperatorJobPanel.stories";
import { OPERATOR_AGENT_AUTHORITY } from "@/components/screens/nvke/NvkeOperatorAgentPanel.stories";
import { useNvkeOperatorJob } from "./use-nvke-operator-job";

const privateText = "PRIVATE_NATIVE_BINARY\n";
const scope = {
  server: "https://astro.example.test/app/gql/config/",
  organizationId: SITE.id,
  actorId: 41,
};
function args() {
  return {
    context: {
      scope: { ...scope },
      scopeKey: "caller",
      access: { read: true, admin: true, manage: false, loading: false },
      loading: false,
      failed: false,
    },
    scopeKey: "parent-visit",
    site: SITE,
    operation: { ...OPERATOR_JOB_PROPS.operation },
    enabled: true,
    onRefresh: vi.fn(),
  };
}
async function harness() {
  const bytes = new TextEncoder().encode(privateText),
    digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (byte) =>
      byte.toString(16).padStart(2, "0")
    ).join("");
  const artifact = { name: "private/stdout", size: String(bytes.length), sha256: digest };
  const state = {
    authority: { ...OPERATOR_AGENT_AUTHORITY },
    page: { ...OPERATOR_JOB_PROPS.receipt!, items: [artifact], nextCursor: null, totalCount: 1 },
  };
  const calls: {
      request: Operation;
      reply: (data: Record<string, unknown>) => void;
      fail: () => void;
    }[] = [],
    cache = new InMemoryCache();
  const client = new ApolloClient({
    cache,
    link: new ApolloLink(
      (request) =>
        new Observable((observer) => {
          const call = {
            request,
            reply: (data: Record<string, unknown>) => {
              observer.next({ data });
              observer.complete();
            },
            fail: () => observer.error(new Error("PRIVATE_TRANSPORT_DIAGNOSTICS")),
          };
          calls.push(call);
          if (request.operationName === "GetNvkeOperatorAuthority")
            queueMicrotask(() => call.reply({ astroliftNvkeOperatorAuthority: state.authority }));
          if (request.operationName === "GetNvkeOperatorJobPage")
            queueMicrotask(() =>
              call.reply({
                astroliftNvkeOperatorJobPage: { ok: true, errors: [], data: state.page },
              })
            );
        })
    ),
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  const chunk = () =>
    JSON.stringify({
      id: OPERATOR_JOB_PROPS.operation.id,
      name: artifact.name,
      offset: "0",
      nextOffset: artifact.size,
      size: artifact.size,
      sha256: artifact.sha256,
      dataBase64: btoa(privateText),
    });
  return { state, calls, cache, client, wrapper, artifact, chunk };
}
async function load(
  h: Awaited<ReturnType<typeof harness>>,
  r: { result: { readonly current: ReturnType<typeof useNvkeOperatorJob> } }
) {
  await waitFor(() => expect(r.result.current?.canManage).toBe(true));
  act(() => r.result.current!.onLoad());
  await waitFor(() => expect(r.result.current?.table.state).toBe("ready"));
}
const create = vi.fn((_blob: Blob) => "blob:owned-native"),
  revoke = vi.fn();
beforeEach(() => {
  create.mockClear();
  revoke.mockClear();
  localStorage.clear();
  Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: revoke, configurable: true });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

it("requires explicit receipt loading, then downloads only original version-bound chunks without cache/storage", async () => {
  const h = await harness(),
    r = renderHook(() => useNvkeOperatorJob(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(r.result.current?.canManage).toBe(true));
  expect(h.calls.map((c) => c.request.operationName)).toEqual(["GetNvkeOperatorAuthority"]);
  await load(h, r);
  let pending: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onDownload(h.artifact, 64 * 1048576);
  });
  await waitFor(() =>
    expect(h.calls.at(-1)?.request.operationName).toBe("GetNvkeOperatorArtifactText")
  );
  const call = h.calls.at(-1)!;
  expect(call.request.variables.input).toEqual({
    operationId: OPERATOR_JOB_PROPS.operation.id,
    expectedActorId: 41,
    expectedSiteVersion: SITE.version,
    expectedAuthorityVersion: OPERATOR_AGENT_AUTHORITY.version,
    expectedOperationVersion: OPERATOR_JOB_PROPS.operation.version,
    name: h.artifact.name,
    offset: "0",
    limit: Number(h.artifact.size),
  });
  expect(call.request.getContext()).toMatchObject({
    redactDiagnostics: true,
    queryDeduplication: false,
    expectedNvkeOrganization: SITE.id,
    fetchOptions: { redirect: "error", cache: "no-store" },
  });
  await act(async () => {
    call.reply({ astroliftNvkeOperatorArtifactText: { ok: true, errors: [], data: h.chunk() } });
    expect(await pending!).toBe(true);
  });
  expect(create).toHaveBeenCalledOnce();
  const text = await new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsText(create.mock.calls[0][0]);
  });
  expect(text).toBe(privateText);
  expect(h.cache.extract()).toEqual({});
  expect(localStorage.length).toBe(0);
  expect(JSON.stringify(r.result.current)).not.toContain(privateText);
});

it.each(["actor", "organization", "permission", "loading", "site", "operation", "parent"])(
  "refuses saved callbacks after %s changes and an A-to-B-to-A visit",
  async (change) => {
    const h = await harness(),
      a = args(),
      r = renderHook((input) => useNvkeOperatorJob(input), { initialProps: a, wrapper: h.wrapper });
    await load(h, r);
    const saved = r.result.current!.onDownload;
    const next = {
      ...a,
      context: { ...a.context, scope: { ...a.context.scope }, access: { ...a.context.access } },
    };
    if (change === "actor") next.context.scope.actorId++;
    if (change === "organization")
      next.context.scope.organizationId = OPERATOR_JOB_PROPS.operation.id;
    if (change === "permission") next.context.access.admin = false;
    if (change === "loading") next.context.access.loading = true;
    if (change === "site") next.site = { ...SITE, version: 99 };
    if (change === "operation") next.operation = { ...a.operation, version: 99 };
    if (change === "parent") next.scopeKey = "other-visit";
    r.rerender(next);
    r.rerender(a);
    expect(await saved(h.artifact, 64 * 1048576)).toBe(false);
    expect(
      h.calls.some((call) => call.request.operationName === "GetNvkeOperatorArtifactText")
    ).toBe(false);
    expect(create).not.toHaveBeenCalled();
  }
);

it.each(["read", "admin", "loading", "disabled"])(
  "does not load private metadata with unavailable %s",
  async (condition) => {
    const h = await harness(),
      a = args();
    if (condition === "read") a.context.access.read = false;
    if (condition === "admin") a.context.access.admin = false;
    if (condition === "loading") a.context.access.loading = true;
    if (condition === "disabled") a.enabled = false;
    const r = renderHook(() => useNvkeOperatorJob(a), { wrapper: h.wrapper });
    act(() => r.result.current?.onLoad());
    expect(h.calls).toHaveLength(0);
    expect(r.result.current?.canManage ?? false).toBe(false);
  }
);

it.each(["instanceId", "bundleManifestSha256", "operationId", "nativeRevision", "command"])(
  "refuses a mismatched %s in receipt metadata",
  async (field) => {
    const h = await harness();
    Object.assign(h.state.page, { [field]: "wrong-source" });
    const r = renderHook(() => useNvkeOperatorJob(args()), { wrapper: h.wrapper });
    await waitFor(() => expect(r.result.current?.canManage).toBe(true));
    act(() => r.result.current!.onLoad());
    await waitFor(() => expect(r.result.current?.error).toBe(messages.nvke.readFailed));
    expect(r.result.current?.receipt).toBeNull();
    expect(r.result.current?.canReconcile).toBe(false);
  }
);

it("drops unexpected private fields before source keys, receipt props and table rows", async () => {
  const h = await harness();
  Object.assign(h.state.authority, { clientKeyPem: "PRIVATE_SURPRISE" });
  Object.assign(h.state.page, { rawPrivateReceipt: "PRIVATE_SURPRISE" });
  Object.assign(h.state.page.items[0], { rawOutput: "PRIVATE_SURPRISE" });
  const r = renderHook(() => useNvkeOperatorJob(args()), { wrapper: h.wrapper });
  await load(h, r);
  expect(JSON.stringify(r.result.current)).not.toContain("PRIVATE_SURPRISE");
  expect(h.cache.extract()).toEqual({});
});

it("cancels an in-flight read without creating a file or another chunk request", async () => {
  const h = await harness(),
    r = renderHook(() => useNvkeOperatorJob(args()), { wrapper: h.wrapper });
  await load(h, r);
  let pending: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onDownload(h.artifact, 64 * 1048576);
  });
  await waitFor(() =>
    expect(h.calls.at(-1)?.request.operationName).toBe("GetNvkeOperatorArtifactText")
  );
  const call = h.calls.at(-1)!;
  act(() => r.result.current!.onCancelDownload());
  expect(call.request.getContext().fetchOptions.signal.aborted).toBe(true);
  await act(async () => {
    call.reply({ astroliftNvkeOperatorArtifactText: { ok: true, errors: [], data: h.chunk() } });
    expect(await pending!).toBe(false);
  });
  expect(create).not.toHaveBeenCalled();
  expect(
    h.calls.filter((call) => call.request.operationName === "GetNvkeOperatorArtifactText")
  ).toHaveLength(1);
});

it("binds reviewed read-only reconciliation and refuses old callbacks after an unconfirmed reply", async () => {
  const h = await harness(),
    a = args();
  a.operation.status = "UNCERTAIN";
  const r = renderHook(() => useNvkeOperatorJob(a), { wrapper: h.wrapper });
  await load(h, r);
  const saved = r.result.current!.onReconcile;
  let pending: Promise<boolean>;
  act(() => {
    pending = saved();
  });
  await waitFor(() =>
    expect(h.calls.at(-1)?.request.operationName).toBe("ReconcileNvkeOperatorJob")
  );
  const call = h.calls.at(-1)!;
  expect(call.request.variables.input).toEqual({
    operationId: a.operation.id,
    expectedActorId: 41,
    expectedSiteVersion: SITE.version,
    expectedAuthorityVersion: OPERATOR_AGENT_AUTHORITY.version,
    expectedOperationVersion: a.operation.version,
  });
  await act(async () => {
    call.fail();
    expect(await pending!).toBe(false);
  });
  expect(r.result.current?.error).toBe(messages.nvke.requestFailed);
  expect(await saved()).toBe(false);
  act(() => r.result.current!.onRefresh());
  expect(await saved()).toBe(false);
  expect(
    h.calls.filter((call) => call.request.operationName === "ReconcileNvkeOperatorJob")
  ).toHaveLength(1);
});

it("accepts only an advanced, matching original operation reconciliation receipt", async () => {
  const h = await harness(),
    a = args();
  a.operation.status = "UNCERTAIN";
  const r = renderHook(() => useNvkeOperatorJob(a), { wrapper: h.wrapper });
  await load(h, r);
  let pending: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReconcile();
  });
  await waitFor(() =>
    expect(h.calls.at(-1)?.request.operationName).toBe("ReconcileNvkeOperatorJob")
  );
  await act(async () => {
    h.calls.at(-1)!.reply({
      reconcileNvkeOperatorJob: {
        ok: true,
        errors: [],
        data: { ...a.operation, status: "SUCCEEDED", version: a.operation.version + 1 },
      },
    });
    expect(await pending!).toBe(true);
  });
  expect(a.onRefresh).toHaveBeenCalledOnce();
  expect(h.cache.extract()).toEqual({});
});
