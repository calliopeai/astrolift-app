import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  buildSchema,
  execute,
  parse,
  validate,
  getNamedType,
  isNonNullType,
  isListType,
  isObjectType,
  isEnumType,
  type GraphQLFieldResolver,
} from "graphql";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useLocalListState } from "@/components/list/use-list-state";
import { LiveLogTerminal } from "@/components/observability/LiveLogTerminal";
import { AgentRunScreen } from "./AgentRunScreen";
import { AGENT_RUNS_LIST, localizedAgentRunsList, runDot } from "./agent-runs-list";
import { RUN, RUN_EMPTY, type RunData } from "./agent-build-run.fixtures";
import { useAgentRun } from "./use-agent-run";
const route = vi.hoisted(() => ({ query: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(route.query),
  usePathname: () => "/agents/literal-agent/runs",
  useRouter: () => ({ replace: route.replace }),
}));
const terminals = vi.hoisted(() => ({
  clients: [] as Array<{
    write: ReturnType<typeof vi.fn>;
    reset: ReturnType<typeof vi.fn>;
    dispose: ReturnType<typeof vi.fn>;
  }>,
}));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    buffer = { active: { viewportY: 0, baseY: 0 } };
    write = vi.fn((_text: string, callback: () => void) => callback());
    reset = vi.fn();
    dispose = vi.fn();
    loadAddon = vi.fn();
    open = vi.fn();
    scrollToBottom = vi.fn();
    constructor() {
      terminals.clients.push(this);
    }
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit = vi.fn();
  },
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const tFor = (locale: string, namespace = "agentRunTab") =>
  createTranslator({ locale, messages: catalogs[locale], namespace });
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const orgId = "71366f2c-4458-4c97-bf4a-476f21a25a82",
  workloadId = "71366f2c-4458-4c97-bf4a-476f21a25a83";
const fieldResolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
  object,
  _args,
  _context,
  info
) => {
  if (object && info.fieldName in object) return object[info.fieldName];
  const type = getNamedType(info.returnType),
    wrapped = isNonNullType(info.returnType) ? info.returnType.ofType : info.returnType;
  if (isListType(wrapped)) return [];
  if (isObjectType(type)) return {};
  if (isEnumType(type)) return type.getValues()[0].name;
  return type.name === "Boolean" ? false : ["Int", "Float"].includes(type.name) ? 0 : "";
};
function harness(locale: string, refused = false) {
  let fail = refused;
  const requests: Array<{ operationName: string; variables: Record<string, unknown> }> = [],
    errors = vi.fn();
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        if (fail) throw new Error("RAW_RUN_READ_DIAGNOSTIC");
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: {
            agentTasksPage: {
              items: RUN.rows,
              nextCursor: "RAW_CURSOR",
              totalCount: 42,
              hasMore: true,
            },
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
    client,
    requests,
    errors,
    recover: () => {
      fail = false;
    },
  };
}
function Runs({
  data = RUN,
  view,
  renderLogs = () => null,
}: {
  data?: RunData;
  view?: string;
  renderLogs?: (taskId: string, running: boolean) => ReactNode;
}) {
  const t = useTranslations("agentRunTab"),
    activity = useTranslations("agentActivity"),
    list = useLocalListState(localizedAgentRunsList(t, activity), view ? { view } : {});
  return <AgentRunScreen {...data} list={list} renderLogs={renderLogs} />;
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
  route.query = "";
  route.replace.mockReset();
  terminals.clients.length = 0;
  window.localStorage.clear();
});
describe.each(locales)("agent run catalog and data hook in %s", (locale) => {
  it("translates presentation while preserving filters, list identity, order and paging", () => {
    const t = tFor(locale),
      activity = tFor(locale, "agentActivity"),
      def = localizedAgentRunsList(t, activity);
    expect(def.id).toBe(AGENT_RUNS_LIST.id);
    expect(def.defaultSort).toEqual(AGENT_RUNS_LIST.defaultSort);
    expect(def.pageSizes).toEqual([25, 50, 100]);
    expect(def.fields[0].options?.map((o) => o.value)).toEqual(
      AGENT_RUNS_LIST.fields[0].options?.map((o) => o.value)
    );
    expect(def.views.map((v) => ({ key: v.key, filters: v.filters }))).toEqual(
      AGENT_RUNS_LIST.views.map((v) => ({ key: v.key, filters: v.filters }))
    );
    expect(def.views.find((v) => v.key === "mine")?.note).toBe(t("mineNote"));
    expect(def.fields[0].options?.find((o) => o.value === "timed_out")?.label).toBe(
      activity("statuses.timed_out")
    );
  });
  it("executes the current SDL through HttpLink with exact org/workload and untranslated cursor/filter values", async () => {
    const h = harness(locale);
    route.query = "view=failed&q=literal-run-token&after=RAW_CURSOR";
    const { result, rerender } = renderHook(() => useAgentRun({ id: workloadId }, orgId), {
      wrapper: h.Wrapper,
    });
    await waitFor(() => expect(result.current.rows).toHaveLength(RUN.rows.length));
    expect(h.requests[0].variables).toEqual({
      orgId,
      workloadId,
      status: "failed",
      search: "literal-run-token",
      limit: 25,
      after: "RAW_CURSOR",
    });
    expect(result.current.nextCursor).toBe("RAW_CURSOR");
    expect(result.current.list.definition.searchPlaceholder).toBe(tFor(locale)("search"));
    route.query = "view=mine";
    rerender();
    await waitFor(() => expect(h.requests.length).toBe(2));
    expect(h.requests[1].variables).toEqual({
      orgId,
      workloadId,
      status: null,
      search: null,
      limit: 25,
      after: null,
    });
    expect(h.requests[1].variables).not.toHaveProperty("startedBy");
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("keeps failed HTTP diagnostics literal and retries the same exact read", async () => {
    const h = harness(locale, true),
      { result } = renderHook(() => useAgentRun({ id: workloadId }, orgId), { wrapper: h.Wrapper });
    await waitFor(() => expect(result.current.error?.message).toBe("RAW_RUN_READ_DIAGNOSTIC"));
    expect(result.current.rows).toEqual([]);
    h.recover();
    act(() => result.current.onRetry());
    await waitFor(() => expect(result.current.rows).toHaveLength(RUN.rows.length));
    expect(h.requests[1].variables).toEqual(h.requests[0].variables);
    h.client.stop();
  });
  it("renders known status and relative dates while preserving IDs, future/prototype tokens and invalid timestamps", () => {
    const h = harness(locale),
      t = tFor(locale);
    render(
      <Runs
        data={{
          ...RUN,
          rows: [
            RUN.rows[0],
            {
              ...RUN.rows[3],
              status: "future_status_v2",
              startedAt: "RAW_INVALID_DATE",
              finishedAt: null,
            },
            { ...RUN.rows[4], status: "__proto__" },
          ],
        }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByRole("columnheader", { name: t("status") })).toBeInTheDocument();
    expect(screen.getByText(t("runningNow"))).toHaveAttribute("title", "running");
    for (const status of ["future_status_v2", "__proto__"])
      expect(screen.getByText(status)).toHaveAttribute("title", status);
    expect(runDot("__proto__")).toBe("muted");
    expect(screen.getByText(t("unknownDate"))).toHaveAttribute("title", "RAW_INVALID_DATE");
    expect(
      screen.getByText(
        new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(-2, "minute")
      )
    ).toHaveAttribute("title", RUN.rows[0].startedAt);
    expect(screen.getByText(RUN.rows[0].id).closest("a")).toHaveAttribute(
      "href",
      `/agents/runs/${RUN.rows[0].id}`
    );
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("discloses Mine's actual scope beside a translated empty state", () => {
    const h = harness(locale),
      t = tFor(locale);
    render(<Runs data={RUN_EMPTY} view="mine" />, { wrapper: h.Wrapper });
    expect(screen.getByText(t("mineNote"))).toBeInTheDocument();
    expect(
      screen.getByText(
        tFor(locale, "shared.list")("emptyView", {
          label: locale.startsWith("en") ? t("runs").toLowerCase() : t("runs"),
          view: t("views.mine"),
        })
      )
    ).toBeInTheDocument();
  });
  it("passes only the selected headless task ID and live state to the localized watch slot", async () => {
    const h = harness(locale),
      t = tFor(locale),
      renderLogs = vi.fn((id: string, live: boolean) => (
        <span>
          {id}:{String(live)}
        </span>
      )),
      view = render(<Runs renderLogs={renderLogs} />, { wrapper: h.Wrapper });
    await userEvent.click(
      screen.getAllByRole("button", {
        name: tFor(locale, "shared.list")("rowActions", { label: t("runs") }),
      })[1]
    );
    await userEvent.click(await screen.findByRole("menuitem", { name: t("watchLogs") }));
    expect(screen.getByRole("dialog")).toHaveTextContent(t("logsTitle"));
    expect(renderLogs).toHaveBeenLastCalledWith(RUN.rows[1].id, true);
    view.rerender(
      <Runs
        renderLogs={renderLogs}
        data={{
          ...RUN,
          rows: RUN.rows.map((row) =>
            row.id === RUN.rows[1].id ? { ...row, status: "completed" } : row
          ),
        }}
      />
    );
    expect(renderLogs).toHaveBeenLastCalledWith(RUN.rows[1].id, false);
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("renders terminal waiting/unavailable/error chrome without rewriting diagnostics", () => {
    const h = harness(locale),
      t = tFor(locale, "agentRunTab.terminal"),
      props = { taskId: RUN.rows[0].id, running: true, lines: null, error: null, loading: false };
    const view = render(<LiveLogTerminal {...props} />, { wrapper: h.Wrapper });
    expect(screen.getByRole("log", { name: t("label") })).toBeInTheDocument();
    expect(screen.getByText(t("waiting"))).toBeInTheDocument();
    view.rerender(<LiveLogTerminal {...props} running={false} lines={[]} />);
    expect(screen.getByText(t("empty"))).toBeInTheDocument();
    view.rerender(<LiveLogTerminal {...props} error="RAW_TERMINAL_DIAGNOSTIC" />);
    expect(screen.getByRole("status")).toHaveTextContent(
      `${t("readFailed")} RAW_TERMINAL_DIAGNOSTIC`
    );
    expect(terminals.clients).toHaveLength(1);
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("has all 28 nonempty parsable ICU messages", () => {
    const baseline = leaves(catalogs.en.agentRunTab),
      localized = leaves(catalogs[locale].agentRunTab);
    expect(Object.keys(localized).sort()).toEqual(Object.keys(baseline).sort());
    expect(Object.keys(localized)).toHaveLength(28);
    for (const text of Object.values(localized)) {
      expect(text.trim()).not.toBe("");
      expect(() => parseIcu(text)).not.toThrow();
    }
  });
});
it("changes locale without recreating/resetting/repainting terminal, then appends only new literal output", () => {
  const lines = ["RAW_LOG_A", "RAW_LOG_B"],
    props = { taskId: RUN.rows[0].id, running: true, lines, error: null, loading: false };
  const content = (locale: string, output: string[]) => (
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
      <LiveLogTerminal {...props} lines={output} />
    </NextIntlClientProvider>
  );
  const view = render(content("fr", lines));
  expect(terminals.clients).toHaveLength(1);
  const terminal = terminals.clients[0];
  expect(terminal.write).toHaveBeenCalledExactlyOnceWith(
    "RAW_LOG_A\r\nRAW_LOG_B\r\n",
    expect.any(Function)
  );
  view.rerender(content("ja", lines));
  expect(
    screen.getByRole("log", { name: tFor("ja", "agentRunTab.terminal")("label") })
  ).toBeInTheDocument();
  expect(terminals.clients).toHaveLength(1);
  expect(terminal.reset).toHaveBeenCalledTimes(1);
  expect(terminal.dispose).not.toHaveBeenCalled();
  expect(terminal.write).toHaveBeenCalledTimes(1);
  view.rerender(content("ja", ["RAW_LOG_B", "RAW_LOG_C"]));
  expect(terminal.write).toHaveBeenLastCalledWith("RAW_LOG_C\r\n", expect.any(Function));
});
