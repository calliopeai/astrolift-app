import { MockedProvider } from "@apollo/client/testing/react";
import type { MockedResponse } from "@apollo/client/testing";
import { act, renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { buildSchema, validate } from "graphql";
import { readFileSync } from "node:fs";
import { beforeEach, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { MODEL_PROMPT_READINESS } from "@/graphql/playground/playground.queries";
import { PLAYGROUND_PROMPT } from "@/graphql/playground/playground.mutations";
import { PLAYGROUND_ENDPOINTS_PAGE } from "@/graphql/playground/playground.queries";
import { usePromptRelay } from "./use-prompt-relay";
import { usePlaygroundBatch } from "./use-playground-batch";
import { usePlayground } from "./use-playground";
import {
  listSavedSessions,
  loadSession,
  saveSession,
  toggleStar,
  MAX_MESSAGES,
  latestObservedPrompt,
  batchToCsv,
} from "./saved-sessions";

const identity = vi.hoisted(() => ({ org: "org-a", user: "user-a" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org } }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({ useMe: () => ({ user: { id: identity.user } }) }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const id = "019abcde-1111-7000-8000-000000000001";
const other = "019abcde-1111-7000-8000-000000000002";
const readiness = (state = "READY", eligible = true) => ({
  state,
  eligible,
  maxPromptChars: 4000,
  maxOutputTokens: 128,
  promptsPerMinute: 6,
  maxWaitSeconds: 40,
});
const read = (target = id, data: unknown = readiness(), delay = 0): MockedResponse => ({
  request: { query: MODEL_PROMPT_READINESS, variables: { id: target } },
  result: { data: { astroliftModelPromptReadiness: data } },
  delay,
});
const response = (status = "succeeded", reply = "Actual controlled reply") => ({
  testModelEndpoint: {
    ok: true,
    errors: [],
    data: { status, reply, latencyMs: 750, promptTokens: 3, completionTokens: 4, totalTokens: 7 },
  },
});
const send = (result: unknown = response(), prompt = "Concrete test prompt"): MockedResponse => ({
  request: { query: PLAYGROUND_PROMPT, variables: { input: { managedServiceId: id, prompt } } },
  result: { data: result as Record<string, unknown> },
});
function wrapper(mocks: MockedResponse[]) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider locale="en" messages={messages}>
        <MockedProvider mocks={mocks}>{children}</MockedProvider>
      </NextIntlClientProvider>
    );
  };
}
function relay(mocks: MockedResponse[]) {
  return renderHook(({ target, scope }) => usePromptRelay(scope, "user-a", target), {
    initialProps: { target: id, scope: "org-a:user-a" },
    wrapper: wrapper(mocks),
  });
}
beforeEach(() => {
  localStorage.clear();
  identity.org = "org-a";
  identity.user = "user-a";
});
it("validates actual prompt, readiness, and catalogue documents against the live SDL", () => {
  const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
  for (const doc of [MODEL_PROMPT_READINESS, PLAYGROUND_PROMPT, PLAYGROUND_ENDPOINTS_PAGE])
    expect(validate(schema, doc)).toEqual([]);
});
it("sends only the selected persisted GUID and single prompt and returns observed provider metrics", async () => {
  const called = vi.fn(() => ({ data: response() }));
  const hook = relay([read(), { ...send(), result: called }]);
  await waitFor(() => expect(hook.result.current.canSend).toBe(true));
  await act(async () => {
    expect(await hook.result.current.invoke("Concrete test prompt")).toEqual({
      ok: true,
      reply: "Actual controlled reply",
      latencyMs: 750,
      totalTokens: 7,
    });
  });
  expect(called).toHaveBeenCalledOnce();
});
it.each(["timed_out", "failed", "unexpected"])(
  "does not interpret an ok envelope with %s as a successful reply",
  async (status) => {
    const hook = relay([
      read(),
      send(response(status, "Leaked provider text must not become an assistant reply")),
    ]);
    await waitFor(() => expect(hook.result.current.canSend).toBe(true));
    await act(async () => {
      const result = await hook.result.current.invoke("Concrete test prompt");
      expect(result.ok).toBe(false);
      expect(result.reply).toBe("");
      expect(result.error).toBe(status === "timed_out" ? "timedOut" : "failed");
    });
  }
);
it.each([
  null,
  { testModelEndpoint: { ok: true, errors: [], data: null } },
  {
    testModelEndpoint: {
      ok: false,
      errors: [{ code: "PERMISSION_DENIED", field: null }],
      data: null,
    },
  },
])("rejects null or refused mutation results", async (data) => {
  const hook = relay([read(), send(data)]);
  await waitFor(() => expect(hook.result.current.canSend).toBe(true));
  await act(async () => {
    expect((await hook.result.current.invoke("Concrete test prompt")).ok).toBe(false);
  });
});
it.each([
  "UNSUPPORTED",
  "INACTIVE",
  "UNAVAILABLE",
  "UNKNOWN_HEARTBEAT",
  "STALE_HEARTBEAT",
  "UNCONFIGURED_RELAY",
  "UNCONFIGURED_MODEL",
])("never invokes when actual readiness is %s", async (state) => {
  const hook = relay([read(id, readiness(state, false))]);
  await waitFor(() => expect(hook.result.current.readiness).toBe(state));
  await act(async () =>
    expect((await hook.result.current.invoke("Concrete test prompt")).ok).toBe(false)
  );
});
it("distinguishes refused metadata from no visible endpoint", async () => {
  const hook = relay([
    {
      request: { query: MODEL_PROMPT_READINESS, variables: { id } },
      result: { errors: [{ message: "Denied", extensions: { code: "PERMISSION_DENIED" } }] },
    },
    read(other, null),
  ]);
  await waitFor(() => expect(hook.result.current.readiness).toBe("refused"));
  hook.rerender({ target: other, scope: "org-a:user-a" });
  await waitFor(() => expect(hook.result.current.readiness).toBe("missing"));
});
it("ignores delayed metadata across A→B→A identity changes and refuses stale handlers", async () => {
  const hook = relay([
    read(id, readiness(), 80),
    read(other, readiness("INACTIVE", false), 5),
    read(id, readiness("STALE_HEARTBEAT", false), 5),
  ]);
  const staleInvoke = hook.result.current.invoke;
  hook.rerender({ target: other, scope: "org-b:user-a" });
  await waitFor(() => expect(hook.result.current.readiness).toBe("INACTIVE"));
  hook.rerender({ target: id, scope: "org-a:user-a" });
  await waitFor(() => expect(hook.result.current.readiness).toBe("STALE_HEARTBEAT"));
  await new Promise((r) => setTimeout(r, 100));
  expect(hook.result.current.canSend).toBe(false);
  await act(async () => expect((await staleInvoke("Concrete test prompt")).ok).toBe(false));
});
it("blocks duplicate admissions and seventh per-user prompt without a network retry", async () => {
  const called = vi.fn(() => ({ data: response() }));
  const hook = relay([read(), { ...send(), maxUsageCount: 6, result: called, delay: 5 }]);
  await waitFor(() => expect(hook.result.current.canSend).toBe(true));
  await act(async () => {
    const first = hook.result.current.invoke("Concrete test prompt");
    expect((await hook.result.current.invoke("Concrete test prompt")).error).toBe("conflict");
    await first;
  });
  for (let i = 0; i < 5; i++)
    await act(async () =>
      expect((await hook.result.current.invoke("Concrete test prompt")).ok).toBe(true)
    );
  await act(async () =>
    expect((await hook.result.current.invoke("Concrete test prompt")).error).toBe("rateLimited")
  );
  expect(called).toHaveBeenCalledTimes(6);
});
it("validates prompts before any model request", async () => {
  const hook = relay([read()]);
  await waitFor(() => expect(hook.result.current.canSend).toBe(true));
  for (const text of [" ", "a".repeat(4001)])
    await act(async () => expect((await hook.result.current.invoke(text)).error).toBe("invalid"));
});
it("runs concrete batch prompts sequentially and cancellation keeps the admitted result only", async () => {
  let finish!: (v: { ok: boolean; reply: string }) => void;
  const invoke = vi.fn(
    () =>
      new Promise<{ ok: boolean; reply: string }>((resolve) => {
        finish = resolve;
      })
  );
  const hook = renderHook(() => usePlaygroundBatch(invoke, "org:user:endpoint", true, 4000), {
    wrapper: wrapper([]),
  });
  act(() => hook.result.current.setInput("First concrete prompt\nSecond concrete prompt"));
  let run!: Promise<void>;
  act(() => {
    run = hook.result.current.onRun();
  });
  expect(invoke).toHaveBeenCalledWith("First concrete prompt");
  act(() => hook.result.current.onCancel());
  await act(async () => {
    finish({ ok: true, reply: "Actual first reply" });
    await run;
  });
  expect(invoke).toHaveBeenCalledOnce();
  expect(hook.result.current.results).toMatchObject([
    { input: "First concrete prompt", output: "Actual first reply", ok: true },
  ]);
  expect(hook.result.current.cancelled).toBe(true);
});
it("stops a batch on failed actual result and never retries or fabricates remaining rows", async () => {
  const invoke = vi.fn().mockResolvedValue({ ok: false, reply: "", error: "timedOut" });
  const hook = renderHook(() => usePlaygroundBatch(invoke, "org:user:endpoint", true, 4000), {
    wrapper: wrapper([]),
  });
  act(() => hook.result.current.setInput("First\nSecond"));
  await act(() => hook.result.current.onRun());
  expect(invoke).toHaveBeenCalledOnce();
  expect(hook.result.current.results).toMatchObject([{ ok: false, error: "timedOut" }]);
});
it("rejects an over-limit batch and drops old-scope results after an organization change", async () => {
  const invoke = vi.fn().mockResolvedValue({ ok: true, reply: "Actual reply" });
  const hook = renderHook(({ scope }) => usePlaygroundBatch(invoke, scope, true, 4000), {
    initialProps: { scope: "org-a:user:endpoint" },
    wrapper: wrapper([]),
  });
  act(() => hook.result.current.setInput(Array(7).fill("Prompt").join("\n")));
  await act(() => hook.result.current.onRun());
  expect(invoke).not.toHaveBeenCalled();
  act(() => hook.result.current.setInput("Concrete prompt"));
  await act(() => hook.result.current.onRun());
  expect(hook.result.current.results).toHaveLength(1);
  hook.rerender({ scope: "org-b:user:endpoint" });
  expect(hook.result.current.results).toEqual([]);
});
it("isolates bounded validated local records by organization and user and ignores global demos", () => {
  const scope = "org-a:user-a";
  const record = {
    id,
    title: "Observed session",
    model: other,
    modelName: "Qwen",
    messages: [
      { role: "user" as const, content: "Concrete prompt" },
      { role: "assistant" as const, content: "Actual reply", totalTokens: 7 },
    ],
  };
  saveSession(scope, record);
  localStorage.setItem("playground:saved:index", JSON.stringify([{ title: "Old global demo" }]));
  expect(listSavedSessions(scope)).toHaveLength(1);
  expect(listSavedSessions("org-b:user-a")).toEqual([]);
  expect(listSavedSessions("org-a:user-b")).toEqual([]);
  expect(loadSession(scope, "../../foreign")).toBeNull();
  toggleStar(scope, id);
  expect(loadSession(scope, id)?.starred).toBe(true);
  expect(() =>
    saveSession(scope, { ...record, messages: Array(MAX_MESSAGES + 1).fill(record.messages[0]) })
  ).toThrow();
  localStorage.setItem(
    `playground:v2:${scope}`,
    JSON.stringify([{ ...loadSession(scope, id), model: "https://foreign/model" }])
  );
  expect(listSavedSessions(scope)).toEqual([]);
});
it("renders fresh real chat without seeded replies and resets records on identity changes", async () => {
  const catalog: MockedResponse = {
    request: {
      query: PLAYGROUND_ENDPOINTS_PAGE,
      variables: { search: null, page: 1, pageSize: 10 },
    },
    maxUsageCount: 3,
    result: {
      data: { astroliftModelEndpointsPage: { items: [], totalCount: 0, page: 1, pageSize: 10 } },
    },
  };
  const hook = renderHook(usePlayground, { wrapper: wrapper([catalog]) });
  expect(hook.result.current.messages).toEqual([]);
  expect(hook.result.current.model).toBe("");
  expect(hook.result.current.loading).toBe(false);
  act(() => hook.result.current.setTitle("Organization A draft"));
  identity.org = "org-b";
  hook.rerender();
  expect(hook.result.current.title).toBe("");
  expect(hook.result.current.savedSessions).toEqual([]);
});

it("rejects malformed readiness limits rather than enabling an unknown invocation", async () => {
  const hook = relay([read(id, { ...readiness(), maxOutputTokens: 0 })]);
  await waitFor(() => expect(hook.result.current.readiness).toBe("transport"));
  expect(hook.result.current.canSend).toBe(false);
});
it("drops an actual reply that arrives after the target or organization changed", async () => {
  const received = vi.fn(() => ({ data: response() }));
  const hook = relay([
    read(),
    { ...send(), result: received, delay: 30 },
    read(other, readiness("INACTIVE", false)),
  ]);
  await waitFor(() => expect(hook.result.current.canSend).toBe(true));
  let call!: ReturnType<typeof hook.result.current.invoke>;
  act(() => {
    call = hook.result.current.invoke("Concrete test prompt");
  });
  hook.rerender({ target: other, scope: "org-b:user-a" });
  await act(async () => {
    expect(await call).toMatchObject({ ok: false, reply: "" });
  });
  expect(received).toHaveBeenCalledOnce();
  expect(hook.result.current.contextKey).toBe(`org-b:user-a:${other}`);
});
it("server search and numbered pages keep a nonmatching selection explicit and never invent eligible models", async () => {
  const page = (search: string | null, page: number, items: unknown[]): MockedResponse => ({
    request: { query: PLAYGROUND_ENDPOINTS_PAGE, variables: { search, page, pageSize: 10 } },
    result: {
      data: { astroliftModelEndpointsPage: { items, totalCount: 31, page, pageSize: 10 } },
    },
  });
  const endpoint = {
    id,
    name: "Persisted Qwen",
    variant: "vllm",
    registeredAppSlug: "support",
    environmentName: "production",
  };
  const hook = renderHook(usePlayground, {
    wrapper: wrapper([page(null, 1, [endpoint]), page(null, 2, []), page("cloud", 1, [])]),
  });
  await waitFor(() => expect(hook.result.current.models).toHaveLength(1));
  expect(hook.result.current.totalCount).toBe(31);
  expect(hook.result.current.model).toBe("");
  act(() => hook.result.current.setPage(2));
  expect(hook.result.current.models).toEqual([]);
  await waitFor(() => expect(hook.result.current.catalogLoading).toBe(false));
  act(() => hook.result.current.setSearch("cloud"));
  expect(hook.result.current.page).toBe(1);
  await waitFor(() => expect(hook.result.current.catalogLoading).toBe(false));
  expect(hook.result.current.model).toBe("");
});
it("restored local history never auto-invokes and an unavailable stored endpoint remains refused", async () => {
  saveSession("org-a:user-a", {
    id: other,
    title: "Actual old reply",
    model: id,
    modelName: "Qwen",
    messages: [
      { role: "user", content: "Concrete past prompt" },
      { role: "assistant", content: "Observed past reply" },
    ],
  });
  window.history.replaceState(null, "", `/playground?session=${other}`);
  const page: MockedResponse = {
    request: {
      query: PLAYGROUND_ENDPOINTS_PAGE,
      variables: { search: null, page: 1, pageSize: 10 },
    },
    result: {
      data: { astroliftModelEndpointsPage: { items: [], totalCount: 0, page: 1, pageSize: 10 } },
    },
  };
  const hook = renderHook(usePlayground, { wrapper: wrapper([page, read(id, null)]) });
  await waitFor(() => expect(hook.result.current.readiness).toBe("missing"));
  expect(hook.result.current.title).toBe("Actual old reply");
  expect(hook.result.current.messages).toHaveLength(2);
  expect(hook.result.current.canSend).toBe(false);
  await act(() => hook.result.current.onSend());
  expect(hook.result.current.messages).toHaveLength(2);
  window.history.replaceState(null, "", "/playground");
});
it("invalid dates and nonfinite observed metrics cannot enter scoped local history", () => {
  const record = {
    id,
    title: "Bounded",
    model: other,
    modelName: "Qwen",
    messages: [
      {
        role: "assistant" as const,
        content: "Observed reply",
        totalTokens: Number.POSITIVE_INFINITY,
      },
    ],
  };
  expect(() => saveSession("org:user", record)).toThrow();
  localStorage.setItem(
    "playground:v2:org:user",
    JSON.stringify([
      {
        ...record,
        schema: 2,
        starred: true,
        messages: [],
        createdAt: "invalid",
        updatedAt: "invalid",
      },
    ])
  );
  expect(listSavedSessions("org:user")).toEqual([]);
});
it("a stale ready handler cannot send after A→B→A even when a fresh A read is ready", async () => {
  const hook = relay([read(), read(other, readiness("INACTIVE", false)), read()]);
  await waitFor(() => expect(hook.result.current.canSend).toBe(true));
  const stale = hook.result.current.invoke;
  hook.rerender({ target: other, scope: "org-b:user-a" });
  await waitFor(() => expect(hook.result.current.readiness).toBe("INACTIVE"));
  hook.rerender({ target: id, scope: "org-a:user-a" });
  await waitFor(() => expect(hook.result.current.canSend).toBe(true));
  await act(async () => expect((await stale("Concrete old prompt")).ok).toBe(false));
});
it("reports transport failure without an invented reply or implicit retry", async () => {
  const called = vi.fn();
  const hook = relay([
    read(),
    { ...send(), error: new Error("Controlled interrupted transport"), result: called },
  ]);
  await waitFor(() => expect(hook.result.current.canSend).toBe(true));
  await act(async () =>
    expect(await hook.result.current.invoke("Concrete test prompt")).toMatchObject({
      ok: false,
      reply: "",
      error: "transport",
    })
  );
  expect(called).not.toHaveBeenCalled();
});
it("a stale batch handler and an in-flight old-scope batch cannot invoke a new endpoint", async () => {
  let finish!: (v: { ok: boolean; reply: string }) => void;
  const invoke = vi.fn(
    () =>
      new Promise<{ ok: boolean; reply: string }>((resolve) => {
        finish = resolve;
      })
  );
  const hook = renderHook(({ scope }) => usePlaygroundBatch(invoke, scope, true, 4000), {
    initialProps: { scope: "org-a:user:endpoint" },
    wrapper: wrapper([]),
  });
  act(() => hook.result.current.setInput("First\nSecond"));
  const staleRun = hook.result.current.onRun;
  let running!: Promise<void>;
  act(() => {
    running = staleRun();
  });
  hook.rerender({ scope: "org-b:user:other-endpoint" });
  await act(() => staleRun());
  await act(async () => {
    finish({ ok: true, reply: "Actual old-scope reply" });
    await running;
  });
  expect(invoke).toHaveBeenCalledOnce();
  expect(hook.result.current.results).toEqual([]);
});

it("history matches observed reply metrics to the successful prompt instead of a later failed attempt", () => {
  const messages = [
    { role: "user" as const, content: "First concrete prompt" },
    { role: "assistant" as const, content: "First reply", totalTokens: 7 },
    { role: "user" as const, content: "Second concrete prompt" },
    { role: "assistant" as const, content: "Second reply", totalTokens: 9 },
    { role: "user" as const, content: "Later failed attempt" },
  ];
  expect(latestObservedPrompt(messages)).toEqual({ prompt: messages[2], reply: messages[3] });
});
it("batch CSV exports only observed rows and makes model-supplied formula text inert", () => {
  const csv = batchToCsv([
    { input: "=HYPERLINK(unsafe)", output: "@SUM(A1)", ok: true, latencyMs: null, totalTokens: 7 },
  ]);
  expect(csv).toContain("'@SUM(A1)");
  expect(csv).toContain("'=HYPERLINK(unsafe)");
  expect(csv).toContain("total_tokens");
  expect(csv).not.toContain("simulated");
});
