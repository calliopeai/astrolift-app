import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildSchema, parse, validate } from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { parse as parseMessage } from "@formatjs/icu-messageformat-parser";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClusterSettingsClient } from "@/app/(app)/clusters/[slug]/settings/cluster-settings-client";
import { locales } from "@/i18n/config";
import { NAV } from "@/lib/shell/nav-model";
import { CLUSTER } from "../settings/fixtures";
import { ClusterHeader } from "./ClusterHeader";
import { ClusterTabs, clusterTabs } from "./ClusterTabs";
import { clusterCrumbs, localizedClusterCrumbs } from "./clusters-list";
const state = vi.hoisted(() => ({ pathname: "/clusters/SLUG_LITERAL/settings" }));
vi.mock("next/navigation", () => ({
  usePathname: () => state.pathname,
  useSearchParams: () => new URLSearchParams("section=users"),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ loading: false, granted: new Set(), can: () => false }),
}));
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const tFor = (locale: string, namespace = "clusters.chrome") =>
  createTranslator({ locale, messages: catalogs[locale], namespace });
const cluster = {
  ...CLUSTER,
  __typename: "AstroliftTenantCluster",
  id: "c0ffee00-0000-4000-8000-000000000001",
  slug: "SLUG_LITERAL",
  name: "NAME_LITERAL",
  region: "REGION_LITERAL",
  providerPluginSlug: "PROVIDER_LITERAL",
  lifecycle: "managed",
  isActive: true,
  oidcAuthConfig: null,
  organizationSlug: "ORG_LITERAL",
  managedAt: null,
  lastHeartbeatAt: null,
  heartbeatStatus: "UNKNOWN",
  heartbeatAgeSeconds: null,
  createdByUsername: "ACTOR_LITERAL",
  lastBootstrapRun: null,
};
type Mode = "ok" | "error" | "null";
function context(locale: string, lifecycle = "managed", initialMode: Mode = "ok") {
  let mode = initialMode;
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://cluster-chrome.test.invalid/graphql/",
      fetch: async (_url, options) => {
        const request = JSON.parse(String(options?.body));
        requests.push(request);
        expect(validate(schema, parse(request.query))).toEqual([]);
        expect(request.operationName).toBe("GetCluster");
        if (mode === "error") throw new Error("RAW_CLUSTER_READ_FAILURE_LITERAL");
        return Response.json({
          data: {
            astroliftCluster:
              mode === "null"
                ? null
                : {
                    ...cluster,
                    slug: request.variables.slug,
                    name:
                      request.variables.slug === "NEXT_SLUG_LITERAL"
                        ? "NEXT_NAME_LITERAL"
                        : cluster.name,
                    lifecycle,
                  },
          },
        });
      },
    }),
  });
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-10-01T12:00:00Z")}
        timeZone="America/Costa_Rica"
      >
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    client,
    requests,
    Wrapper,
    setMode: (next: Mode) => {
      mode = next;
    },
  };
}
function tree(locale: string, children: React.ReactNode) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={new Date("2026-10-01T12:00:00Z")}
      timeZone="America/Costa_Rica"
    >
      {children}
    </NextIntlClientProvider>
  );
}
beforeEach(() => {
  state.pathname = "/clusters/SLUG_LITERAL/settings";
  localStorage.clear();
});
describe.each(locales)("Cluster chrome in %s", (locale) => {
  it.each(["registered", "managing", "managed", "error"])(
    "localizes observed %s in the actual settings route without unauthorized card reads",
    async (lifecycle) => {
      const ctx = context(locale, lifecycle),
        t = tFor(locale);
      render(<ClusterSettingsClient slug={cluster.slug} />, { wrapper: ctx.Wrapper });
      await screen.findByRole("heading", { name: cluster.name });
      expect(document.body).toHaveTextContent(t(`lifecycle.${lifecycle}`));
      expect(document.body).toHaveTextContent(cluster.region);
      expect(document.body).toHaveTextContent(cluster.providerPluginSlug);
      const tabs = screen.getByRole("navigation", { name: t("tabsAria") });
      for (const tab of clusterTabs(cluster.slug, "", "settings")) {
        const link = within(tabs).getByRole("link", { name: t(`tabs.${tab.key}`) });
        expect(link).toHaveAttribute("href", tab.href);
        expect(link.getAttribute("aria-current")).toBe(tab.active ? "page" : null);
      }
      expect(
        ctx.requests.map(({ operationName, variables }) => ({ operationName, variables }))
      ).toEqual([{ operationName: "GetCluster", variables: { slug: cluster.slug } }]);
    }
  );
  it("retains caller failed-read and verified missing titles rather than replacing source meaning", async () => {
    const ctx = context(locale, "managed", "error"),
      source = tFor(locale, "clusterSettings.source"),
      t = tFor(locale);
    render(<ClusterSettingsClient slug={cluster.slug} />, { wrapper: ctx.Wrapper });
    await screen.findByRole("heading", { name: source("readFailed") });
    expect(document.body).toHaveTextContent("RAW_CLUSTER_READ_FAILURE_LITERAL");
    expect(screen.queryByRole("heading", { name: t("notFound") })).toBeNull();
    ctx.setMode("null");
    await userEvent.click(screen.getByRole("button", { name: source("retry") }));
    await screen.findByRole("heading", { name: source("notFoundHeader") });
    expect(
      ctx.requests.every(
        (r) => r.operationName === "GetCluster" && r.variables.slug === cluster.slug
      )
    ).toBe(true);
  });
  it("preserves cached source recovery and binds header links to a changed observed target", async () => {
    const ctx = context(locale),
      source = tFor(locale, "clusterSettings.source"),
      t = tFor(locale);
    const result = render(<ClusterSettingsClient slug={cluster.slug} />, { wrapper: ctx.Wrapper });
    await screen.findByRole("heading", { name: cluster.name });
    ctx.setMode("error");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] }).catch(() => {});
    });
    expect(screen.getByRole("heading", { name: cluster.name })).toBeTruthy();
    expect(document.body).toHaveTextContent(source("cached"));
    ctx.setMode("ok");
    await userEvent.click(screen.getByRole("button", { name: source("retry") }));
    await waitFor(() => expect(screen.queryByText("RAW_CLUSTER_READ_FAILURE_LITERAL")).toBeNull());
    result.rerender(<ClusterSettingsClient slug="NEXT_SLUG_LITERAL" />);
    await screen.findByRole("heading", { name: "NEXT_NAME_LITERAL" });
    expect(
      within(screen.getByRole("navigation", { name: t("tabsAria") })).getByRole("link", {
        name: t("tabs.settings"),
      })
    ).toHaveAttribute("href", "/clusters/NEXT_SLUG_LITERAL/settings");
    expect(ctx.requests.some((r) => r.variables.slug === "NEXT_SLUG_LITERAL")).toBe(true);
  });
  it("uses keyboard navigation with identical breadcrumb hrefs, tab IDs and active matching", async () => {
    const t = tFor(locale),
      shell = tFor(locale, "shared.shellHeader");
    render(tree(locale, <ClusterHeader slug={cluster.slug} cluster={cluster} />));
    const trigger = screen.getByRole("button", { name: shell("switch", { label: t("admin") }) });
    trigger.focus();
    await userEvent.keyboard("{Enter}");
    for (const item of NAV.find((area) => area.key === "admin")!.groups.flatMap(
      (group) => group.functions
    )) {
      const link = screen.getByRole("menuitem", { name: t(`functions.${item.key}`) });
      expect(link).toHaveAttribute("href", item.href);
      expect(link.getAttribute("aria-current")).toBe(item.key === "clusters" ? "page" : null);
    }
    await userEvent.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
    const overview = within(screen.getByRole("navigation", { name: t("tabsAria") })).getByRole(
      "link",
      { name: t("tabs.overview") }
    );
    overview.focus();
    await userEvent.tab();
    expect(screen.getByRole("link", { name: t("tabs.status") })).toHaveFocus();
    const routes = (list: ReturnType<typeof clusterTabs>) =>
      list.map(({ key, href, active }) => ({ key, href, active }));
    expect(
      routes(
        clusterTabs(cluster.slug, "/clusters/SLUG_LITERAL/health", undefined, (key) =>
          t(`tabs.${key}`)
        )
      )
    ).toEqual(routes(clusterTabs(cluster.slug, "/clusters/SLUG_LITERAL/health")));
    const crumbs = localizedClusterCrumbs(t, cluster.name),
      original = clusterCrumbs(cluster.name);
    expect(crumbs.map((c) => c.href)).toEqual(original.map((c) => c.href));
    expect(crumbs[0].switcher?.map(({ href, active }) => ({ href, active }))).toEqual(
      original[0].switcher?.map(({ href, active }) => ({ href, active }))
    );
    expect(crumbs.at(-1)?.label).toBe(cluster.name);
  });
  it("preserves future breadcrumb metadata and the legacy factory while localizing only opted-in labels", () => {
    const admin = NAV.find((area) => area.key === "admin")!,
      group = admin.groups[0];
    const item = {
      ...group.functions[0],
      key: "future-function",
      label: "FUTURE_NAV_LABEL_LITERAL",
      href: "/FUTURE_ROUTE_LITERAL",
    };
    group.functions.push(item);
    try {
      const projected = localizedClusterCrumbs(tFor(locale));
      expect(projected).toHaveLength(2);
      expect(projected[1]).toEqual({ label: tFor(locale)("clusters") });
      expect(projected[0].switcher?.find((option) => option.href === item.href)).toEqual({
        label: item.label,
        href: item.href,
        active: false,
      });
      expect(clusterCrumbs()[0].label).toBe("Admin");
      expect(clusterCrumbs()[1].label).toBe("Clusters");
    } finally {
      group.functions.pop();
    }
  });
  it("preserves unknown metadata and caller-owned action/source nodes literally", () => {
    const t = tFor(locale);
    const result = render(
      tree(
        locale,
        <ClusterHeader
          slug={cluster.slug}
          cluster={{ ...cluster, lifecycle: "FUTURE_STATUS_LITERAL", isActive: false }}
          primaryAction={<button>ACTION_LITERAL</button>}
          menu={<button>MENU_LITERAL</button>}
        />
      )
    );
    for (const literal of [
      cluster.name,
      cluster.region,
      cluster.providerPluginSlug,
      "FUTURE_STATUS_LITERAL",
      "ACTION_LITERAL",
      "MENU_LITERAL",
    ])
      expect(document.body).toHaveTextContent(literal);
    expect(document.body).toHaveTextContent(t("inactive"));
    result.rerender(
      tree(
        locale,
        <ClusterHeader
          slug={cluster.slug}
          cluster={null}
          emptyTitle={<span>CALLER_SOURCE_LITERAL</span>}
        />
      )
    );
    expect(screen.getByRole("heading", { name: "CALLER_SOURCE_LITERAL" })).toBeTruthy();
    result.rerender(tree(locale, <ClusterHeader slug={cluster.slug} cluster={null} />));
    expect(screen.getByRole("heading", { name: t("notFound") })).toBeTruthy();
  });
  it("hydrates request-local chrome without changing observed identity or language", async () => {
    const container = document.createElement("div"),
      recoverable = vi.fn();
    document.body.append(container);
    const element = tree(
      locale,
      <ClusterHeader slug={cluster.slug} cluster={{ ...cluster, isActive: false }} />
    );
    container.innerHTML = renderToString(element);
    const before = container.textContent;
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, element, { onRecoverableError: recoverable });
      });
      expect(container.textContent).toBe(before);
      expect(recoverable).not.toHaveBeenCalled();
      expect(container).toHaveTextContent(tFor(locale)("lifecycle.managed"));
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });
  it("changes chrome locale while preserving navigation and source identity", () => {
    const result = render(tree("en", <ClusterTabs slug={cluster.slug} active="activity" />));
    result.rerender(tree(locale, <ClusterTabs slug={cluster.slug} active="activity" />));
    const link = screen.getByRole("link", { name: tFor(locale)("tabs.activity") });
    expect(link).toHaveAttribute("aria-current", "page");
    expect(link).toHaveAttribute("href", `/clusters/${cluster.slug}/activity`);
  });
  it("has valid matching ICU shape and genuine lifecycle/settings translations", () => {
    const flat = (node: Record<string, unknown>): Record<string, string> =>
      Object.fromEntries(
        Object.entries(node).flatMap(([key, value]) =>
          typeof value === "string"
            ? [[key, value]]
            : Object.entries(flat(value as Record<string, unknown>)).map(([child, copy]) => [
                `${key}.${child}`,
                copy,
              ])
        )
      );
    const english = flat(catalogs.en.clusters.chrome),
      copy = flat(catalogs[locale].clusters.chrome);
    expect(Object.keys(copy)).toEqual(Object.keys(english));
    expect(Object.keys(copy)).toHaveLength(32);
    for (const [key, message] of Object.entries(copy)) {
      expect(parseMessage(message).every((node) => node.type === 0)).toBe(true);
      expect(tFor(locale)(key)).toBe(message);
    }
    if (locale !== "en") {
      expect(copy["lifecycle.managing"]).not.toBe(english["lifecycle.managing"]);
      expect(copy["tabs.settings"]).not.toBe(english["tabs.settings"]);
    }
  });
});
