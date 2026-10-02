import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider, useQuery } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
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
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { CI_SETUP_PROBLEMS } from "./app-ci-observability-section.fixtures";
import { formatRelativeWebhookInstall } from "./ci-setup-meta";
import { useCiSetup } from "./use-ci-setup";
import { WorkflowSyncStatusControl } from "./WorkflowSyncStatusControl";
const feedback = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: feedback }));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const appId = "0b6c1d52-6f0e-4d3a-9d8c-2f1b7c9e4a10";
const status = {
  ...CI_SETUP_PROBLEMS.ciWorkflowSyncStatus!,
  repoText: "RAW_REPO_YAML",
  renderedText: "RAW_RENDERED_YAML",
  prUrl: "",
};
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
type Mode = "success" | "refused" | "fallback" | "transport" | "unknown" | "pr";
function harness(locale: string, initial: Mode = "success", granted = true) {
  let mode = initial;
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
        const rootValue: Record<string, unknown> = {
          astroliftPlatformApiUrl: "https://api.invalid",
          astroliftApp: { id: appId, slug: "literal-app", ciWorkflowSyncStatus: status },
        };
        for (const name of [
          "refreshCiWorkflowSyncStatus",
          "resyncAstroliftCiWorkflow",
          "openCiWorkflowReconcilePr",
          "pullCiWorkflowFromRepo",
        ])
          rootValue[name] = {
            ok: !["refused", "fallback"].includes(mode),
            errors:
              mode === "refused"
                ? [{ code: "PERMISSION_DENIED", message: "RAW_SERVER_REFUSAL" }]
                : [],
            data: {
              ...status,
              state: mode === "unknown" ? "future_state_v2" : "conflict",
              prUrl: mode === "pr" ? "https://source.invalid/pull/123" : "",
            },
          };
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          rootValue,
          fieldResolver,
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
          <PermissionsProvider
            value={{ granted: new Set(granted ? ["app.update"] : []), loading: false }}
          >
            <TooltipProvider>{children}</TooltipProvider>
          </PermissionsProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  function useConnected() {
    useQuery(GET_APP, {
      variables: { slug: "literal-app", includeDrift: true },
      fetchPolicy: "network-only",
    });
    return useCiSetup({ appId, appSlug: "literal-app" });
  }
  return {
    Wrapper,
    requests,
    errors,
    useConnected,
    mode: (next: Mode) => {
      mode = next;
    },
  };
}
const translator = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "apps.overview.ciWorkflow" });
beforeEach(() => vi.clearAllMocks());
describe.each(locales)("CI workflow recovery in %s", (locale) => {
  it.each(["onRefresh", "onPush", "onReconcile", "onPull"] as const)(
    "localizes %s through real HTTP, refetches and preserves exact app ID",
    async (action) => {
      const h = harness(locale);
      const t = translator(locale);
      const { result, unmount } = renderHook(h.useConnected, { wrapper: h.Wrapper });
      await waitFor(() => expect(h.requests.some((r) => r.operationName === "GetApp")).toBe(true));
      const reads = h.requests.filter((r) => r.operationName === "GetApp").length;
      await act(() => result.current.workflowSync[action]());
      const success = {
        onRefresh: t("feedback.refreshed", { state: t("states.conflict.label") }),
        onPush: t("feedback.pushed"),
        onReconcile: t("feedback.reconciled"),
        onPull: t("feedback.pulled"),
      }[action];
      expect(feedback.success).toHaveBeenCalledWith(success);
      expect(h.requests.filter((r) => r.operationName === "GetApp").length).toBeGreaterThan(reads);
      const writes = h.requests.filter(
        (r) => r.operationName !== "GetApp" && r.operationName !== "GetPlatformApiUrl"
      );
      expect(writes).toHaveLength(1);
      expect(writes[0].variables).toEqual({ input: { appId } });
      h.mode("fallback");
      const failure = {
        onRefresh: "refreshFailed",
        onPush: "pushFailed",
        onReconcile: "reconcileFailed",
        onPull: "pullFailed",
      }[action];
      await act(async () => {
        if (action === "onPull")
          await expect(result.current.workflowSync[action]()).rejects.toThrow(
            t(`feedback.${failure}`)
          );
        else await result.current.workflowSync[action]();
      });
      if (action !== "onPull")
        expect(feedback.error).toHaveBeenCalledWith(t(`feedback.${failure}`));
      h.mode("refused");
      await act(async () => {
        if (action === "onPull")
          await expect(result.current.workflowSync[action]()).rejects.toThrow("RAW_SERVER_REFUSAL");
        else await result.current.workflowSync[action]();
      });
      if (action !== "onPull") expect(feedback.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL");
      h.mode("transport");
      await act(async () => {
        if (action === "onPull")
          await expect(result.current.workflowSync[action]()).rejects.toThrow(
            "RAW_TRANSPORT_DIAGNOSTIC"
          );
        else await result.current.workflowSync[action]();
      });
      if (action !== "onPull")
        expect(feedback.error).toHaveBeenCalledWith("RAW_TRANSPORT_DIAGNOSTIC");
      expect(h.errors).not.toHaveBeenCalled();
      unmount();
    }
  );
  it("localizes PR feedback and preserves its destination, while future drift states stay literal", async () => {
    const h = harness(locale, "pr");
    const t = translator(locale);
    const { result, unmount } = renderHook(h.useConnected, { wrapper: h.Wrapper });
    await act(() => result.current.workflowSync.onPush());
    const element = feedback.success.mock.calls.at(-1)![0];
    render(element, { wrapper: h.Wrapper });
    expect(screen.getByRole("link", { name: t("viewPr") })).toHaveAttribute(
      "href",
      "https://source.invalid/pull/123"
    );
    h.mode("unknown");
    await act(() => result.current.workflowSync.onRefresh());
    expect(feedback.success).toHaveBeenCalledWith(
      t("feedback.refreshed", { state: "future_state_v2" })
    );
    expect(h.errors).not.toHaveBeenCalled();
    unmount();
  });
  it("keeps pull confirmation open for a refusal and retries the same target before showing verbatim YAML", async () => {
    const h = harness(locale, "refused");
    const t = translator(locale);
    function Connected() {
      const data = h.useConnected();
      return <WorkflowSyncStatusControl status={status} {...data.workflowSync} />;
    }
    render(<Connected />, { wrapper: h.Wrapper });
    await userEvent.click(screen.getByRole("button", { name: t("pull") }));
    await userEvent.click(screen.getAllByRole("button", { name: t("pull") }).at(-1)!);
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL"));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    h.mode("success");
    await userEvent.click(screen.getAllByRole("button", { name: t("pull") }).at(-1)!);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.getByText("RAW_REPO_YAML")).toBeInTheDocument();
    expect(screen.getByText("RAW_RENDERED_YAML")).toBeInTheDocument();
    const writes = h.requests.filter((r) => r.operationName === "PullCiWorkflowFromRepo");
    expect(writes).toHaveLength(2);
    for (const write of writes) expect(write.variables).toEqual({ input: { appId } });
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("shows known states with localized copy, opaque future states literally and no write controls without grants", () => {
    const h = harness(locale, "success", false);
    const t = translator(locale);
    const { rerender } = render(
      <WorkflowSyncStatusControl
        status={{ ...status, state: "__proto__" }}
        {...CI_SETUP_PROBLEMS.workflowSync}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText("__proto__")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: t("push") })).not.toBeInTheDocument();
    rerender(<WorkflowSyncStatusControl status={status} {...CI_SETUP_PROBLEMS.workflowSync} />);
    expect(screen.getByText(t("states.conflict.label"))).toBeInTheDocument();
    expect(screen.getByText(t("states.conflict.hint"))).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("has complete ICU keys and preserves relative-time boundaries and legacy English callers", () => {
    function flatten(value: Record<string, unknown>, prefix = ""): Record<string, string> {
      return Object.fromEntries(
        Object.entries(value).flatMap(([key, v]) =>
          typeof v === "string"
            ? [[prefix + key, v]]
            : Object.entries(flatten(v as Record<string, unknown>, prefix + key + "."))
        )
      );
    }
    const source = flatten(catalogs.en.apps.overview.ciWorkflow);
    const localized = flatten(catalogs[locale].apps.overview.ciWorkflow);
    expect(Object.keys(localized)).toEqual(Object.keys(source));
    for (const [key, message] of Object.entries(localized)) {
      expect(parseIcu(message)).toBeDefined();
      const names = (s: string) => [...s.matchAll(/\{(\w+)[,}]/g)].map((m) => m[1]).sort();
      expect(names(message)).toEqual(names(source[key]));
      if (locale !== "en") expect(message).not.toBe(source[key]);
    }
    const now = Date.now();
    const t = translator(locale);
    for (const [key, ms, count] of [
      ["minutes", 60000, 1],
      ["hours", 3600000, 1],
      ["days", 86400000, 1],
      ["months", 2592000000, 1],
      ["years", 31536000000, 1],
    ] as const) {
      expect(
        formatRelativeWebhookInstall(new Date(now - ms).toISOString(), (key, values) =>
          t(`relative.${key}`, values)
        )
      ).toBe(t(`relative.${key}`, { count }));
    }
    expect(
      formatRelativeWebhookInstall("INVALID_TIMESTAMP", (key, values) =>
        t(`relative.${key}`, values)
      )
    ).toBe(t("relative.justNow"));
    expect(formatRelativeWebhookInstall(new Date(now - 60000).toISOString())).toBe("1m ago");
  });
});
