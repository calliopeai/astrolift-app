import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
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
import { AgentFrame, agentStatus, runModeLabel } from "./AgentFrame";
import { AgentOverviewView } from "./AgentOverview";
import { useAgentFrame } from "./use-agent-frame";
import { useAgentOverview } from "./use-agent-overview";
import { FRAME, FAILING_FRAME } from "./agent-frame.fixtures";
import { AGENT, RUNNING_TASKS } from "./agent-detail-shell.fixtures";

const feedback = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  push: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: feedback }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: feedback.push }),
  usePathname: () => "/agents/research-scout",
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "71366f2c-4458-4c97-bf4a-476f21a25a82" } }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    loading: false,
    granted: new Set(["agent.dispatch"]),
    can: () => true,
  }),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const agent = { ...AGENT, id: "0b6c1d52-6f0e-4d3a-9d8c-2f1b7c9e4a10" };
const orgId = "71366f2c-4458-4c97-bf4a-476f21a25a82";
const taskId = RUNNING_TASKS[0].id;
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
type Mode = "success" | "refused" | "fallback" | "transport" | "unconfirmed";
function harness(locale: string, initial: Mode = "success") {
  let mode = initial;
  const errors = vi.fn();
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        const mutation = document.definitions.some(
          (d) => d.kind === "OperationDefinition" && d.operation === "mutation"
        );
        if (mutation && mode === "transport") throw new Error("RAW_TRANSPORT_DIAGNOSTIC");
        const response = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: {
            agentFleet: [agent],
            astroliftEnvironments: [
              { id: taskId, name: "LITERAL_ENV", clusterSlug: "LITERAL_CLUSTER" },
            ],
            agentEnvironmentSpecs: [{ slug: agent.slug, managedModel: true }],
            agentTasks: [],
            agent: null,
            agentTasksPage: {
              items: RUNNING_TASKS.slice(0, 2),
              totalCount: 2,
              hasMore: false,
              nextCursor: null,
            },
            runAstroliftAgent: {
              ok: mode === "success",
              errors:
                mode === "refused" ? [{ code: "PRECONDITION", message: "RAW_SERVER_REFUSAL" }] : [],
              data: { id: taskId, status: "queued", createdAt: AGENT.lastRunAt },
            },
            sendAgentTaskInput: {
              ok: !["refused", "fallback"].includes(mode),
              errors:
                mode === "refused" ? [{ code: "PRECONDITION", message: "RAW_SERVER_REFUSAL" }] : [],
              data:
                mode === "unconfirmed"
                  ? null
                  : {
                      id: taskId,
                      message: request.variables.message,
                      createdAt: AGENT.lastRunAt,
                      author: "LITERAL_ACTOR",
                      deliveredAt: null,
                    },
            },
          },
        });
        expect("errors" in response ? response.errors : undefined).toBeUndefined();
        return new Response(JSON.stringify(response), {
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
    mode: (next: Mode) => {
      mode = next;
    },
  };
}
const translator = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "agentFrame" });
beforeEach(() => vi.clearAllMocks());
describe.each(locales)("agent frame and input in %s", (locale) => {
  it("localizes all tabs and retains exact encoded routing, known modes and unknown tokens", () => {
    const h = harness(locale);
    const t = translator(locale);
    render(
      <AgentFrame {...FRAME}>
        <div>LITERAL_CONTENT</div>
      </AgentFrame>,
      { wrapper: h.Wrapper }
    );
    const nav = within(screen.getByRole("navigation", { name: t("sections") }));
    for (const key of [
      "overview",
      "runs",
      "configuration",
      "skills",
      "logs",
      "secrets",
      "access",
      "settings",
    ])
      expect(nav.getByRole("link", { name: t(`tabs.${key}`) })).toBeInTheDocument();
    expect(nav.getByRole("link", { name: t("tabs.overview") })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(nav.getByRole("link", { name: t("tabs.runs") })).toHaveAttribute(
      "href",
      `/agents/${AGENT.slug}/runs`
    );
    expect(runModeLabel("task", "schedule", (key) => t(key))).toBe(
      `${t("modes.task")} · ${t("modes.schedule")}`
    );
    expect(runModeLabel("__proto__", "FUTURE_MODE", (key) => t(key))).toBe(
      "__proto__ · FUTURE_MODE"
    );
    expect(runModeLabel("service", "service", (key) => t(key))).toBe(t("modes.service"));
    for (const count of [1, 2, 5])
      expect(
        agentStatus({ ...AGENT, runningCount: count }, (key, values) => t(key, values)).label
      ).toBe(t("status.running", { count }));
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("retains a refused run sheet and original slug until the acknowledged dispatch closes it", async () => {
    const h = harness(locale, "refused");
    const t = translator(locale);
    function Connected() {
      const { frame } = useAgentFrame(agent.slug);
      return (
        <AgentFrame {...frame}>
          <div>LITERAL_CONTENT</div>
        </AgentFrame>
      );
    }
    render(<Connected />, { wrapper: h.Wrapper });
    await userEvent.click(await screen.findByRole("button", { name: t("runNow") }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(t("runTitle", { name: agent.name }))).toBeInTheDocument();
    expect(within(dialog).getByText("LITERAL_CLUSTER")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: t("runNow") }));
    await waitFor(() =>
      expect(feedback.error).toHaveBeenCalledWith(expect.any(String), {
        description: "RAW_SERVER_REFUSAL",
      })
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    h.mode("success");
    await userEvent.click(within(dialog).getByRole("button", { name: t("runNow") }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(feedback.push).toHaveBeenCalledWith(`/agents/${agent.slug}/runs`);
    const writes = h.requests.filter((r) => r.operationName === "RunAgent");
    expect(writes).toHaveLength(2);
    for (const write of writes)
      expect(write.variables).toEqual({ input: { agentSlug: agent.slug } });
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("localizes connected model/copy feedback without altering the copied GUID", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const { result, unmount } = renderHook(() => useAgentFrame(agent.slug), { wrapper: h.Wrapper });
    await waitFor(() => expect(result.current.frame.model).toBe(t("managedModel")));
    await act(async () => result.current.frame.onCopyId());
    expect(writeText).toHaveBeenCalledWith(agent.id);
    expect(feedback.success).toHaveBeenCalledWith(t("idCopied"));
    feedback.success.mockClear();
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    await act(async () => result.current.frame.onCopyId());
    expect(feedback.error).toHaveBeenCalledWith(t("copyFailed"));
    expect(feedback.success).not.toHaveBeenCalled();
    unmount();
  });
  it.each(["refused", "fallback", "transport", "unconfirmed"] as const)(
    "retains message draft for %s input and retries the same task without translating its body",
    async (mode) => {
      const h = harness(locale, mode);
      const t = translator(locale);
      function Connected() {
        const data = useAgentOverview({ agent, orgId });
        return <AgentOverviewView {...data} fleet={null} />;
      }
      render(<Connected />, { wrapper: h.Wrapper });
      const input = await screen.findByRole("textbox", { name: t("input.ariaLabel") });
      const body = "LITERAL_MESSAGE_内容";
      await userEvent.type(input, body);
      await userEvent.click(screen.getByRole("button", { name: t("input.send") }));
      await waitFor(() =>
        expect(feedback.error).toHaveBeenCalledWith(t("input.failed"), {
          description:
            mode === "refused"
              ? "RAW_SERVER_REFUSAL"
              : mode === "transport"
                ? "RAW_TRANSPORT_DIAGNOSTIC"
                : mode === "unconfirmed"
                  ? t("input.unconfirmed")
                  : t("input.notQueued"),
        })
      );
      expect(input).toHaveValue(body);
      expect(feedback.success).not.toHaveBeenCalled();
      h.mode("success");
      await userEvent.click(screen.getByRole("button", { name: t("input.send") }));
      await waitFor(() => expect(input).toHaveValue(""));
      expect(screen.getByText(body)).toBeInTheDocument();
      expect(feedback.success).toHaveBeenCalledWith(t("input.queued"));
      const writes = h.requests.filter((r) => r.operationName === "SendAgentTaskInput");
      expect(writes).toHaveLength(2);
      for (const write of writes) expect(write.variables).toEqual({ taskId, message: body });
      expect(h.errors).not.toHaveBeenCalled();
    }
  );
  it("keeps diagnostic text literal, known fallback localized and canRun restrictions intact", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const { rerender } = render(
      <AgentFrame {...FRAME} agent={null} error="RAW_DIAGNOSTIC">
        <div />
      </AgentFrame>,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText(t("loadFailed"))).toBeInTheDocument();
    expect(screen.getByText("RAW_DIAGNOSTIC")).toBeInTheDocument();
    rerender(
      <AgentFrame {...FAILING_FRAME} canRun={false} failedRun={{ id: taskId, reason: null }}>
        <div />
      </AgentFrame>
    );
    expect(screen.getByText(t("noReason"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: t("runNow") })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: t("openRun") })).toHaveAttribute(
      "href",
      `/agents/runs/${taskId}`
    );
    rerender(
      <AgentFrame
        {...FRAME}
        agent={{ ...AGENT, runFamily: "__proto__", runMode: "CUSTOM_MODE", lastRunAt: "malformed" }}
        failedRun={{ id: taskId, reason: "RAW_FAILURE" }}
      >
        <div />
      </AgentFrame>
    );
    expect(screen.getByText(/__proto__ · CUSTOM_MODE/)).toBeInTheDocument();
    expect(screen.getByText(t("unknownDate"))).toBeInTheDocument();
    expect(screen.getByText("RAW_FAILURE")).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("has complete parseable ICU messages and unchanged slug/name/count placeholders", () => {
    const source = catalogs.en.agentFrame;
    const target = catalogs[locale].agentFrame;
    function flatten(v: Record<string, unknown>, prefix = ""): Record<string, string> {
      return Object.fromEntries(
        Object.entries(v).flatMap(([key, value]) =>
          typeof value === "string"
            ? [[prefix + key, value]]
            : Object.entries(flatten(value as Record<string, unknown>, prefix + key + "."))
        )
      );
    }
    const en = flatten(source);
    const actual = flatten(target);
    expect(Object.keys(actual)).toEqual(Object.keys(en));
    for (const [key, value] of Object.entries(actual)) {
      expect(parseIcu(value)).toBeDefined();
      const args = (s: string) => [...s.matchAll(/\{(\w+)[,}]/g)].map((m) => m[1]).sort();
      expect(args(value)).toEqual(args(en[key]));
    }
  });
});
