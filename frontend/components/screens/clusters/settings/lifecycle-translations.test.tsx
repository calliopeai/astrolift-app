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
import { useState, type ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ClusterSettingsScreen } from "./ClusterSettings";
import { BOOTSTRAP_RUN, CLUSTER, SETTINGS } from "./fixtures";
import { useClusterSettings } from "./use-cluster-settings";

const feedback = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: feedback }));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const resolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
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
type ReadMode = "ok" | "failed" | "missing" | "unknown" | "wrong-slug" | "wrong-id" | "deferred";
type WriteMode = "accepted" | "refused" | "fallback" | "transport" | "deferred";
const SECOND_ID = "c0ffee00-0000-4000-8000-000000000002";
function harness(
  locale: string,
  initialRead: ReadMode = "ok",
  granted = ["cluster.manage", "cluster.unregister"]
) {
  let readMode = initialRead;
  let writeMode: WriteMode = "accepted";
  let setPermissions: (value: string[]) => void = () => undefined;
  const waiting: (() => void)[] = [];
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
        const read = request.operationName === "GetCluster";
        const mode = read ? readMode : writeMode;
        if (mode === "failed") throw new Error("RAW_READ_DIAGNOSTIC");
        if (mode === "transport") throw new Error("RAW_MUTATION_DIAGNOSTIC");
        if (mode === "deferred") await new Promise<void>((resolve) => waiting.push(resolve));
        const slug = String(request.variables.slug || CLUSTER.slug);
        const cluster = {
          ...CLUSTER,
          id: slug === "second-cluster" ? SECOND_ID : CLUSTER.id,
          slug,
          isActive: true,
        };
        const field = {
          BringClusterIntoManagement: "bringClusterIntoManagement",
          RefreshClusterManagement: "refreshClusterManagement",
          DecommissionCluster: "decommissionCluster",
        }[request.operationName as string];
        const rootValue = read
          ? {
              astroliftCluster:
                mode === "missing"
                  ? null
                  : {
                      ...cluster,
                      ...(mode === "wrong-slug" ? { slug: "FOREIGN_SLUG" } : {}),
                      ...(mode === "wrong-id" ? { id: SECOND_ID } : {}),
                    },
            }
          : {
              [field!]: {
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
                data: { ...cluster, id: request.variables.input.clusterId },
              },
            };
        const result =
          read && mode === "unknown"
            ? { data: {} }
            : await execute({
                schema,
                document,
                variableValues: request.variables,
                rootValue,
                fieldResolver: resolver,
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
    read: (mode: ReadMode) => {
      readMode = mode;
    },
    write: (mode: WriteMode) => {
      writeMode = mode;
    },
    permissions: (value: string[]) => setPermissions(value),
    release: () => {
      expect(waiting.length).toBeGreaterThan(0);
      waiting.shift()!();
    },
  };
}
type Hook = ReturnType<typeof useClusterSettings>;
const cases = [
  {
    name: "bring",
    operation: "BringClusterIntoManagement",
    input: {},
    key: "manageRequested",
    call: (h: Hook) => h.onBring(),
  },
  {
    name: "refresh",
    operation: "RefreshClusterManagement",
    input: { forcePreflight: false },
    key: "refreshRequested",
    call: (h: Hook) => h.onRefresh(false),
  },
  {
    name: "full preflight",
    operation: "RefreshClusterManagement",
    input: { forcePreflight: true },
    key: "fullRequested",
    call: (h: Hook) => h.onRefresh(true),
  },
  {
    name: "retire",
    operation: "DecommissionCluster",
    input: { deleteCloudInfra: false },
    key: "retireRequested",
    call: (h: Hook) => h.onDecommission(false),
  },
  {
    name: "retire and teardown",
    operation: "DecommissionCluster",
    input: { deleteCloudInfra: true },
    key: "deleteRequested",
    call: (h: Hook) => h.onDecommission(true),
  },
] as const;
function translator(locale: string) {
  return createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "clusterSettings.lifecycle",
  });
}
async function invoke(test: (typeof cases)[number], hook: Hook) {
  let error: unknown;
  await act(async () => {
    try {
      await test.call(hook);
    } catch (e) {
      error = e;
    }
  });
  return error;
}
beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe.each(locales)("cluster lifecycle in %s", (locale) => {
  it.each(cases)(
    "submits the exact $name intent and reports accepted target identity",
    async (test) => {
      const h = harness(locale);
      const t = translator(locale);
      const hook = renderHook(() => useClusterSettings(CLUSTER.slug), { wrapper: h.Wrapper });
      await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
      expect(await invoke(test, hook.result.current)).toBeUndefined();
      expect(h.requests).toEqual([
        { operationName: "GetCluster", variables: { slug: CLUSTER.slug } },
        {
          operationName: test.operation,
          variables: { input: { clusterId: CLUSTER.id, ...test.input } },
        },
        { operationName: "GetCluster", variables: { slug: CLUSTER.slug } },
      ]);
      expect(feedback.success).toHaveBeenCalledWith(
        t(test.key, { slug: CLUSTER.slug }) + " " + t("target", { clusterId: CLUSTER.id })
      );
      expect(feedback.warning).not.toHaveBeenCalled();
      expect(feedback.error).not.toHaveBeenCalled();
    }
  );

  it.each(cases)("keeps accepted $name distinct from a failed follow-up read", async (test) => {
    const h = harness(locale);
    const t = translator(locale);
    const hook = renderHook(() => useClusterSettings(CLUSTER.slug), { wrapper: h.Wrapper });
    await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
    h.read("failed");
    expect(await invoke(test, hook.result.current)).toBeUndefined();
    expect(feedback.success).toHaveBeenCalledTimes(1);
    expect(feedback.warning).toHaveBeenCalledWith(
      t("acceptedRefreshFailed", { slug: CLUSTER.slug }) +
        " " +
        t("target", { clusterId: CLUSTER.id })
    );
    expect(feedback.error).not.toHaveBeenCalled();
    await waitFor(() => expect(hook.result.current.readOnly).toBe(true));
    expect(hook.result.current.cluster?.id).toBe(CLUSTER.id);
    h.read("ok");
    await act(async () => {
      await hook.result.current.onRetry();
    });
    await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
  });

  it.each(["missing", "unknown", "wrong-slug"] as const)(
    "keeps an accepted operation distinct from a %s follow-up source",
    async (mode) => {
      const h = harness(locale);
      const hook = renderHook(() => useClusterSettings(CLUSTER.slug), { wrapper: h.Wrapper });
      await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
      h.read(mode);
      expect(await invoke(cases[1], hook.result.current)).toBeUndefined();
      expect(feedback.success).toHaveBeenCalledTimes(1);
      expect(feedback.warning).toHaveBeenCalledTimes(1);
      expect(feedback.error).not.toHaveBeenCalled();
      await waitFor(() => expect(hook.result.current.readOnly).toBe(true));
      const before = h.requests.length;
      await invoke(cases[1], hook.result.current);
      expect(h.requests).toHaveLength(before);
      h.read("ok");
      await act(async () => {
        await hook.result.current.onRetry();
      });
      await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
    }
  );

  for (const mode of ["refused", "fallback", "transport"] as const) {
    it.each(cases)(`preserves ${mode} $name feedback without a follow-up read`, async (test) => {
      const h = harness(locale);
      const t = translator(locale);
      const hook = renderHook(() => useClusterSettings(CLUSTER.slug), { wrapper: h.Wrapper });
      await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
      h.write(mode);
      const error = await invoke(test, hook.result.current);
      const message =
        mode === "refused"
          ? "RAW_SERVER_REFUSAL"
          : mode === "transport"
            ? "RAW_MUTATION_DIAGNOSTIC"
            : t("failed");
      if (test.operation === "DecommissionCluster") expect(error).toEqual(new Error(message));
      else {
        expect(error).toBeUndefined();
        expect(feedback.error).toHaveBeenCalledWith(message);
      }
      expect(h.requests.filter((r) => r.operationName === "GetCluster")).toHaveLength(1);
      expect(feedback.success).not.toHaveBeenCalled();
      expect(feedback.warning).not.toHaveBeenCalled();
    });
  }

  it.each(["missing", "unknown", "wrong-slug", "failed"] as const)(
    "keeps a %s source read-only and offers actual retry",
    async (mode) => {
      const h = harness(locale, mode);
      const hook = renderHook(() => useClusterSettings(CLUSTER.slug), { wrapper: h.Wrapper });
      await waitFor(() => expect(hook.result.current.loading).toBe(false));
      expect(hook.result.current.readOnly).toBe(true);
      const before = h.requests.length;
      await invoke(cases[0], hook.result.current);
      await invoke(cases[1], hook.result.current);
      await invoke(cases[3], hook.result.current);
      expect(h.requests).toHaveLength(before);
      h.read("ok");
      await act(async () => {
        await hook.result.current.onRetry();
      });
      await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
      expect(h.requests.filter((r) => r.operationName === "GetCluster")).toHaveLength(2);
    }
  );

  it("invalidates callbacks after observed cluster GUID ABA without retargeting", async () => {
    const h = harness(locale);
    const hook = renderHook(() => useClusterSettings(CLUSTER.slug), { wrapper: h.Wrapper });
    await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
    const old = hook.result.current;
    h.read("wrong-id");
    await act(async () => {
      await hook.result.current.onRetry();
    });
    await waitFor(() => expect(hook.result.current.cluster?.id).toBe(SECOND_ID));
    h.read("ok");
    await act(async () => {
      await hook.result.current.onRetry();
    });
    await waitFor(() => expect(hook.result.current.cluster?.id).toBe(CLUSTER.id));
    const before = h.requests.length;
    for (const test of cases) await invoke(test, old);
    expect(h.requests).toHaveLength(before);
    expect(feedback.success).not.toHaveBeenCalled();
  });

  it("invalidates callbacks after observed permission withdrawal and restoration", async () => {
    const h = harness(locale);
    const hook = renderHook(() => useClusterSettings(CLUSTER.slug), { wrapper: h.Wrapper });
    await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
    const old = hook.result.current;
    act(() => h.permissions([]));
    await waitFor(() => expect(hook.result.current.access.manage).toBe(false));
    act(() => h.permissions(["cluster.manage", "cluster.unregister"]));
    await waitFor(() => expect(hook.result.current.access.manage).toBe(true));
    const before = h.requests.length;
    for (const test of cases) await invoke(test, old);
    expect(h.requests).toHaveLength(before);
    expect(feedback.success).not.toHaveBeenCalled();
  });

  it("checks the operation-specific permission before any write", async () => {
    const h = harness(locale, "ok", ["cluster.manage"]);
    const hook = renderHook(() => useClusterSettings(CLUSTER.slug), { wrapper: h.Wrapper });
    await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
    expect(await invoke(cases[3], hook.result.current)).toEqual(
      new Error(translator(locale)("sourceChanged"))
    );
    expect(h.requests.filter((r) => r.operationName !== "GetCluster")).toHaveLength(0);
    await invoke(cases[1], hook.result.current);
    expect(h.requests.filter((r) => r.operationName === "RefreshClusterManagement")).toHaveLength(
      1
    );
  });

  it("keeps requests on separate observed targets independent and refuses duplicates", async () => {
    const h = harness(locale);
    const hook = renderHook(({ slug }) => useClusterSettings(slug), {
      initialProps: { slug: CLUSTER.slug },
      wrapper: h.Wrapper,
    });
    await waitFor(() => expect(hook.result.current.readOnly).toBe(false));
    h.write("deferred");
    let first!: Promise<void>;
    act(() => {
      first = hook.result.current.onRefresh(false);
    });
    await waitFor(() => expect(hook.result.current.refreshing).toBe(true));
    await invoke(cases[1], hook.result.current);
    expect(h.requests.filter((r) => r.operationName === "RefreshClusterManagement")).toHaveLength(
      1
    );
    hook.rerender({ slug: "second-cluster" });
    await waitFor(() => {
      expect(hook.result.current.cluster?.id).toBe(SECOND_ID);
      expect(hook.result.current.readOnly).toBe(false);
    });
    let second!: Promise<void>;
    act(() => {
      second = hook.result.current.onRefresh(true);
    });
    await waitFor(() => expect(hook.result.current.refreshing).toBe(true));
    await act(async () => {
      h.release();
      await first;
    });
    expect(hook.result.current.refreshing).toBe(true);
    expect(feedback.success).toHaveBeenCalledWith(
      translator(locale)("refreshRequested", { slug: CLUSTER.slug }) +
        " " +
        translator(locale)("target", { clusterId: CLUSTER.id })
    );
    expect(h.requests.filter((r) => r.operationName === "GetCluster")).toHaveLength(2);
    await act(async () => {
      h.release();
      await second;
    });
    await waitFor(() => expect(hook.result.current.refreshing).toBe(false));
    expect(
      h.requests
        .filter((r) => r.operationName === "RefreshClusterManagement")
        .map((r) => r.variables)
    ).toEqual([
      { input: { clusterId: CLUSTER.id, forcePreflight: false } },
      { input: { clusterId: SECOND_ID, forcePreflight: true } },
    ]);
  });

  it("renders translated lifecycle controls and retains literal source diagnostics", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const onRefresh = vi.fn();
    render(<ClusterSettingsScreen {...SETTINGS} onRefresh={onRefresh} />, { wrapper: h.Wrapper });
    await userEvent.click(screen.getByRole("button", { name: t("refresh") }));
    expect(onRefresh).toHaveBeenCalledWith(false);
    await userEvent.click(screen.getByRole("button", { name: t("more") }));
    await userEvent.click(screen.getByRole("menuitem", { name: t("fullRefresh") }));
    expect(onRefresh).toHaveBeenCalledWith(true);
  });

  it("closes a decommission review after its observed source changes", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const confirm = vi.fn();
    const { rerender } = render(<ClusterSettingsScreen {...SETTINGS} onDecommission={confirm} />, {
      wrapper: h.Wrapper,
    });
    await userEvent.click(screen.getByRole("button", { name: t("retire") }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    rerender(
      <ClusterSettingsScreen
        {...SETTINGS}
        cluster={{ ...CLUSTER, id: SECOND_ID }}
        onDecommission={confirm}
      />
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("distinguishes malformed capabilities from known installed and absent reports", () => {
    const h = harness(locale);
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "clusterSettings.presentation",
    });
    const historyT = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "clusterSettings.bootstrapHistory",
    });
    const { container } = render(
      <ClusterSettingsScreen
        {...SETTINGS}
        cluster={{
          ...CLUSTER,
          capabilities: {
            cert_manager: { installed: "false", version: {}, default_issuer: 42 },
            ingress: { installed: false, class: "LITERAL_CLASS" },
            external_dns: { installed: true, provider: "LITERAL_PROVIDER" },
            storage_classes: [null, "gp3"],
            metrics_server: "false",
            prometheus: false,
          },
        }}
      />,
      { wrapper: h.Wrapper }
    );
    function status(label: string) {
      return within(screen.getByText(label, { selector: "dt" }).parentElement!);
    }
    expect(status("cert-manager").getByText(historyT("unknown"))).toBeInTheDocument();
    expect(status(t("ingressController")).getByText(t("notDetected"))).toBeInTheDocument();
    expect(
      status(t("ingressController")).getByText(t("classDetail", { value: "LITERAL_CLASS" }))
    ).toBeInTheDocument();
    expect(status("external-dns").getByText(t("installed"))).toBeInTheDocument();
    expect(status("external-dns").getByText("LITERAL_PROVIDER")).toBeInTheDocument();
    expect(status(t("storageClasses")).getByText(historyT("unknown"))).toBeInTheDocument();
    expect(status("metrics-server").getByText(historyT("unknown"))).toBeInTheDocument();
    expect(status("Prometheus").getByText(t("notDetected"))).toBeInTheDocument();
    expect(container.textContent).not.toContain("[object Object]");
    expect(screen.getByText(t("missingTitle"))).toBeInTheDocument();
    expect(container.querySelector('a[href="/downloads"]')).toHaveAccessibleName();
    expect(
      container.querySelector('a[href="/documentation/cluster-prerequisites"]')
    ).toHaveAccessibleName();
  });

  it("preserves literal unknown bootstrap status and malformed release row counts", async () => {
    const h = harness(locale);
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "clusterSettings.presentation",
    });
    const historyT = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "clusterSettings.bootstrapHistory",
    });
    const cluster = {
      ...CLUSTER,
      lastBootstrapRun: {
        ...CLUSTER.lastBootstrapRun!,
        status: "LITERAL_FUTURE_STATUS",
        errorMessage: "RAW_BOOTSTRAP_DIAGNOSTIC",
        installedReleases: [
          null,
          { name: 42, version: {}, status: false },
          { name: "LITERAL_RELEASE", version: "LITERAL_VERSION", status: "LITERAL_STATUS" },
        ] as unknown as NonNullable<typeof CLUSTER.lastBootstrapRun>["installedReleases"],
      },
    };
    const { container } = render(<ClusterSettingsScreen {...SETTINGS} cluster={cluster} />, {
      wrapper: h.Wrapper,
    });
    expect(screen.getByText("LITERAL_FUTURE_STATUS")).toHaveAttribute("data-variant", "outline");
    expect(screen.queryByText(historyT("failed"))).not.toBeInTheDocument();
    expect(screen.getByText("RAW_BOOTSTRAP_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.getByText(t("releasesCount", { count: 3 }))).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: t("viewReleases") }));
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getAllByText(historyT("unknown"))).toHaveLength(6);
    for (const literal of ["LITERAL_RELEASE", "LITERAL_VERSION", "LITERAL_STATUS"])
      expect(within(table).getByText(literal)).toBeInTheDocument();
    expect(container.textContent).not.toContain("[object Object]");
    expect(screen.getByPlaceholderText(t("searchReleases"))).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: t("hideReleases") }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("hydrates translated stored reports and literal identifiers without recovery errors", async () => {
    const h = harness(locale);
    const container = document.createElement("div");
    const content = (
      <h.Wrapper>
        <ClusterSettingsScreen {...SETTINGS} />
      </h.Wrapper>
    );
    container.innerHTML = renderToString(content);
    document.body.append(container);
    const errors: unknown[] = [];
    let root!: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, content, { onRecoverableError: (error) => errors.push(error) });
    });
    expect(errors).toEqual([]);
    expect(container.textContent).toContain(CLUSTER.endpoint);
    expect(container.textContent).toContain(BOOTSTRAP_RUN.chartVersion);
    await act(async () => root.unmount());
    container.remove();
  });

  it.each(["decommissioning", "decommissioned", "LITERAL_FUTURE_STATE"])(
    "disables destructive controls and offers no management action in %s",
    (lifecycle) => {
      const h = harness(locale);
      const t = translator(locale);
      render(
        <ClusterSettingsScreen
          {...SETTINGS}
          lifecycle={lifecycle}
          cluster={{ ...CLUSTER, lifecycle }}
        />,
        { wrapper: h.Wrapper }
      );
      for (const key of ["manage", "retryManage", "refresh", "forceRetrigger"] as const)
        expect(screen.queryByRole("button", { name: t(key) })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: t("retire") })).toBeDisabled();
      expect(screen.getByRole("button", { name: t("deleteAction") })).toBeDisabled();
    }
  );

  it("disables all lifecycle controls on an unconfirmed cached source", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const onRefresh = vi.fn();
    const onDecommission = vi.fn();
    render(
      <ClusterSettingsScreen
        {...SETTINGS}
        readOnly
        onRefresh={onRefresh}
        onDecommission={onDecommission}
      />,
      { wrapper: h.Wrapper }
    );
    for (const key of ["refresh", "more", "retire", "deleteAction"] as const)
      expect(screen.getByRole("button", { name: t(key) })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: t("refresh") }));
    await userEvent.click(screen.getByRole("button", { name: t("retire") }));
    expect(onRefresh).not.toHaveBeenCalled();
    expect(onDecommission).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("closes a destructive review when observed permissions are withdrawn", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const confirm = vi.fn();
    const { rerender } = render(<ClusterSettingsScreen {...SETTINGS} onDecommission={confirm} />, {
      wrapper: h.Wrapper,
    });
    await userEvent.click(screen.getByRole("button", { name: t("deleteAction") }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    rerender(
      <ClusterSettingsScreen
        {...SETTINGS}
        access={{ ...SETTINGS.access, unregister: false }}
        onDecommission={confirm}
      />
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("keeps an unknown release payload distinct from a known empty report", () => {
    const h = harness(locale);
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "clusterSettings.presentation",
    });
    const historyT = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "clusterSettings.bootstrapHistory",
    });
    const { rerender } = render(
      <ClusterSettingsScreen
        {...SETTINGS}
        cluster={{
          ...CLUSTER,
          lastBootstrapRun: { ...CLUSTER.lastBootstrapRun!, installedReleases: {} },
        }}
      />,
      { wrapper: h.Wrapper }
    );
    const field = () => screen.getByText(historyT("releases"), { selector: "dt" }).parentElement!;
    expect(within(field()).getByText(historyT("unknown"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: t("viewReleases") })).not.toBeInTheDocument();
    rerender(
      <ClusterSettingsScreen
        {...SETTINGS}
        cluster={{
          ...CLUSTER,
          lastBootstrapRun: {
            ...CLUSTER.lastBootstrapRun!,
            installedReleases: [] as unknown as NonNullable<
              typeof CLUSTER.lastBootstrapRun
            >["installedReleases"],
          },
        }}
      />
    );
    expect(within(field()).getByText(t("releasesCount", { count: 0 }))).toBeInTheDocument();
    expect(within(field()).queryByText(historyT("unknown"))).not.toBeInTheDocument();
  });
});

it("keeps lifecycle catalogue keys, arguments and rich tags identical in all eight locales", () => {
  function argumentsOf(message: string): string[] {
    const values = new Set<string>();
    function walk(nodes: ReturnType<typeof parseIcu>) {
      for (const node of nodes) {
        if ("value" in node && node.type !== 0) values.add(node.value);
        if ("children" in node) walk(node.children);
        if ("options" in node) for (const option of Object.values(node.options)) walk(option.value);
      }
    }
    walk(parseIcu(message));
    return [...values].sort();
  }
  for (const [namespace, count] of [
    ["lifecycle", 27],
    ["presentation", 37],
  ] as const) {
    const base = catalogs.en.clusterSettings[namespace];
    expect(Object.keys(base)).toHaveLength(count);
    for (const locale of locales) {
      const entries = catalogs[locale].clusterSettings[namespace];
      expect(Object.keys(entries).sort()).toEqual(Object.keys(base).sort());
      for (const key of Object.keys(base))
        expect(argumentsOf(entries[key])).toEqual(argumentsOf(base[key]));
    }
  }
});
