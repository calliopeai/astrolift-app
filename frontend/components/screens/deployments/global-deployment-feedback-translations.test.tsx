import { readFileSync } from "node:fs";
import { ApolloClient, ApolloLink, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
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
import { Observable } from "rxjs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DEPLOY_RUNNING } from "@/components/screens/apps/deployments/app-deployments-logs.fixtures";
import { START } from "./deployments.fixtures";
import { StartDeploymentPage } from "./StartDeploymentPage";
import { useDeployments } from "./use-deployments";
import { useDeploymentDetail } from "./use-deployment-detail";
import { useStartDeployment } from "./use-start-deployment";
const feedback = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: feedback }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: feedback.push, replace: feedback.replace }),
  usePathname: () => "/deployments",
  useSearchParams: () => new URLSearchParams(),
}));
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
type Mode = "success" | "refused" | "fallback" | "transport" | "unknown" | "absent" | "null";
function harness(locale: string, initial: Mode = "success") {
  let mode = initial;
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const intlErrors = vi.fn();
  const http = new HttpLink({
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
        astroliftDeployment: DEPLOY_RUNNING,
        astroliftDeploymentsPage: { items: [], nextCursor: null, totalCount: 0 },
        astroliftApps: START.apps,
        astroliftEnvironments: START.environments,
      };
      for (const field of [
        "approveDeployment",
        "abortDeployment",
        "rollbackDeployment",
        "redeployApp",
        "startDeployment",
      ])
        rootValue[field] = {
          ok: !["refused", "fallback"].includes(mode),
          errors:
            mode === "refused"
              ? [{ code: "PERMISSION_DENIED", message: "RAW_SERVER_REFUSAL", field: "image_tag" }]
              : [],
          data: { ...DEPLOY_RUNNING, status: mode === "unknown" ? "future_status_v2" : "running" },
        };
      const result = await execute({
        schema,
        document,
        variableValues: request.variables,
        rootValue,
        fieldResolver,
      });
      expect("errors" in result ? result.errors : undefined).toBeUndefined();
      const response =
        mutation && mode === "absent"
          ? { data: {} }
          : mutation && mode === "null"
            ? { data: Object.fromEntries(Object.keys(result.data ?? {}).map((key) => [key, null])) }
            : result;
      return new Response(JSON.stringify(response), {
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  const link = ApolloLink.split(
    (op) =>
      op.query.definitions.some(
        (d) => d.kind === "OperationDefinition" && d.operation === "subscription"
      ),
    new ApolloLink(() => new Observable(() => undefined)),
    http
  );
  const client = new ApolloClient({ cache: new InMemoryCache(), link });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={intlErrors}
      >
        <ApolloProvider client={client}>
          <PermissionsProvider
            value={{
              granted: new Set(["app.deploy", "app.approve_deploy", "app.rollback"]),
              loading: false,
            }}
          >
            <TooltipProvider>{children}</TooltipProvider>
          </PermissionsProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    Wrapper,
    requests,
    intlErrors,
    mode: (value: Mode) => {
      mode = value;
    },
  };
}
const translator = (locale: string, namespace: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace });
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});
describe.each(locales)("global deployment feedback in %s", (locale) => {
  it.each(["approve", "abort", "rollback", "redeploy"] as const)(
    "localizes %s action through real HTTP and keeps exact target/reason",
    async (action) => {
      const h = harness(locale);
      const t = translator(locale, "apps.deployments.actions");
      const s = translator(locale, "apps.deployments.statuses");
      const { result, unmount } = renderHook(() => useDeployments(), { wrapper: h.Wrapper });
      await act(() => result.current.runAction(action, DEPLOY_RUNNING, "LITERAL_USER_REASON"));
      expect(feedback.success).toHaveBeenCalledWith(
        t("success", {
          action: t(action === "rollback" ? "rollbackConfirm" : action),
          status: s("running"),
        })
      );
      const request = h.requests.find((r) =>
        ["ApproveDeployment", "AbortDeployment", "RollbackDeployment", "RedeployApp"].includes(
          r.operationName
        )
      )!;
      expect(request.variables).toEqual({
        input: {
          id: DEPLOY_RUNNING.id,
          ...(action === "abort" ? { reason: "LITERAL_USER_REASON" } : {}),
        },
      });
      h.mode("refused");
      await act(async () => {
        await expect(
          result.current.runAction(action, DEPLOY_RUNNING, "LITERAL_USER_REASON")
        ).rejects.toThrow("RAW_SERVER_REFUSAL");
      });
      expect(h.intlErrors).not.toHaveBeenCalled();
      unmount();
    }
  );
  it.each(["approve", "abort", "rollback", "redeploy"] as const)(
    "refuses %s without an acknowledgement and permits a retry",
    async (action) => {
      const h = harness(locale, "absent");
      const t = translator(locale, "apps.deployments.actions");
      const translatedAction = action === "rollback" ? "rollbackConfirm" : action;
      const { result, unmount } = renderHook(() => useDeployments(), { wrapper: h.Wrapper });
      for (const mode of ["absent", "null"] as const) {
        h.mode(mode);
        await act(async () => {
          await expect(
            result.current.runAction(action, DEPLOY_RUNNING, "LITERAL_USER_REASON")
          ).rejects.toThrow(t("failed", { action: t(translatedAction) }));
        });
        expect(feedback.success).not.toHaveBeenCalled();
      }
      h.mode("success");
      await act(() => result.current.runAction(action, DEPLOY_RUNNING, "LITERAL_USER_REASON"));
      expect(feedback.success).toHaveBeenCalledTimes(1);
      expect(h.intlErrors).not.toHaveBeenCalled();
      unmount();
    }
  );
  it("keeps a detail mutation refusal literal, localizes fallback and preserves future statuses", async () => {
    const h = harness(locale, "unknown");
    const t = translator(locale, "apps.deployments.actions");
    const { result, unmount } = renderHook(() => useDeploymentDetail(DEPLOY_RUNNING.id), {
      wrapper: h.Wrapper,
    });
    await waitFor(() => expect(result.current.deployment?.id).toBe(DEPLOY_RUNNING.id));
    await act(() => result.current.onApprove());
    expect(feedback.success).toHaveBeenCalledWith(
      t("success", { action: t("approve"), status: "future_status_v2" })
    );
    h.mode("fallback");
    await act(() => result.current.onApprove());
    expect(feedback.error).toHaveBeenCalledWith(t("failed", { action: t("approve") }));
    h.mode("refused");
    await act(() => result.current.onApprove());
    expect(feedback.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL");
    h.mode("transport");
    await act(() => result.current.onApprove());
    expect(feedback.error).toHaveBeenCalledWith("RAW_TRANSPORT_DIAGNOSTIC");
    expect(h.intlErrors).not.toHaveBeenCalled();
    unmount();
  });
  it("localizes successful start while preserving submission values, redirect identity and field diagnostics", async () => {
    const h = harness(locale, "unknown");
    const t = translator(locale, "lists.deployments.startSheet");
    const { result, unmount } = renderHook(() => useStartDeployment(), { wrapper: h.Wrapper });
    await act(async () => result.current.setAppSlug("literal-app"));
    const input = {
      environmentName: "LITERAL_ENV",
      imageTag: "sha-literal",
      imageDigest: "  sha256:literal  ",
      triggerKind: "manual" as const,
    };
    await act(async () => {
      expect(await result.current.onSubmit(input)).toEqual({ ok: true });
    });
    expect(h.requests.find((r) => r.operationName === "StartDeployment")?.variables).toEqual({
      input: { ...input, imageDigest: "sha256:literal", appSlug: "literal-app" },
    });
    expect(feedback.success).toHaveBeenCalledWith(t("started", { status: "future_status_v2" }));
    expect(feedback.push).toHaveBeenCalledWith(`/deployments/${DEPLOY_RUNNING.id}`);
    h.mode("absent");
    await act(async () => {
      expect(await result.current.onSubmit(input)).toEqual({
        ok: false,
        fieldErrors: {},
        formError: t("startFailed"),
      });
    });
    h.mode("refused");
    await act(async () => {
      expect(await result.current.onSubmit(input)).toEqual({
        ok: false,
        fieldErrors: { imageTag: "RAW_SERVER_REFUSAL" },
        formError: null,
      });
    });
    expect(h.intlErrors).not.toHaveBeenCalled();
    unmount();
  });
  it("localizes wizard validation, review and plural approval warnings without changing technical data", async () => {
    const h = harness(locale);
    const t = translator(locale, "lists.deployments.startSheet");
    const { rerender } = render(<StartDeploymentPage {...START} appSlug="" />, {
      wrapper: h.Wrapper,
    });
    await userEvent.click(screen.getByRole("button", { name: t("continue") }));
    expect(screen.getByText(t("validationApp"))).toBeInTheDocument();
    expect(screen.getByText(t("validationEnv"))).toBeInTheDocument();
    rerender(
      <StartDeploymentPage
        {...START}
        key="review"
        initialStep={3}
        initialValues={{ environmentName: START.environments[0].name, imageTag: "sha-literal" }}
        environments={[{ ...START.environments[0], requiredApprovals: 2, deploysPaused: true }]}
      />
    );
    expect(screen.getByText(t("reviewDescription"))).toBeInTheDocument();
    expect(
      screen.getByText(t("approvalWarning", { env: START.environments[0].name, count: 2 }))
    ).toBeInTheDocument();
    expect(
      screen.getByText(t("pausedWarning", { env: START.environments[0].name }))
    ).toBeInTheDocument();
    expect(screen.getByText("sha-literal")).toBeInTheDocument();
    expect(h.intlErrors).not.toHaveBeenCalled();
  });
  it("parses new ICU copy and preserves every locale’s argument contract", () => {
    const source = catalogs.en.lists.deployments.startSheet;
    const localized = catalogs[locale].lists.deployments.startSheet;
    function flatten(value: Record<string, unknown>, prefix = ""): Record<string, string> {
      return Object.fromEntries(
        Object.entries(value).flatMap(([key, v]) =>
          typeof v === "string"
            ? [[prefix + key, v]]
            : Object.entries(flatten(v as Record<string, unknown>, prefix + key + "."))
        )
      );
    }
    const sourceMessages = flatten(source);
    const localeMessages = flatten(localized);
    expect(Object.keys(localeMessages)).toEqual(Object.keys(sourceMessages));
    for (const [key, message] of Object.entries(localeMessages)) {
      expect(message.trim()).not.toBe("");
      expect(parseIcu(message)).toBeDefined();
      const names = (s: string) => [...s.matchAll(/\{(\w+)[,}]/g)].map((m) => m[1]).sort();
      expect(names(message)).toEqual(names(sourceMessages[key]));
    }
    if (locale !== "en")
      for (const key of ["title", "description", "validationApp", "started", "approvalWarning"])
        expect(localized[key]).not.toBe(source[key]);
    for (const count of [0, 1, 2, 5])
      expect(
        translator(locale, "lists.deployments.startSheet")("approvalWarning", {
          env: "LITERAL_ENV",
          count,
        })
      ).toContain("LITERAL_ENV");
  });
});
