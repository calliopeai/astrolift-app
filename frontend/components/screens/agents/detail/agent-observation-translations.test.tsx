import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  buildSchema,
  execute,
  getNamedType,
  isEnumType,
  isListType,
  isNonNullType,
  isObjectType,
  parse,
  validate,
  type GraphQLFieldResolver,
} from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { TooltipProvider } from "@/components/ui/tooltip";
import { VncViewer } from "@/components/observability/VncViewer";
import { LogView } from "@/components/run/LogView";
import { AgentVncPopoutScreen } from "../runs/AgentVncPopout";
import { useAgentVncPopout } from "../runs/use-agent-vnc-popout";
import { VNC_POPOUT } from "../runs/agent-runs.fixtures";
import { AgentObserveScreen } from "./AgentObserve";
import { AgentTaskLogsView } from "./AgentTaskLogs";
import { useAgentObserve } from "./use-agent-observe";
import { useAgentTaskLogs } from "./use-agent-task-logs";
import { FAILED_TASK, RUNNING_TASK } from "./agent-observe-secure.fixtures";
const rfb = vi.hoisted(() => ({
  clients: [] as Array<
    EventTarget & { scaleViewport: boolean; disconnect: ReturnType<typeof vi.fn>; url: string }
  >,
  fail: false,
}));
vi.mock("@novnc/novnc", () => ({
  default: class FakeRfb extends EventTarget {
    scaleViewport = false;
    background = "";
    disconnect = vi.fn();
    constructor(
      _container: HTMLElement,
      public url: string
    ) {
      super();
      if (rfb.fail) throw new Error("RAW_RFB_CONSTRUCTOR_DIAGNOSTIC");
      rfb.clients.push(this);
    }
  },
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const orgId = "71366f2c-4458-4c97-bf4a-476f21a25a82";
const tFor = (locale: string, namespace: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace });
const fieldResolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
  object,
  _args,
  _context,
  info
) => {
  if (object && info.fieldName in object) return object[info.fieldName];
  const type = getNamedType(info.returnType);
  const wrapped = isNonNullType(info.returnType) ? info.returnType.ofType : info.returnType;
  if (isListType(wrapped)) return [];
  if (isObjectType(type)) return {};
  if (isEnumType(type)) return type.getValues()[0].name;
  return type.name === "Boolean" ? false : ["Int", "Float"].includes(type.name) ? 0 : "";
};
type Mode =
  | "normal"
  | "taskRefused"
  | "taskMissing"
  | "observeEmpty"
  | "observeRefused"
  | "earlierRefused"
  | "futureLevel"
  | "prototypeLevel";
function harness(locale: string, initial: Mode = "normal") {
  let mode = initial;
  let refreshed = false;
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const errors = vi.fn();
  const page = (messages: string[], cursor: string | null) => ({
    items: messages.map((message, i) => ({
      id: `log-${message}-${i}`,
      timestamp: "2026-10-02T00:00:00Z",
      message,
      level:
        mode === "futureLevel"
          ? "future_level_v2"
          : mode === "prototypeLevel"
            ? "__proto__"
            : "warn",
      stream: "stderr",
      podName: "literal-pod",
      container: "literal-container",
    })),
    nextCursor: cursor,
    hasMore: cursor !== null,
    pageSize: 200,
    liveOnly: true,
    windowLimited: true,
    expiresAt: "2026-10-02T00:05:00Z",
  });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        if (
          (mode === "taskRefused" && request.operationName === "GetAgentTask") ||
          (mode === "observeRefused" && request.operationName === "ListAgentTasksPage") ||
          (mode === "earlierRefused" && request.variables.cursor)
        )
          throw new Error("RAW_READ_DIAGNOSTIC");
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: {
            agentTask:
              mode === "taskMissing"
                ? null
                : { ...VNC_POPOUT.task!, status: "completed", vncUrl: "" },
            agentTasksPage: {
              items: mode === "observeEmpty" ? [] : [RUNNING_TASK],
              totalCount: mode === "observeEmpty" ? 0 : 1,
              nextCursor: null,
              hasMore: false,
            },
            agentTaskLogsPage: request.variables.cursor
              ? page(["RAW_EARLIER_LOG"], null)
              : page([refreshed ? "RAW_REFRESHED_LOG" : "RAW_LATEST_LOG"], "literal-cursor"),
          },
        });
        expect("errors" in result ? result.errors : undefined).toBeUndefined();
        return new Response(JSON.stringify(result), {
          headers: { "Content-Type": "application/json" },
        });
      },
    }),
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={errors}
      >
        <ApolloProvider client={client}>
          <TooltipProvider>{children}</TooltipProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    Wrapper,
    requests,
    errors,
    refresh: () => {
      refreshed = true;
      mode = "normal";
    },
    client,
  };
}
function Session() {
  return <AgentVncPopoutScreen {...useAgentVncPopout(VNC_POPOUT.taskId)} />;
}
function Logs() {
  return <AgentTaskLogsView task={RUNNING_TASK} {...useAgentTaskLogs(RUNNING_TASK.id)} />;
}
function Observe() {
  return (
    <AgentObserveScreen
      {...useAgentObserve({ id: "71366f2c-4458-4c97-bf4a-476f21a25a83" }, orgId)}
      slug="literal/agent 空"
      logs={<span>RAW_CONNECTED_LOG_SLOT</span>}
    />
  );
}
function leaves(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([k, v]) =>
      typeof v === "string"
        ? [[prefix + k, v]]
        : Object.entries(leaves(v as Record<string, unknown>, prefix + k + "."))
    )
  );
}
beforeEach(() => {
  rfb.clients.length = 0;
  rfb.fail = false;
});
describe.each(locales)("agent observation in %s", (locale) => {
  it("reads an exact terminal task and shows ended session metadata without opening a relay", async () => {
    const h = harness(locale);
    const t = tFor(locale, "agentObservation.session");
    render(<Session />, { wrapper: h.Wrapper });
    expect(await screen.findByText(t("ended"))).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: t("title") })).toBeInTheDocument();
    expect(screen.getByText(VNC_POPOUT.taskId)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: t("back") })).toHaveAttribute(
      "href",
      "/agents?tab=theatre"
    );
    expect(h.requests.find((r) => r.operationName === "GetAgentTask")?.variables).toEqual({
      id: VNC_POPOUT.taskId,
    });
    expect(rfb.clients).toHaveLength(0);
    expect(h.errors).not.toHaveBeenCalled();
  });
  it.each(["taskRefused", "taskMissing"] as const)(
    "keeps %s session reads honest with no invented live target",
    async (mode) => {
      const h = harness(locale, mode);
      const t = tFor(locale, "agentObservation.session");
      render(<Session />, { wrapper: h.Wrapper });
      expect(
        await screen.findByText(mode === "taskRefused" ? "RAW_READ_DIAGNOSTIC" : t("notFound"))
      ).toBeInTheDocument();
      expect(rfb.clients).toHaveLength(0);
      expect(h.errors).not.toHaveBeenCalled();
    }
  );
  it("localizes unavailable/loading session controls while preserving literal task identity", () => {
    const h = harness(locale);
    const t = tFor(locale, "agentObservation.session");
    const { rerender } = render(<AgentVncPopoutScreen {...VNC_POPOUT} task={null} loading />, {
      wrapper: h.Wrapper,
    });
    expect(screen.getByRole("status", { name: t("loading") })).toBeInTheDocument();
    rerender(
      <AgentVncPopoutScreen {...VNC_POPOUT} task={{ ...VNC_POPOUT.task!, vncEnabled: false }} />
    );
    expect(screen.getByText(t("notCapable"))).toBeInTheDocument();
    expect(screen.getByText(VNC_POPOUT.taskId)).toBeInTheDocument();
    expect(rfb.clients).toHaveLength(0);
  });
  it("keeps noVNC relay and client pinned through locale/view-mode changes and renders raw security reasons", async () => {
    const h = harness(locale);
    const t = tFor(locale, "agentObservation.viewer");
    const user = userEvent.setup();
    const { rerender } = render(
      <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
        <VncViewer vncPath="/app/vnc/literal-task" />
      </NextIntlClientProvider>,
      { wrapper: h.Wrapper }
    );
    await waitFor(() => expect(rfb.clients).toHaveLength(1));
    const client = rfb.clients[0];
    expect(client.url).toMatch(/\/app\/vnc\/literal-task$/);
    expect(screen.getByText(t("connecting"))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: t("actual") })).toBeDisabled();
    act(() => client.dispatchEvent(new Event("connect")));
    expect(screen.getByRole("button", { name: t("actual") })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: t("actual") }));
    expect(client.scaleViewport).toBe(false);
    await user.click(screen.getByRole("button", { name: t("fit") }));
    expect(client.scaleViewport).toBe(true);
    const next = locale === "ja" ? "fr" : "ja";
    rerender(
      <NextIntlClientProvider locale={next} messages={catalogs[next]}>
        <VncViewer vncPath="/app/vnc/literal-task" />
      </NextIntlClientProvider>
    );
    expect(
      screen.getByRole("button", { name: tFor(next, "agentObservation.viewer")("actual") })
    ).toBeEnabled();
    expect(rfb.clients).toHaveLength(1);
    expect(client.disconnect).not.toHaveBeenCalled();
    act(() =>
      client.dispatchEvent(
        new CustomEvent("securityfailure", { detail: { reason: "RAW_RFB_REFUSAL" } })
      )
    );
    expect(screen.getByText("RAW_RFB_REFUSAL")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: tFor(next, "agentObservation.viewer")("actual") })
    ).toBeDisabled();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("renders translated noVNC defaults and literal constructor failures", async () => {
    const h = harness(locale);
    const t = tFor(locale, "agentObservation.viewer");
    const { unmount } = render(<VncViewer vncPath="/literal-relay" />, { wrapper: h.Wrapper });
    await waitFor(() => expect(rfb.clients).toHaveLength(1));
    const client = rfb.clients[0];
    act(() => client.dispatchEvent(new CustomEvent("securityfailure", { detail: {} })));
    expect(screen.getByText(t("denied"))).toBeInTheDocument();
    act(() => client.dispatchEvent(new CustomEvent("disconnect", { detail: { clean: true } })));
    expect(screen.getByText(t("closed"))).toBeInTheDocument();
    act(() => client.dispatchEvent(new CustomEvent("disconnect", { detail: { clean: false } })));
    expect(screen.getByText(t("error"))).toBeInTheDocument();
    unmount();
    expect(client.disconnect).toHaveBeenCalledOnce();
    rfb.fail = true;
    render(<VncViewer vncPath="/literal-relay" />, { wrapper: h.Wrapper });
    expect(await screen.findByText("RAW_RFB_CONSTRUCTOR_DIAGNOSTIC")).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("reads earlier log pages with exact ID/cursor, preserving stderr/log bodies and localized controls", async () => {
    const h = harness(locale);
    const t = tFor(locale, "agentObservation.logs");
    const user = userEvent.setup();
    render(<Logs />, { wrapper: h.Wrapper });
    expect(await screen.findByText("[stderr] RAW_LATEST_LOG")).toBeInTheDocument();
    expect(screen.getByText(t("live"))).toBeInTheDocument();
    expect(screen.getByText(t("window"))).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: t("loadEarlier") }));
    expect(await screen.findByText("[stderr] RAW_EARLIER_LOG")).toBeInTheDocument();
    expect(
      h.requests
        .filter((r) => r.operationName === "AgentTaskLogsPage")
        .map((r) => r.variables.cursor)
    ).toEqual([null, "literal-cursor"]);
    expect(
      h.requests.every((r) => r.variables.id === RUNNING_TASK.id && r.variables.limit === 200)
    ).toBe(true);
    expect(h.errors).not.toHaveBeenCalled();
  });
  it.each(["futureLevel", "prototypeLevel"] as const)(
    "renders %s wire log levels literally with neutral styling instead of inherited table lookups",
    async (mode) => {
      const h = harness(locale, mode);
      render(<Logs />, { wrapper: h.Wrapper });
      expect(await screen.findByText("[stderr] RAW_LATEST_LOG")).toBeInTheDocument();
      const token = mode === "futureLevel" ? "future_level_v2" : "__proto__";
      expect(screen.getByText(token)).toHaveAttribute("title", token);
      expect(screen.getByText(token).closest("[data-level]")).toHaveAttribute(
        "data-level",
        "other"
      );
      expect(h.errors).not.toHaveBeenCalled();
    }
  );
  it("keeps displayed logs and raw refusal diagnostics until explicit refresh recovers the latest page", async () => {
    const h = harness(locale, "earlierRefused");
    const t = tFor(locale, "agentObservation.logs");
    const user = userEvent.setup();
    render(<Logs />, { wrapper: h.Wrapper });
    expect(await screen.findByText("[stderr] RAW_LATEST_LOG")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: t("loadEarlier") }));
    expect(await screen.findByText("RAW_READ_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.getByText("[stderr] RAW_LATEST_LOG")).toBeInTheDocument();
    h.refresh();
    await user.click(screen.getByRole("button", { name: t("refresh") }));
    expect(await screen.findByText("[stderr] RAW_REFRESHED_LOG")).toBeInTheDocument();
    expect(screen.queryByText("RAW_READ_DIAGNOSTIC")).not.toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it.each(["normal", "observeEmpty", "observeRefused"] as const)(
    "keeps %s overview read state and exact encoded runs destination",
    async (mode) => {
      const h = harness(locale, mode);
      const t = tFor(locale, "agentObservation.logs");
      render(<Observe />, { wrapper: h.Wrapper });
      expect(
        await screen.findByText(
          mode === "normal"
            ? "RAW_CONNECTED_LOG_SLOT"
            : mode === "observeEmpty"
              ? t("noRuns")
              : "RAW_READ_DIAGNOSTIC"
        )
      ).toBeInTheDocument();
      if (mode === "observeEmpty")
        expect(screen.getByRole("link", { name: t("runs") })).toHaveAttribute(
          "href",
          "/agents/literal%2Fagent%20%E7%A9%BA/runs"
        );
      expect(
        h.requests.find((r) => r.operationName === "ListAgentTasksPage")?.variables.orgId
      ).toBe(orgId);
      expect(h.errors).not.toHaveBeenCalled();
    }
  );
  it("localizes shared log defaults/follow/download/new-line counts without rewriting technical output or identifiers", async () => {
    const h = harness(locale);
    const t = tFor(locale, "runLog");
    const user = userEvent.setup();
    const lines = [{ ts: "2026-10-02T00:00:00Z", message: "RAW_LOG_BODY", level: "warn" as const }];
    const { rerender } = render(<LogView lines={lines} onDownload={() => {}} />, {
      wrapper: h.Wrapper,
    });
    expect(screen.getByRole("log", { name: t("title") })).toBeInTheDocument();
    expect(screen.getByText("RAW_LOG_BODY")).toBeInTheDocument();
    expect(screen.getByText("WRN")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: t("download") })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: t("follow") }));
    rerender(
      <LogView lines={[...lines, { ...lines[0], message: "RAW_NEW_LOG" }]} onDownload={() => {}} />
    );
    const jump = screen.getByRole("button", { name: (n) => n.startsWith(t("jump")) });
    expect(jump).toHaveTextContent(t("newLines", { count: 1 }));
    await user.click(jump);
    expect(
      screen.queryByRole("button", { name: (n) => n.startsWith(t("jump")) })
    ).not.toBeInTheDocument();
    rerender(<LogView lines={[]} />);
    expect(screen.getByText(t("empty"))).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("shows translated spawn guidance while preserving failure bodies and unknown status tokens", () => {
    const h = harness(locale);
    const t = tFor(locale, "agentObservation.logs");
    const props = {
      task: FAILED_TASK,
      lines: [],
      loading: false,
      error: null,
      onRetry: () => {},
      onDownload: () => {},
      onLoadEarlier: () => undefined,
      onRefresh: () => {},
      hasMore: false,
      loadingEarlier: false,
      pageError: null,
      liveOnly: true,
      windowLimited: false,
    };
    const { rerender } = render(<AgentTaskLogsView {...props} />, { wrapper: h.Wrapper });
    expect(screen.getByText(t("spawnFailed"))).toBeInTheDocument();
    expect(screen.getByText(FAILED_TASK.failureMessage!)).toBeInTheDocument();
    rerender(<AgentTaskLogsView {...props} task={{ ...FAILED_TASK, status: "__proto__" }} />);
    expect(screen.getByText("__proto__")).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("parses all messages with matching argument names and natural locale count rendering", () => {
    for (const namespace of ["agentObservation", "runLog"]) {
      const en = leaves(catalogs.en[namespace]);
      const actual = leaves(catalogs[locale][namespace]);
      expect(Object.keys(actual).sort()).toEqual(Object.keys(en).sort());
      for (const [key, value] of Object.entries(actual)) {
        expect(() => parseIcu(value)).not.toThrow();
        const args = (s: string) => (s.match(/\{(id|count)[,}]/g) ?? []).map((v) => v.slice(1, -1));
        expect(args(value)).toEqual(args(en[key]));
      }
    }
    for (const count of [0, 1, 2, 5])
      expect(tFor(locale, "runLog")("newLines", { count })).not.toContain("{count");
    expect(tFor(locale, "agentObservation.logs")("runTitle", { id: "RAW_ID" })).toContain("RAW_ID");
  });
});
