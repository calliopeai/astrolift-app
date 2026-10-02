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
import { useDispatchAgent } from "../detail/use-dispatch-agent";
import { AgentRunDetail } from "./AgentRunDetail";
import { RUN_DETAIL, RUNNING_TASK, TASK_ID } from "./agent-runs.fixtures";
import { useAgentRunDetail } from "./use-agent-run-detail";

const feedback = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: feedback }));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
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
type Mode = "success" | "refused" | "fallback" | "transport" | "empty" | "blank" | "refreshFailure";
function harness(locale: string, initial: Mode = "success") {
  let mode = initial;
  let stopped = false;
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const errors = vi.fn();
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
        if (
          !mutation &&
          stopped &&
          mode === "refreshFailure" &&
          request.operationName === "GetAgentTask"
        )
          throw new Error("RAW_REFRESH_DIAGNOSTIC");
        if (
          mutation &&
          request.operationName === "CancelTask" &&
          !["refused", "fallback"].includes(mode)
        )
          stopped = true;
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: {
            agentTask: {
              ...RUNNING_TASK,
              status: stopped ? "cancelled" : "running",
              vncEnabled: false,
              vncUrl: "",
            },
            agentTaskLogs: ["RAW_LOG_OUTPUT"],
            runAstroliftAgent: {
              ok: !["refused", "fallback"].includes(mode),
              errors:
                mode === "refused" ? [{ code: "PRECONDITION", message: "RAW_SERVER_REFUSAL" }] : [],
              data:
                mode === "empty"
                  ? null
                  : {
                      id: mode === "blank" ? "" : TASK_ID,
                      status: "queued",
                      createdAt: RUNNING_TASK.createdAt,
                    },
            },
            cancelTask: {
              ok: !["refused", "fallback"].includes(mode),
              errors:
                mode === "refused" ? [{ code: "PRECONDITION", message: "RAW_SERVER_REFUSAL" }] : [],
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
    requests,
    errors,
    mode: (next: Mode) => {
      mode = next;
    },
  };
}
const translator = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "agentRecovery" });
beforeEach(() => vi.clearAllMocks());
describe.each(locales)("agent recovery in %s", (locale) => {
  it("returns the acknowledged task ID and distinguishes a failed refresh from dispatch failure", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const after = vi.fn().mockRejectedValue(new Error("RAW_REFRESH_DIAGNOSTIC"));
    const { result, unmount } = renderHook(
      () => useDispatchAgent({ slug: "LITERAL_SLUG", name: "LITERAL_NAME" }, after),
      { wrapper: h.Wrapper }
    );
    await act(async () => {
      expect(await result.current.dispatch()).toBe(TASK_ID);
    });
    expect(after).toHaveBeenCalledWith(TASK_ID);
    expect(feedback.success).toHaveBeenCalledWith(
      t("feedback.dispatched", { name: "LITERAL_NAME" })
    );
    expect(feedback.warning).toHaveBeenCalledWith(t("feedback.dispatchRefreshFailed"));
    expect(feedback.error).not.toHaveBeenCalled();
    expect(h.requests.find((r) => r.operationName === "RunAgent")!.variables).toEqual({
      input: { agentSlug: "LITERAL_SLUG" },
    });
    expect(h.errors).not.toHaveBeenCalled();
    unmount();
  });
  it.each(["empty", "blank", "fallback", "refused", "transport"] as const)(
    "requires an actual dispatch acknowledgement and preserves %s diagnostics",
    async (mode) => {
      const h = harness(locale, mode);
      const t = translator(locale);
      const after = vi.fn();
      const { result, unmount } = renderHook(
        () => useDispatchAgent({ slug: "LITERAL_SLUG", name: "LITERAL_NAME" }, after),
        { wrapper: h.Wrapper }
      );
      await act(async () => {
        expect(await result.current.dispatch()).toBeNull();
      });
      expect(after).not.toHaveBeenCalled();
      expect(feedback.success).not.toHaveBeenCalled();
      expect(feedback.error).toHaveBeenCalledWith(
        t("feedback.couldNotDispatch", { name: "LITERAL_NAME" }),
        {
          description:
            mode === "refused"
              ? "RAW_SERVER_REFUSAL"
              : mode === "transport"
                ? "RAW_TRANSPORT_DIAGNOSTIC"
                : ["empty", "blank"].includes(mode)
                  ? t("feedback.dispatchUnconfirmed")
                  : t("feedback.dispatchFailed"),
        }
      );
      expect(h.requests.filter((r) => r.operationName === "RunAgent")).toHaveLength(1);
      expect(h.errors).not.toHaveBeenCalled();
      unmount();
    }
  );
  it("retains the localized stop dialog on refusal and retries only the original task ID", async () => {
    const h = harness(locale, "refused");
    const t = translator(locale);
    function Connected() {
      const data = useAgentRunDetail(TASK_ID);
      return <AgentRunDetail {...RUN_DETAIL} {...data} />;
    }
    render(<Connected />, { wrapper: h.Wrapper });
    await userEvent.click(await screen.findByRole("button", { name: t("run.kill") }));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText(t("run.killTitle"))).toBeInTheDocument();
    expect(within(dialog).getByText(t("run.killDescription"))).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: t("run.kill") }));
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL"));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    h.mode("success");
    await userEvent.click(within(dialog).getByRole("button", { name: t("run.kill") }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(feedback.success).toHaveBeenCalledWith(t("feedback.stopped"), {
      description: t("feedback.stoppedDescription"),
    });
    expect(screen.queryByRole("button", { name: t("run.kill") })).not.toBeInTheDocument();
    const writes = h.requests.filter((r) => r.operationName === "CancelTask");
    expect(writes).toHaveLength(2);
    for (const write of writes) expect(write.variables).toEqual({ id: TASK_ID });
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("does not turn an acknowledged stop into another write after a failed refresh", async () => {
    const h = harness(locale, "refreshFailure");
    const t = translator(locale);
    function Connected() {
      const data = useAgentRunDetail(TASK_ID);
      return <AgentRunDetail {...RUN_DETAIL} {...data} />;
    }
    render(<Connected />, { wrapper: h.Wrapper });
    await userEvent.click(await screen.findByRole("button", { name: t("run.kill") }));
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: t("run.kill") })
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(feedback.success).toHaveBeenCalledWith(t("feedback.stopped"), {
      description: t("feedback.stoppedDescription"),
    });
    expect(feedback.warning).toHaveBeenCalledWith(t("feedback.stopRefreshFailed"));
    expect(feedback.error).not.toHaveBeenCalled();
    expect(h.requests.filter((r) => r.operationName === "CancelTask")).toHaveLength(1);
    expect(h.errors).not.toHaveBeenCalled();
  });
  it.each(["fallback", "transport"] as const)(
    "refuses %s stop without claiming cancellation",
    async (mode) => {
      const h = harness(locale, mode);
      const t = translator(locale);
      const { result, unmount } = renderHook(() => useAgentRunDetail(TASK_ID), {
        wrapper: h.Wrapper,
      });
      await waitFor(() => expect(result.current.task).not.toBeNull());
      await act(async () => {
        await expect(result.current.onHardStop()).rejects.toThrow(
          mode === "fallback" ? t("feedback.stopFailed") : "RAW_TRANSPORT_DIAGNOSTIC"
        );
      });
      expect(feedback.success).not.toHaveBeenCalled();
      expect(feedback.warning).not.toHaveBeenCalled();
      expect(h.requests.filter((r) => r.operationName === "CancelTask")).toHaveLength(1);
      unmount();
    }
  );
  it("keeps result, callback URL, pod identity and server failure reason literal", () => {
    const h = harness(locale);
    const t = translator(locale);
    render(
      <AgentRunDetail
        {...RUN_DETAIL}
        terminal
        task={{
          ...RUNNING_TASK,
          status: "failed",
          failureMessage: "RAW_FAILURE_MARKER",
          podName: "LITERAL_POD",
          callbackUrl: "https://callback.invalid/literal",
          result: { marker: "RAW_RESULT_MARKER" },
        }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText(t("run.runFailed"))).toBeInTheDocument();
    for (const value of ["RAW_FAILURE_MARKER", "LITERAL_POD", "https://callback.invalid/literal"])
      expect(screen.getByText(value)).toBeInTheDocument();
    expect(screen.getByText(/RAW_RESULT_MARKER/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: t("run.kill") })).not.toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("copies the exact run ID and reports unavailable clipboard without false success", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<AgentRunDetail {...RUN_DETAIL} />, { wrapper: h.Wrapper });
    await userEvent.click(screen.getByRole("button", { name: t("run.moreActions") }));
    await userEvent.click(await screen.findByRole("menuitem", { name: t("run.copyId") }));
    expect(writeText).toHaveBeenCalledWith(TASK_ID);
    expect(feedback.success).toHaveBeenCalledWith(t("run.idCopied"));
    feedback.success.mockClear();
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    await userEvent.click(screen.getByRole("button", { name: t("run.moreActions") }));
    await userEvent.click(await screen.findByRole("menuitem", { name: t("run.copyId") }));
    expect(feedback.error).toHaveBeenCalledWith(t("run.copyFailed"));
    expect(feedback.success).not.toHaveBeenCalled();
  });
  it("renders localized missing-run recovery and complete ICU contracts", () => {
    const h = harness(locale);
    const t = translator(locale);
    render(<AgentRunDetail {...RUN_DETAIL} task={null} />, { wrapper: h.Wrapper });
    expect(screen.getByText(t("run.notFound"))).toBeInTheDocument();
    expect(screen.getByText(t("run.notFoundDescription"))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: t("run.openRuns") })).toHaveAttribute(
      "href",
      "/tasks?kind=agent"
    );
    for (const group of ["run", "feedback"] as const) {
      const source = catalogs.en.agentRecovery[group];
      const target = catalogs[locale].agentRecovery[group];
      expect(Object.keys(target)).toEqual(Object.keys(source));
      for (const [key, message] of Object.entries(target)) {
        expect(parseIcu(message as string)).toBeDefined();
        const names = (s: string) => [...s.matchAll(/\{(\w+)[,}]/g)].map((m) => m[1]).sort();
        expect(names(message as string)).toEqual(names(source[key]));
      }
    }
    expect(h.errors).not.toHaveBeenCalled();
  });
});
