import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider, useQuery } from "@apollo/client/react";
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
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GET_APP } from "@/graphql/registry/registry.queries";
import {
  CI_SETUP_PROBLEMS,
  CI_SETUP_EU,
  CI_SETUP_AGENT,
} from "./app-ci-observability-section.fixtures";
import { renderWorkflowYaml, resolveProviderCiMeta } from "./ci-setup-meta";
import { useCiSetup } from "./use-ci-setup";
import { CiSetupSectionView, PushAndRotateButtonView } from "./CiSetupSection";
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
type Mode =
  | "success"
  | "refused"
  | "fallback"
  | "transport"
  | "unknown"
  | "pr"
  | "partial"
  | "in_sync"
  | "updated"
  | "refreshed";
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
          "pushAstroliftCiSecretsToRepo",
          "validateAstroliftCiSecrets",
          "pushAstroliftCiWorkflowToRepo",
          "installAstroliftSourceWebhook",
        ])
          rootValue[name] = {
            ok: !["refused", "fallback"].includes(mode),
            errors:
              mode === "refused"
                ? [{ code: "PERMISSION_DENIED", message: "RAW_SERVER_REFUSAL" }]
                : [],
            data: {
              status:
                mode === "unknown"
                  ? "future_status_v2"
                  : mode === "pr"
                    ? "pr_opened"
                    : mode === "refreshed"
                      ? "refreshed"
                      : mode === "updated"
                        ? "updated"
                        : mode === "in_sync"
                          ? "in_sync"
                          : "created",
              commitSha: "abcdef0123456789",
              receiverUrl: "https://receiver.invalid/literal",
              hookId: "LITERAL_HOOK",
              secretNames: ["LITERAL_SECRET_A", "LITERAL_SECRET_B"],
              rotatedTokenLast4: "abcd",
              repo: "literal/repo",
              results: [
                {
                  secretName: "LITERAL_SECRET_A",
                  isSet: true,
                  isCurrent: true,
                  updatedAt: "2026-09-28T23:00:00Z",
                },
                {
                  secretName: "LITERAL_SECRET_B",
                  isSet: true,
                  isCurrent: mode !== "partial",
                  updatedAt: "2026-09-28T23:00:00Z",
                },
              ],
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
        timeZone="Asia/Tokyo"
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
  function useConnected(agentMode = false) {
    useQuery(GET_APP, {
      variables: { slug: "literal-app", includeDrift: true },
      fetchPolicy: "network-only",
    });
    return useCiSetup({ appId, appSlug: "literal-app", agentMode });
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
  createTranslator({ locale, messages: catalogs[locale], namespace: "apps.overview.ciSetup" });
beforeEach(() => vi.clearAllMocks());
describe.each(locales)("complete CI setup controls in %s", (locale) => {
  it.each(["push", "validate", "sync", "webhook"] as const)(
    "localizes %s outcomes over real SDL/HTTP and keeps literal app targets",
    async (action) => {
      const h = harness(locale);
      const t = translator(locale);
      const { result, unmount } = renderHook(() => h.useConnected(), { wrapper: h.Wrapper });
      const invoke = () =>
        action === "push"
          ? result.current.pushAndRotate.onPushAndRotate()
          : action === "validate"
            ? result.current.validate.onValidate()
            : action === "sync"
              ? result.current.syncFile.onSync()
              : result.current.webhook.onInstall();
      await act(invoke);
      const expected = {
        push: t("feedback.pushed", { count: 2, repo: "literal/repo", last4: "abcd" }),
        validate: t("feedback.validated", { count: 2, repo: "literal/repo" }),
        sync: t("feedback.syncedCommit", {
          verb: t("feedback.created"),
          filename: "astrolift-ci.yml",
          commit: "abcdef0",
        }),
        webhook: t("feedback.webhookUrl", {
          verb: t("feedback.installed"),
          url: "https://receiver.invalid/literal",
        }),
      }[action];
      expect(feedback.success).toHaveBeenCalledWith(expected);
      const writes = h.requests.filter(
        (r) => !["GetApp", "GetPlatformApiUrl"].includes(r.operationName)
      );
      expect(writes).toHaveLength(1);
      expect(writes[0].variables).toEqual({ input: { appSlug: "literal-app" } });
      const failed = {
        push: "pushFailed",
        validate: "validateFailed",
        sync: "syncFailed",
        webhook: "webhookFailed",
      }[action];
      for (const mode of ["fallback", "refused", "transport"] as const) {
        h.mode(mode);
        const message =
          mode === "fallback"
            ? t(`feedback.${failed}`)
            : mode === "refused"
              ? "RAW_SERVER_REFUSAL"
              : "RAW_TRANSPORT_DIAGNOSTIC";
        await act(async () => {
          if (action === "push") await expect(invoke()).rejects.toThrow(message);
          else await invoke();
        });
        if (action !== "push") expect(feedback.error).toHaveBeenCalledWith(message);
      }
      if (action === "validate") expect(result.current.validate.results).toBeNull();
      expect(h.errors).not.toHaveBeenCalled();
      unmount();
    }
  );
  it("preserves protected-branch PR links, idempotent state and opaque future sync/webhook statuses", async () => {
    const h = harness(locale, "pr");
    const t = translator(locale);
    const { result, unmount } = renderHook(() => h.useConnected(), { wrapper: h.Wrapper });
    await act(() => result.current.syncFile.onSync());
    render(feedback.success.mock.calls.at(-1)![0], { wrapper: h.Wrapper });
    expect(screen.getByRole("link", { name: t("feedback.viewPr") })).toHaveAttribute(
      "href",
      "https://source.invalid/pull/123"
    );
    h.mode("in_sync");
    await act(() => result.current.syncFile.onSync());
    expect(feedback.success).toHaveBeenCalledWith(t("feedback.inSync"));
    h.mode("unknown");
    await act(() => result.current.syncFile.onSync());
    expect(feedback.success).toHaveBeenCalledWith(
      t("feedback.syncedCommit", {
        verb: "future_status_v2",
        filename: "astrolift-ci.yml",
        commit: "abcdef0",
      })
    );
    await act(() => result.current.webhook.onInstall());
    expect(feedback.success).toHaveBeenCalledWith(
      t("feedback.webhookUrl", {
        verb: "future_status_v2",
        url: "https://receiver.invalid/literal",
      })
    );
    expect(h.errors).not.toHaveBeenCalled();
    unmount();
  });
  it("localizes validation labels/dates in the configured locale and retains secret names and partial counts", async () => {
    const h = harness(locale, "partial");
    const t = translator(locale);
    function Connected() {
      const data = h.useConnected();
      return <CiSetupSectionView {...CI_SETUP_PROBLEMS} {...data} />;
    }
    render(<Connected />, { wrapper: h.Wrapper });
    await userEvent.click(
      within(screen.getByText(t("validateTitle")).parentElement!.parentElement!).getByRole(
        "button",
        { name: t("validate") }
      )
    );
    expect(await screen.findByText("LITERAL_SECRET_A")).toBeInTheDocument();
    expect(screen.getByText("LITERAL_SECRET_B")).toBeInTheDocument();
    expect(screen.getByText(t("current"))).toBeInTheDocument();
    expect(screen.getByText(t("stale"))).toBeInTheDocument();
    const date = new Intl.DateTimeFormat(locale, {
      timeZone: "Asia/Tokyo",
      year: "numeric",
      month: "short",
      day: "numeric",
    }).format(new Date("2026-09-28T23:00:00Z"));
    expect(screen.getAllByText(date)).toHaveLength(2);
    expect(feedback.warning).toHaveBeenCalledWith(
      t("feedback.partiallyValidated", { healthy: 1, total: 2, repo: "literal/repo" })
    );
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("shows localized missing-secret and malformed-date states without exposing an Invalid Date", () => {
    const h = harness(locale);
    const t = translator(locale);
    render(
      <CiSetupSectionView
        {...CI_SETUP_PROBLEMS}
        validate={{
          ...CI_SETUP_PROBLEMS.validate,
          results: {
            ...CI_SETUP_PROBLEMS.validate.results!,
            results: [
              {
                secretName: "LITERAL_SECRET_NAME",
                isSet: false,
                isCurrent: false,
                updatedAt: "not-a-date",
              },
            ],
          },
        }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText("LITERAL_SECRET_NAME")).toBeInTheDocument();
    expect(screen.getByText(t("notSet"))).toBeInTheDocument();
    expect(screen.getByText(t("unknownDate"))).toBeInTheDocument();
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
  });
  it("keeps token rotation confirmation open on refusal and retries unchanged app targets", async () => {
    const h = harness(locale, "refused");
    const t = translator(locale);
    function Connected() {
      const data = h.useConnected();
      return <PushAndRotateButtonView {...data.pushAndRotate} />;
    }
    render(<Connected />, { wrapper: h.Wrapper });
    await userEvent.click(screen.getByRole("button", { name: t("pushRotate") }));
    expect(screen.getByText(t("rotateDescription"))).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: t("pushRotate") }).at(-1)!);
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL"));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    h.mode("success");
    await userEvent.click(screen.getAllByRole("button", { name: t("pushRotate") }).at(-1)!);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    const writes = h.requests.filter((r) => r.operationName === "PushAstroliftCiSecretsToRepo");
    expect(writes).toHaveLength(2);
    for (const write of writes)
      expect(write.variables).toEqual({ input: { appSlug: "literal-app" } });
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("copies credential refs and backend YAML verbatim, retaining the token destination and provider-specific technical names", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<CiSetupSectionView {...CI_SETUP_EU} />, { wrapper: h.Wrapper });
    expect(screen.getByRole("link", { name: t("manageTokens") })).toHaveAttribute(
      "href",
      CI_SETUP_EU.tokensHref
    );
    await userEvent.click(screen.getAllByRole("button", { name: t("copy") })[0]);
    expect(writeText).toHaveBeenCalledWith(CI_SETUP_EU.pushCredentialRef);
    expect(feedback.success).toHaveBeenCalledWith(
      t("valueCopied", { name: "ASTROLIFT_PUSH_ROLE_ARN" })
    );
    await userEvent.click(screen.getByText(t("managedWorkflow")));
    await userEvent.click(screen.getByRole("button", { name: t("copyYaml") }));
    expect(writeText).toHaveBeenCalledWith(CI_SETUP_EU.ciWorkflowSyncStatus!.renderedText);
    for (const provider of ["gcp", "azure", "k8s_native"]) {
      const yaml = renderWorkflowYaml(resolveProviderCiMeta(provider), {
        providerSlug: provider,
        deployBranch: "LITERAL_BRANCH",
      });
      expect(yaml).toContain('branches: ["LITERAL_BRANCH"]');
    }
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("keeps read-only CI review available without writes and localizes clipboard failures", async () => {
    const h = harness(locale, "success", false);
    const t = translator(locale);
    const writeText = vi.fn().mockRejectedValue(new Error("RAW_CLIPBOARD_DETAIL"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    function Connected() {
      const data = h.useConnected();
      return <CiSetupSectionView {...CI_SETUP_EU} {...data} />;
    }
    render(<Connected />, { wrapper: h.Wrapper });
    for (const label of ["pushRotate", "validate", "installWebhook", "refreshWebhook"])
      expect(screen.queryByRole("button", { name: t(label) })).not.toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: t("copy") })[0]);
    expect(feedback.error).toHaveBeenCalledWith(t("copyValueFailed"));
    await userEvent.click(screen.getByText(t("managedWorkflow")));
    await userEvent.click(screen.getByRole("button", { name: t("copyYaml") }));
    expect(feedback.error).toHaveBeenCalledWith(t("copyTextFailed"));
    expect(h.requests.some((r) => /Push|Install|Validate/.test(r.operationName))).toBe(false);
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("localizes agent-package semantics and keeps the package workflow path exact", async () => {
    const h = harness(locale);
    const t = translator(locale);
    function Connected() {
      const data = h.useConnected(true);
      return <CiSetupSectionView {...CI_SETUP_AGENT} {...data} />;
    }
    render(<Connected />, { wrapper: h.Wrapper });
    expect(screen.getByText(t("agentTitle"))).toBeInTheDocument();
    expect(screen.getByText(t("agentDescription"))).toBeInTheDocument();
    expect(screen.getByText(t("agentRunsHint"))).toBeInTheDocument();
    expect(
      screen.getByText(".github/workflows/astrolift-agent-literal-app.yml")
    ).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("has complete ICU/rich contracts and preserves technical path and count placeholders", () => {
    function flatten(value: Record<string, unknown>, prefix = ""): Record<string, string> {
      return Object.fromEntries(
        Object.entries(value).flatMap(([key, v]) =>
          typeof v === "string"
            ? [[prefix + key, v]]
            : Object.entries(flatten(v as Record<string, unknown>, prefix + key + "."))
        )
      );
    }
    const source = flatten(catalogs.en.apps.overview.ciSetup);
    const localized = flatten(catalogs[locale].apps.overview.ciSetup);
    expect(Object.keys(localized)).toEqual(Object.keys(source));
    for (const [key, message] of Object.entries(localized)) {
      expect(parseIcu(message)).toBeDefined();
      const names = (s: string) => [...s.matchAll(/\{(\w+)[,}]/g)].map((m) => m[1]).sort();
      expect(names(message)).toEqual(names(source[key]));
      if (locale !== "en") expect(message).not.toBe(source[key]);
    }
    expect(localized.pasteInto).toContain(".github/workflows/astrolift-ci.yml");
    const t = translator(locale);
    for (const count of [0, 1, 2, 5]) {
      expect(
        t("feedback.pushed", { count, repo: "LITERAL_REPO", last4: "LITERAL_SUFFIX" })
      ).toContain("LITERAL_REPO");
      expect(t("feedback.validated", { count, repo: "LITERAL_REPO" })).toContain("LITERAL_REPO");
    }
  });
});
