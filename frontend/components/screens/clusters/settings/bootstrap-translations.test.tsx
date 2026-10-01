import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
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
import { useState, type ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { BootstrapPlanView } from "./BootstrapPlan";
import { BootstrapHistoryView } from "./ClusterSettings";
import { PLAN, HISTORY } from "./fixtures";
import { useBootstrapPlan, useBootstrapHistory } from "./use-bootstrap";
import { CLUSTER_BOOTSTRAP_PLAN } from "@/graphql/clusters/clusters.queries";

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
type Mode =
  | "accepted"
  | "refused"
  | "fallback"
  | "transport"
  | "empty"
  | "no-components"
  | "unknown"
  | "read-failed"
  | "wrong-target"
  | "read-deferred"
  | "deferred";
function harness(locale: string, initial: Mode = "accepted", granted = ["cluster.manage"]) {
  let mode = initial;
  const waiting: (() => void)[] = [];
  let setPermissions: (value: string[]) => void = () => undefined;
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/gql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push({ operationName: request.operationName, variables: request.variables });
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        const read = request.operationName !== "InstallClusterPrereqs";
        if ((read && mode === "read-failed") || (!read && mode === "transport"))
          throw new Error("RAW_TRANSPORT_DIAGNOSTIC");
        if ((!read && mode === "deferred") || (read && mode === "read-deferred"))
          await new Promise<void>((resolve) => {
            waiting.push(resolve);
          });
        const id = String(request.variables.clusterId || "LITERAL_CLUSTER");
        const rootValue = read
          ? request.operationName === "ClusterBootstrapPlan"
            ? {
                astroliftClusterBootstrapPlan:
                  mode === "empty"
                    ? null
                    : mode === "unknown"
                      ? undefined
                      : {
                          ...PLAN.plan,
                          clusterId: mode === "wrong-target" ? "FOREIGN_CLUSTER" : id,
                          components: mode === "no-components" ? [] : PLAN.plan!.components,
                        },
              }
            : {
                astroliftCluster:
                  mode === "empty"
                    ? null
                    : mode === "unknown"
                      ? undefined
                      : {
                          id,
                          slug: mode === "wrong-target" ? "foreign-slug" : request.variables.slug,
                          bootstrapRuns: mode === "no-components" ? [] : HISTORY.runs,
                        },
              }
          : {
              installClusterPrereqs: {
                ok: !["refused", "fallback"].includes(mode),
                errors:
                  mode === "refused"
                    ? [
                        {
                          code: "PERMISSION_DENIED",
                          message: "RAW_SERVER_REFUSAL",
                          field: "clusterId",
                        },
                      ]
                    : [],
                data: {
                  id: request.variables.input.clusterId,
                  slug: "literal-slug",
                  lifecycle: "managing",
                  lastManagementError: "",
                },
              },
            };
        const result =
          mode === "unknown" && read
            ? { data: {} }
            : await execute({
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
    const [permissions, update] = useState(granted);
    setPermissions = update;
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-10-01T17:00:00Z")}
        timeZone="America/Costa_Rica"
      >
        <ApolloProvider client={client}>
          <PermissionsProvider value={{ granted: new Set(permissions), loading: false }}>
            <TooltipProvider>{children}</TooltipProvider>
          </PermissionsProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    client,
    Wrapper,
    requests,
    mode: (next: Mode) => {
      mode = next;
    },
    release: () => {
      expect(waiting.length).toBeGreaterThan(0);
      waiting.shift()!();
    },
    permissions: (value: string[]) => setPermissions(value),
  };
}
const selected = { cert_manager: true, aws_lb_controller: true };
const options = { cert_manager: { issuer: "letsencrypt-staging" } };
function translator(locale: string, namespace = "clusterSettings.bootstrapPlan") {
  return createTranslator({ locale, messages: catalogs[locale], namespace });
}
beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe.each(locales)("bootstrap source and feedback in %s", (locale) => {
  it("renders translated controls while keeping recipe fields and option values literal", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const install = vi.fn();
    render(<BootstrapPlanView {...PLAN} onInstall={install} />, { wrapper: h.Wrapper });
    expect(screen.getByText(t("title"))).toBeInTheDocument();
    expect(screen.getByText(PLAN.plan!.components[0].rationale)).toBeInTheDocument();
    expect(screen.getByText(t("installed"))).toBeInTheDocument();
    expect(screen.getByText(t("outsideRecipe"))).toBeInTheDocument();
    const dropdown = screen.getByRole("combobox", { name: "Default issuer" });
    await userEvent.selectOptions(dropdown, "letsencrypt-staging");
    await userEvent.click(screen.getByRole("button", { name: t("install") }));
    expect(install).toHaveBeenCalledWith(
      expect.objectContaining({ cert_manager: true, aws_lb_controller: false }),
      expect.objectContaining({ cert_manager: { issuer: "letsencrypt-staging" } })
    );
  });

  it.each(["accepted", "refused", "fallback", "transport"] as const)(
    "uses the actual mutation input and truthful %s feedback",
    async (mode) => {
      const h = harness(locale, mode);
      const hook = renderHook(() => useBootstrapPlan("LITERAL_CLUSTER"), { wrapper: h.Wrapper });
      await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
      await act(() => hook.result.current.onInstall(selected, options));
      expect(h.requests.filter((r) => r.operationName === "InstallClusterPrereqs")).toEqual([
        {
          operationName: "InstallClusterPrereqs",
          variables: {
            input: {
              clusterId: "LITERAL_CLUSTER",
              selectedComponents: ["cert_manager", "aws_lb_controller"],
              optionOverrides: [
                { componentKey: "cert_manager", optionKey: "issuer", value: "letsencrypt-staging" },
              ],
            },
          },
        },
      ]);
      expect(h.requests.filter((r) => r.operationName === "ClusterBootstrapPlan")).toHaveLength(1);
      if (mode === "accepted")
        expect(feedback.success).toHaveBeenCalledWith(
          translator(locale)("requested", { count: 2, clusterId: "LITERAL_CLUSTER" })
        );
      else
        expect(feedback.error).toHaveBeenCalledWith(
          mode === "fallback"
            ? translator(locale)("failed")
            : mode === "refused"
              ? "RAW_SERVER_REFUSAL"
              : "RAW_TRANSPORT_DIAGNOSTIC"
        );
      expect(hook.result.current.installing).toBe(false);
    }
  );

  it.each(["unknown", "read-failed", "wrong-target", "empty"] as const)(
    "keeps %s observation separate from an empty recipe and supports real retry",
    async (mode) => {
      const h = harness(locale, mode);
      function Card() {
        return <BootstrapPlanView {...useBootstrapPlan("LITERAL_CLUSTER")} />;
      }
      render(<Card />, { wrapper: h.Wrapper });
      await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
      expect(screen.queryByText("astro cluster bootstrap")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: translator(locale)("install") })
      ).not.toBeInTheDocument();
      h.mode("accepted");
      await userEvent.click(screen.getByRole("button", { name: translator(locale)("retry") }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: translator(locale)("install") })).toBeEnabled()
      );
    }
  );

  it("renders a returned empty component list and the exact CLI command", async () => {
    const h = harness(locale, "no-components");
    function Card() {
      return <BootstrapPlanView {...useBootstrapPlan("LITERAL_CLUSTER")} />;
    }
    render(<Card />, { wrapper: h.Wrapper });
    await waitFor(() => expect(screen.getByText("astro cluster bootstrap")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a confirmed empty history subset without claiming missing source", async () => {
    const h = harness(locale, "no-components");
    const hook = renderHook(() => useBootstrapHistory("literal-slug"), { wrapper: h.Wrapper });
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    expect(hook.result.current.error).toBeNull();
    expect(hook.result.current.runs).toEqual([]);
  });

  it("retains edited choices through a failed cached read and stays read-only until retry completes", async () => {
    const h = harness(locale);
    let current!: ReturnType<typeof useBootstrapPlan>;
    function Card() {
      current = useBootstrapPlan("LITERAL_CLUSTER");
      return <BootstrapPlanView {...current} />;
    }
    render(<Card />, { wrapper: h.Wrapper });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: translator(locale)("install") })).toBeEnabled()
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Default issuer" }),
      "letsencrypt-staging"
    );
    const prior = current.onInstall;
    h.mode("read-failed");
    await act(async () => {
      await h.client.refetchQueries({ include: ["ClusterBootstrapPlan"] }).catch(() => undefined);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("RAW_TRANSPORT_DIAGNOSTIC");
    await act(() => prior(selected, options));
    h.mode("read-deferred");
    await userEvent.click(screen.getByRole("button", { name: translator(locale)("retry") }));
    expect(current.readOnly).toBe(true);
    await act(() => current.onInstall(selected, options));
    expect(h.requests.filter((r) => r.operationName === "InstallClusterPrereqs")).toHaveLength(0);
    h.mode("accepted");
    await act(() => h.release());
    await waitFor(() =>
      expect(screen.getByRole("button", { name: translator(locale)("install") })).toBeEnabled()
    );
    expect(screen.getByRole("combobox", { name: "Default issuer" })).toHaveValue(
      "letsencrypt-staging"
    );
  });

  it("refuses stale source/permission ABA callbacks and current invalid option values", async () => {
    const h = harness(locale);
    const hook = renderHook(({ id }) => useBootstrapPlan(id), {
      initialProps: { id: "LITERAL_CLUSTER" },
      wrapper: h.Wrapper,
    });
    await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
    const old = hook.result.current.onInstall;
    hook.rerender({ id: "OTHER_CLUSTER" });
    await waitFor(() => expect(hook.result.current.plan?.clusterId).toBe("OTHER_CLUSTER"));
    hook.rerender({ id: "LITERAL_CLUSTER" });
    await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
    await act(() => old(selected, options));
    const permitted = hook.result.current.onInstall;
    act(() => h.permissions([]));
    act(() => h.permissions(["cluster.manage"]));
    await act(() => permitted(selected, options));
    await act(() =>
      hook.result.current.onInstall(selected, { cert_manager: { issuer: "UNLISTED_VALUE" } })
    );
    expect(h.requests.filter((r) => r.operationName === "InstallClusterPrereqs")).toHaveLength(0);
    expect(feedback.error).toHaveBeenLastCalledWith(translator(locale)("invalidOption"));
  });

  it("preserves unknown history statuses and localizes current known statuses", () => {
    const h = harness(locale);
    const t = translator(locale, "clusterSettings.bootstrapHistory");
    render(
      <BootstrapHistoryView
        {...HISTORY}
        runs={[
          ...HISTORY.runs,
          {
            ...HISTORY.runs[0],
            id: "unknown-run",
            status: "RAW_UNKNOWN_STATUS",
            chartVersion: "LITERAL_CHART",
          },
        ]}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText("RAW_UNKNOWN_STATUS")).toBeInTheDocument();
    expect(screen.getByText("LITERAL_CHART")).toBeInTheDocument();
    expect(screen.getByText(t("succeeded"))).toBeInTheDocument();
    expect(screen.getByText(t("failed"))).toBeInTheDocument();
  });

  it("rejects a callback from a replaced recipe even after the observed recipe returns", async () => {
    const h = harness(locale);
    const hook = renderHook(() => useBootstrapPlan("LITERAL_CLUSTER"), { wrapper: h.Wrapper });
    await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
    const prior = hook.result.current.onInstall;
    const source = hook.result.current.plan!;
    const write = (provider: string) =>
      h.client.writeQuery({
        query: CLUSTER_BOOTSTRAP_PLAN,
        variables: { clusterId: "LITERAL_CLUSTER" },
        data: { astroliftClusterBootstrapPlan: { ...source, providerPluginSlug: provider } },
      });
    act(() => write("REPLACED_PROVIDER"));
    await waitFor(() =>
      expect(hook.result.current.plan?.providerPluginSlug).toBe("REPLACED_PROVIDER")
    );
    act(() => write(source.providerPluginSlug));
    await waitFor(() =>
      expect(hook.result.current.plan?.providerPluginSlug).toBe(source.providerPluginSlug)
    );
    await act(() => prior(selected, options));
    expect(h.requests.filter((r) => r.operationName === "InstallClusterPrereqs")).toHaveLength(0);
    expect(feedback.error).toHaveBeenCalledWith(translator(locale)("sourceChanged"));
  });

  it("hydrates the translated server render without changing technical recipe values", async () => {
    const h = harness(locale);
    const content = (
      <h.Wrapper>
        <BootstrapPlanView {...PLAN} />
      </h.Wrapper>
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(content);
    document.body.append(container);
    const errors: unknown[] = [];
    let root!: ReturnType<typeof hydrateRoot>;
    await act(() => {
      root = hydrateRoot(container, content, { onRecoverableError: (error) => errors.push(error) });
    });
    expect(errors).toEqual([]);
    expect(container.textContent).toContain(PLAN.plan!.components[0].rationale);
    expect(container.textContent).toContain(translator(locale)("title"));
    await act(() => root.unmount());
    container.remove();
  });

  it.each(["empty", "unknown", "wrong-target", "read-failed"] as const)(
    "does not turn %s history reads into an empty success",
    async (mode) => {
      const h = harness(locale, mode);
      const hook = renderHook(() => useBootstrapHistory("literal-slug"), { wrapper: h.Wrapper });
      await waitFor(() => expect(hook.result.current.loading).toBe(false));
      expect(hook.result.current.error).toBeTruthy();
      expect(hook.result.current.runs).toEqual([]);
      h.mode("accepted");
      act(() => hook.result.current.onRetry());
      await waitFor(() => expect(hook.result.current.error).toBeNull());
      expect(hook.result.current.runs).toHaveLength(2);
      expect(h.requests[0].variables).toEqual({ slug: "literal-slug", limit: 10 });
    }
  );

  it("keeps keys and ICU arguments compatible with English", () => {
    for (const namespace of ["bootstrapPlan", "bootstrapHistory"]) {
      const current = catalogs[locale].clusterSettings[namespace];
      const english = catalogs.en.clusterSettings[namespace];
      expect(Object.keys(current)).toEqual(Object.keys(english));
      for (const [key, text] of Object.entries(current)) {
        function argumentsOf(value: string): string[] {
          const argumentsSeen = new Set<string>();
          const visit = (nodes: ReturnType<typeof parseIcu>) => {
            for (const node of nodes) {
              if ("value" in node && node.type !== 0) argumentsSeen.add(String(node.value));
              if ("options" in node)
                for (const option of Object.values(node.options)) visit(option.value);
              if ("children" in node) visit(node.children);
            }
          };
          visit(parseIcu(value));
          return [...argumentsSeen].sort();
        }
        expect(argumentsOf(text as string), key).toEqual(argumentsOf(english[key]));
      }
    }
  });
});

it("keeps pending requests scoped so an old completion cannot clear another target", async () => {
  const h = harness("en", "deferred");
  const hook = renderHook(({ id }) => useBootstrapPlan(id), {
    initialProps: { id: "FIRST_CLUSTER" },
    wrapper: h.Wrapper,
  });
  await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
  let first!: Promise<void>;
  act(() => {
    first = hook.result.current.onInstall(selected, options);
  });
  await waitFor(() => expect(hook.result.current.installing).toBe(true));
  hook.rerender({ id: "SECOND_CLUSTER" });
  await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
  expect(hook.result.current.installing).toBe(false);
  let second!: Promise<void>;
  act(() => {
    second = hook.result.current.onInstall(selected, options);
  });
  await waitFor(() =>
    expect(h.requests.filter((r) => r.operationName === "InstallClusterPrereqs")).toHaveLength(2)
  );
  await act(() => hook.result.current.onInstall(selected, options));
  expect(h.requests.filter((r) => r.operationName === "InstallClusterPrereqs")).toHaveLength(2);
  await act(async () => {
    h.release();
    await first;
  });
  expect(hook.result.current.installing).toBe(true);
  expect(hook.result.current.plan?.clusterId).toBe("SECOND_CLUSTER");
  await act(async () => {
    h.release();
    await second;
  });
  expect(hook.result.current.installing).toBe(false);
});
