import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import {
  parse as parseMessage,
  type MessageFormatElement,
} from "@formatjs/icu-messageformat-parser";
import { act, render, renderHook, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildSchema, parse, validate } from "graphql";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";

import { useLocalListState } from "@/components/list/use-list-state";
import { effectiveFilters, parseListState } from "@/components/list/list-state";
import { TooltipProvider } from "@/components/ui/tooltip";
import { locales } from "@/i18n/config";
import { ClusterDetail } from "./ClusterDetail";
import { ClusterLiveStats } from "./ClusterLiveStats";
import { ClustersList, type ClustersListProps } from "./ClustersList";
import { CLUSTERS_LIST, clustersPageVariables, localizedClustersList } from "./clusters-list";
import { cluster as fixtureCluster, detailProps, listProps } from "./fixtures";
import { useClusterActions } from "./use-cluster-actions";
import { useClustersList } from "./use-clusters-list";

const state = vi.hoisted(() => ({ allowed: true }));
const feedback = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: feedback }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/clusters",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    loading: false,
    granted: new Set(
      state.allowed ? ["cluster.manage", "cluster.register", "cluster.unregister"] : []
    ),
    can: (permission: string) => state.allowed && permission.startsWith("cluster."),
  }),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const tFor = (locale: string, namespace: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace });
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const cluster = {
  ...fixtureCluster("SLUG_LITERAL", {
    id: "c0ffee00-0000-4000-8000-000000000001",
    name: "NAME_LITERAL",
    region: "REGION_LITERAL",
    ingressClass: "INGRESS_LITERAL",
    createdByUsername: "ACTOR_LITERAL",
    authMethod: "exec_plugin",
  }),
  __typename: "AstroliftTenantCluster",
  organizationSlug: "ORG_LITERAL",
  albAuthConfig: null,
  oidcAuthConfig: null,
  lastBootstrapRun: null,
};
function tree(locale: string, children: React.ReactNode) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="UTC"
      now={new Date("2026-10-01T12:00:00Z")}
    >
      <TooltipProvider delayDuration={0}>{children}</TooltipProvider>
    </NextIntlClientProvider>
  );
}
function List(patch: Partial<Omit<ClustersListProps, "list">>) {
  const t = useTranslations("clusters.list"),
    lifecycle = useTranslations("clusters.chrome");
  const list = useLocalListState(localizedClustersList(t, lifecycle));
  return <ClustersList {...listProps({ rows: [cluster], totalCount: 1, ...patch })} list={list} />;
}
function ConnectedList() {
  return <ClustersList {...useClustersList()} />;
}
type Mode = "ok" | "no-message" | "refused";
function context(locale: string) {
  let mode: Mode = "ok";
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://cluster-actions.test.invalid/graphql/",
      fetch: async (_url, options) => {
        const request = JSON.parse(String(options?.body));
        expect(validate(schema, parse(request.query))).toEqual([]);
        requests.push(request);
        let data;
        switch (request.operationName) {
          case "ListClustersPage":
            data = {
              astroliftClustersPage: {
                __typename: "AstroliftTenantClusterPage",
                items: [cluster],
                totalCount: 1,
                page: 1,
                pageSize: 25,
                hasNext: false,
                endCursor: null,
                nextCursor: null,
              },
            };
            break;
          case "ListClusters":
            data = { astroliftClusters: [cluster] };
            break;
          case "BringClusterIntoManagement":
          case "RefreshClusterManagement": {
            const field =
              request.operationName === "BringClusterIntoManagement"
                ? "bringClusterIntoManagement"
                : "refreshClusterManagement";
            data = {
              [field]: {
                ok: mode === "ok",
                data: mode === "ok" ? cluster : null,
                errors:
                  mode === "refused"
                    ? [{ code: "PRECONDITION", message: "SERVER_DIAGNOSTIC_LITERAL" }]
                    : [],
              },
            };
            break;
          }
          default:
            throw new Error("Unexpected operation " + request.operationName);
        }
        return Response.json({ data });
      },
    }),
  });
  function Wrapper({ children }: { children: React.ReactNode }) {
    return tree(locale, <ApolloProvider client={client}>{children}</ApolloProvider>);
  }
  return {
    Wrapper,
    client,
    requests,
    setMode: (next: Mode) => {
      mode = next;
    },
  };
}
function leaves(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, child]) =>
      typeof child === "string"
        ? [[prefix + key, child]]
        : Object.entries(leaves(child as Record<string, unknown>, prefix + key + "."))
    )
  );
}
function argumentsOf(elements: MessageFormatElement[]): string[] {
  return elements
    .flatMap((element) => {
      if (element.type === 0) return [];
      if (element.type === 7) return ["#"];
      if (element.type === 8) return ["tag:" + element.value, ...argumentsOf(element.children)];
      if (element.type === 5 || element.type === 6)
        return [
          "argument:" + element.value,
          ...Object.values(element.options).flatMap((option) => argumentsOf(option.value)),
        ];
      return ["argument:" + element.value];
    })
    .filter((value, index, all) => all.indexOf(value) === index)
    .sort();
}
beforeEach(() => {
  state.allowed = true;
  localStorage.clear();
  vi.clearAllMocks();
});

describe.each(locales)("cluster row and detail actions in %s", (locale) => {
  const list = tFor(locale, "clusters.list"),
    actions = tFor(locale, "clusters.actions"),
    detail = tFor(locale, "clusters.detail");
  const chrome = tFor(locale, "clusters.chrome"),
    shared = tFor(locale, "shared.list");
  it("has complete ICU arguments and rich-link tags without translating query values", () => {
    for (const namespace of ["list", "actions", "detail"]) {
      const english = leaves(catalogs.en.clusters[namespace]),
        localized = leaves(catalogs[locale].clusters[namespace]);
      expect(Object.keys(localized).sort()).toEqual(Object.keys(english).sort());
      for (const [key, message] of Object.entries(localized))
        expect(argumentsOf(parseMessage(message))).toEqual(argumentsOf(parseMessage(english[key])));
    }
    const definition = localizedClustersList(list, chrome);
    expect(definition.fields[0].options).toEqual(CLUSTERS_LIST.fields[0].options);
    expect(definition.fields[1].options?.map((option) => option.value)).toEqual([
      "registered",
      "managing",
      "managed",
      "error",
    ]);
    const query =
      "q=OPERATOR_LITERAL&view=mine&provider=eks&status=managed&sort=provider,-name&page=2&pageSize=50";
    const state = parseListState(definition, query);
    expect(
      clustersPageVariables({ ...state, filters: effectiveFilters(definition, state) })
    ).toEqual({
      search: "OPERATOR_LITERAL",
      filter: { provider: ["eks"], status: ["managed"], registeredBy: ["me"] },
      sort: "provider,-name",
      page: 2,
      pageSize: 50,
    });
  });
  it("renders the connected numbered list with translated labels and unchanged API state", async () => {
    const ctx = context(locale);
    render(<ConnectedList />, { wrapper: ctx.Wrapper });
    await screen.findByText(cluster.name);
    expect(screen.getByPlaceholderText(list("searchPlaceholder"))).toBeTruthy();
    for (const key of [
      "cluster",
      "status",
      "provider",
      "region",
      "ingress",
      "live",
      "lastProbe",
      "registeredBy",
    ])
      expect(
        screen.getByRole("columnheader", { name: new RegExp(list("columns." + key)) })
      ).toBeTruthy();
    for (const key of ["all", "mine", "offline"])
      expect(screen.getByRole("link", { name: list("views." + key) })).toBeTruthy();
    expect(screen.getByRole("link", { name: list("register") })).toHaveAttribute(
      "href",
      "/clusters/new"
    );
    expect(document.body).toHaveTextContent(chrome("lifecycle.managed"));
    expect(document.body).toHaveTextContent(tFor(locale, "clusterConnection")("connected"));
    for (const value of [
      cluster.slug,
      cluster.region,
      cluster.ingressClass,
      cluster.createdByUsername!,
    ])
      expect(document.body).toHaveTextContent(value);
    expect(
      ctx.requests.map(({ operationName, variables }) => ({ operationName, variables }))
    ).toEqual([
      {
        operationName: "ListClustersPage",
        variables: { search: null, filter: null, sort: "name", page: 1, pageSize: 25 },
      },
    ]);
  });
  it.each([false, true])(
    "dispatches the translated refresh menu with forcePreflight=%s on the observed cluster",
    async (full) => {
      const ctx = context(locale);
      render(<ConnectedList />, { wrapper: ctx.Wrapper });
      await screen.findByText(cluster.name);
      await userEvent.click(
        screen.getByRole("button", { name: shared("rowActions", { label: chrome("clusters") }) })
      );
      await userEvent.click(
        await screen.findByRole("menuitem", {
          name: actions(full ? "fullPreflight" : "refresh"),
        })
      );
      expect(feedback.success).toHaveBeenCalledWith(
        actions(full ? "fullPreflightAccepted" : "refreshAccepted", { slug: cluster.slug })
      );
      expect(
        ctx.requests
          .filter((r) => r.operationName === "RefreshClusterManagement")
          .map((r) => r.variables)
      ).toEqual([{ input: { clusterId: cluster.id, forcePreflight: full } }]);
    }
  );
  it("keeps empty copy and permission denial separate from missing lifecycle diagnostics", () => {
    state.allowed = false;
    const result = render(tree(locale, <List rows={[]} totalCount={0} />));
    expect(screen.getByText(list("emptyTitle"))).toBeTruthy();
    expect(screen.getByText(list("emptyDescription"))).toBeTruthy();
    expect(screen.queryByRole("link", { name: list("register") })).toBeNull();
    result.rerender(
      tree(
        locale,
        <List
          rows={[
            {
              ...cluster,
              lifecycle: "FUTURE_LIFECYCLE",
              heartbeatStatus: "FUTURE_HEARTBEAT",
              capabilitiesProbedAt: null,
            },
          ]}
        />
      )
    );
    expect(document.body).toHaveTextContent("FUTURE_LIFECYCLE");
    expect(document.body).toHaveTextContent("FUTURE_HEARTBEAT");
    expect(document.body).toHaveTextContent(list("never"));
    expect(screen.queryByRole("button", { name: actions("refresh") })).toBeNull();
  });
  it("translates detail capabilities while preserving technical products, auth and raw errors", async () => {
    const target = {
      ...cluster,
      lifecycle: "error",
      lastManagementError: "SERVER_DIAGNOSTIC_LITERAL",
    };
    render(
      tree(
        locale,
        <ClusterDetail
          {...detailProps({ cluster: target, slug: target.slug, renderLiveStats: () => null })}
        />
      )
    );
    for (const key of [
      "failureTitle",
      "failureHelp",
      "management",
      "capabilitiesTitle",
      "capabilitiesDescription",
      "storageClasses",
      "metricsServer",
      "auth",
      "actionRequired",
    ])
      expect(document.body).toHaveTextContent(detail(key));
    for (const value of [
      "SERVER_DIAGNOSTIC_LITERAL",
      "AWS EKS",
      "exec_plugin",
      "cert-manager",
      "external-dns",
      "Prometheus",
      cluster.region,
    ])
      expect(document.body).toHaveTextContent(value);
    expect(document.body).toHaveTextContent(detail("installed"));
    expect(document.body).toHaveTextContent(detail("notDetected"));
    expect(screen.getByRole("button", { name: actions("retry") })).toBeTruthy();
    const help = screen.getByText(detail("actionRequired")).parentElement!;
    expect(within(help).getByRole("link")).toHaveAttribute(
      "href",
      `/clusters/${cluster.slug}/settings`
    );
    await userEvent.click(screen.getByRole("button", { name: detail("menuLabel") }));
    expect(screen.getByRole("menuitem", { name: chrome("tabs.settings") })).toHaveAttribute(
      "href",
      `/clusters/${cluster.slug}/settings`
    );
  });
  it.each(["registered", "managing", "managed", "error"])(
    "retains the %s detail action, disabled state and exact dispatch target",
    async (lifecycle) => {
      const bring = vi.fn(async () => {}),
        refresh = vi.fn(async () => {});
      const target = { ...cluster, lifecycle };
      render(
        tree(
          locale,
          <ClusterDetail
            {...detailProps({
              cluster: target,
              slug: target.slug,
              onBring: bring,
              onRefresh: refresh,
              renderLiveStats: () => null,
            })}
          />
        )
      );
      expect(document.body).toHaveTextContent(chrome("lifecycle." + lifecycle));
      if (lifecycle === "managing") {
        expect(screen.getByRole("button", { name: actions("inProgress") })).toBeDisabled();
        expect(bring).not.toHaveBeenCalled();
        expect(refresh).not.toHaveBeenCalled();
      } else {
        await userEvent.click(
          screen.getByRole("button", {
            name: actions(
              lifecycle === "registered" ? "bring" : lifecycle === "error" ? "retry" : "refresh"
            ),
          })
        );
        if (lifecycle === "managed") expect(refresh).toHaveBeenCalledExactlyOnceWith(target, false);
        else expect(bring).toHaveBeenCalledExactlyOnceWith(target);
      }
    }
  );
  it("hides management actions for a denied viewer and disables an in-flight refresh", async () => {
    state.allowed = false;
    const result = render(tree(locale, <List />));
    await userEvent.click(
      screen.getByRole("button", { name: shared("rowActions", { label: chrome("clusters") }) })
    );
    expect(screen.queryByRole("menuitem", { name: actions("refresh") })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: actions("fullPreflight") })).toBeNull();
    await userEvent.keyboard("{Escape}");
    state.allowed = true;
    result.rerender(tree(locale, <List refreshing />));
    await userEvent.click(
      screen.getByRole("button", { name: shared("rowActions", { label: chrome("clusters") }) })
    );
    expect(screen.getByRole("menuitem", { name: actions("refresh") })).toHaveAttribute(
      "aria-disabled",
      "true"
    );
    expect(screen.getByRole("menuitem", { name: actions("fullPreflight") })).toHaveAttribute(
      "aria-disabled",
      "true"
    );
  });
  it("hydrates locale-specific row and detail labels without changing identity", async () => {
    const element = tree(
      locale,
      <>
        <List />
        <ClusterDetail
          {...detailProps({ cluster, slug: cluster.slug, renderLiveStats: () => null })}
        />
      </>
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(element);
    document.body.append(container);
    expect(container).toHaveTextContent(detail("management"));
    const recoverable = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, element, { onRecoverableError: recoverable });
      });
      expect(recoverable).not.toHaveBeenCalled();
      expect(container.querySelector("input")?.getAttribute("placeholder")).toBe(
        list("searchPlaceholder")
      );
      expect(container).toHaveTextContent(cluster.slug);
      expect(container).toHaveTextContent(detail("capabilitiesTitle"));
      expect(container).toHaveTextContent(chrome("lifecycle.managed"));
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });
  it("translates live-stat headings without turning unavailable metrics into known values", () => {
    const result = render(
      tree(
        locale,
        <ClusterLiveStats
          appCount={12}
          appLoading={false}
          metricsLoading={false}
          metrics={{
            available: true,
            nodeCount: 3,
            podRunningRatio: 0.987,
            cpuUtilization: 0.742,
            memoryUtilization: 0.368,
            deploymentReadyRatio: 1,
          }}
        />
      )
    );
    for (const key of ["appsBound", "nodes", "podsRunning", "cpu", "memory"])
      expect(document.body).toHaveTextContent(detail("stats." + key));
    for (const value of ["12", "3", "98.7%", "74.2%", "36.8%"])
      expect(screen.getByText(value)).toBeTruthy();
    result.rerender(
      tree(
        locale,
        <ClusterLiveStats
          appCount={0}
          appLoading={false}
          metricsLoading={false}
          metrics={{
            available: false,
            nodeCount: 3,
            podRunningRatio: 0.987,
            cpuUtilization: 0.742,
            memoryUtilization: 0.368,
            deploymentReadyRatio: 1,
          }}
        />
      )
    );
    expect(screen.getByText("0")).toBeTruthy();
    expect(screen.getAllByText("—")).toHaveLength(4);
    expect(screen.queryByText("74.2%")).toBeNull();
  });
  it("translates a missing target without an action or live-stats call", () => {
    const stats = vi.fn();
    render(
      tree(
        locale,
        <ClusterDetail
          {...detailProps({ cluster: undefined, slug: cluster.slug, renderLiveStats: stats })}
        />
      )
    );
    expect(screen.getByText(detail("notFoundTitle", { slug: cluster.slug }))).toBeTruthy();
    expect(screen.getByText(detail("notFoundDescription"))).toBeTruthy();
    expect(screen.getByRole("link", { name: detail("back") })).toHaveAttribute("href", "/clusters");
    expect(stats).not.toHaveBeenCalled();
  });
  it.each(["ok", "no-message", "refused"] as const)(
    "preserves bring/refresh mutation shapes and %s feedback",
    async (mode) => {
      const ctx = context(locale);
      ctx.setMode(mode);
      const hook = renderHook(useClusterActions, { wrapper: ctx.Wrapper });
      for (const operation of ["bring", "refresh", "fullPreflight"] as const) {
        await act(async () => {
          if (operation === "bring") await hook.result.current.onBring(cluster);
          else await hook.result.current.onRefresh(cluster, operation === "fullPreflight");
        });
        if (mode === "ok")
          expect(feedback.success).toHaveBeenLastCalledWith(
            actions(operation + "Accepted", { slug: cluster.slug })
          );
        else
          expect(feedback.error).toHaveBeenLastCalledWith(
            mode === "refused"
              ? "SERVER_DIAGNOSTIC_LITERAL"
              : actions(operation === "bring" ? "bringFailed" : "refreshFailed")
          );
      }
      expect(
        ctx.requests
          .filter((r) => /Bring|Refresh/.test(r.operationName))
          .map((r) => ({ operationName: r.operationName, variables: r.variables }))
      ).toEqual([
        {
          operationName: "BringClusterIntoManagement",
          variables: { input: { clusterId: cluster.id } },
        },
        {
          operationName: "RefreshClusterManagement",
          variables: { input: { clusterId: cluster.id, forcePreflight: false } },
        },
        {
          operationName: "RefreshClusterManagement",
          variables: { input: { clusterId: cluster.id, forcePreflight: true } },
        },
      ]);
    }
  );
});
