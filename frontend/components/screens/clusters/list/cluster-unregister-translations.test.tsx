import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildSchema, parse, validate } from "graphql";
import { parse as parseMessage } from "@formatjs/icu-messageformat-parser";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClustersClient } from "@/app/(app)/clusters/clusters-client";
import { ClusterDetailClient } from "@/app/(app)/clusters/[slug]/cluster-detail-client";
import { locales } from "@/i18n/config";
import { CLUSTER } from "../settings/fixtures";
import { ClusterDetail } from "./ClusterDetail";
import { TooltipProvider } from "@/components/ui/tooltip";
vi.mock("@/app/(app)/clusters/_components/cluster-live-stats", () => ({
  ClusterLiveStatsContainer: () => null,
}));
import { detailProps } from "./fixtures";
import { useUnregisterReview } from "./use-unregister-review";
import { useClusterDetail } from "./use-cluster-detail";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
const state = vi.hoisted(() => ({
  allowed: true,
  push: vi.fn(),
  pathname: "/clusters/SLUG_LITERAL",
  search: "q=NAME_LITERAL&page=2",
}));
vi.mock("sonner", () => ({ toast: feedback }));
const feedback = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push, replace: vi.fn() }),
  usePathname: () => state.pathname,
  useSearchParams: () => new URLSearchParams(state.search),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    loading: false,
    granted: new Set(state.allowed ? ["cluster.unregister"] : []),
    can: (permission: unknown) => state.allowed && permission === "cluster.unregister",
  }),
}));
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const tFor = (locale: string, namespace = "clusters.unregister") =>
  createTranslator({ locale, messages: catalogs[locale], namespace });
const cluster = {
  ...CLUSTER,
  __typename: "AstroliftTenantCluster",
  id: "c0ffee00-0000-4000-8000-000000000001",
  slug: "SLUG_LITERAL",
  name: "NAME_LITERAL",
  region: "REGION_LITERAL",
  providerPluginSlug: "PROVIDER_LITERAL",
  lifecycle: "registered",
  isActive: true,
  oidcAuthConfig: null,
  organizationSlug: "ORG_LITERAL",
  managedAt: null,
  lastHeartbeatAt: null,
  heartbeatStatus: "never_seen",
  heartbeatAgeSeconds: null,
  createdByUsername: "ACTOR_LITERAL",
  lastBootstrapRun: null,
};
type Kind = "list" | "detail";
type Mode =
  | "ok"
  | "refused"
  | "transport"
  | "no-message"
  | "refresh-failed"
  | "refused-read-failed"
  | "deferred"
  | "replacement"
  | "withdrawn";
function context(locale: string, initialMode: Mode = "ok") {
  let mode = initialMode,
    seenWrite = false;
  let release: (() => void) | undefined;
  const retired = new Set<string>(),
    requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://unregister.test.invalid/graphql/",
      fetch: async (_url, options) => {
        const request = JSON.parse(String(options?.body));
        requests.push(request);
        expect(validate(schema, parse(request.query))).toEqual([]);
        let data;
        if (request.operationName === "UnregisterTenantCluster") {
          seenWrite = true;
          if (mode === "deferred")
            await new Promise<void>((resolve) => {
              release = resolve;
            });
          if (mode === "transport") throw new Error("RAW_PROVIDER_TRANSPORT_LITERAL");
          const ok = !["refused", "refused-read-failed", "no-message"].includes(mode);
          if (ok) retired.add(request.variables.input.id);
          data = {
            unregisterTenantCluster: {
              ok,
              errors:
                ok || mode === "no-message"
                  ? []
                  : [{ code: "PRECONDITION", message: "RAW_SERVER_REFUSAL_LITERAL" }],
              data: ok ? { id: request.variables.input.id, deleted: true } : null,
            },
          };
        } else {
          if (seenWrite && ["refresh-failed", "refused-read-failed"].includes(mode))
            throw new Error("RAW_REFRESH_FAILURE_LITERAL");
          if (request.operationName === "GetCluster") {
            const c =
              request.variables.slug === "NEXT_SLUG_LITERAL"
                ? {
                    ...cluster,
                    id: "c0ffee00-0000-4000-8000-000000000002",
                    slug: "NEXT_SLUG_LITERAL",
                    name: "NEXT_NAME_LITERAL",
                  }
                : mode === "replacement"
                  ? { ...cluster, id: "c0ffee00-0000-4000-8000-000000000003" }
                  : cluster;
            data = { astroliftCluster: mode === "withdrawn" || retired.has(c.id) ? null : c };
          } else if (request.operationName === "ListClustersPage")
            data = {
              astroliftClustersPage: {
                __typename: "AstroliftTenantClusterPage",
                items: retired.has(cluster.id) ? [] : [cluster],
                totalCount: retired.has(cluster.id) ? 0 : 51,
                page: 2,
                pageSize: 25,
                hasNext: false,
                endCursor: null,
                nextCursor: null,
              },
            };
          else if (request.operationName === "ListClusters")
            data = { astroliftClusters: retired.has(cluster.id) ? [] : [cluster] };
          else throw new Error("Unexpected operation " + request.operationName);
        }
        return Response.json({ data });
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
        <ApolloProvider client={client}>
          <TooltipProvider>{children}</TooltipProvider>
        </ApolloProvider>
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
    waiting: () => Boolean(release),
    release: () => release?.(),
  };
}
function mount(locale: string, kind: Kind, mode: Mode = "ok") {
  const ctx = context(locale, mode);
  state.pathname = kind === "list" ? "/clusters" : `/clusters/${cluster.slug}`;
  return {
    ...ctx,
    result: render(
      kind === "list" ? <ClustersClient /> : <ClusterDetailClient slug={cluster.slug} />,
      { wrapper: ctx.Wrapper }
    ),
  };
}
async function review(locale: string, kind: Kind) {
  const t = tFor(locale);
  const label =
    kind === "list"
      ? tFor(locale, "shared.list")("rowActions", {
          label: tFor(locale, "clusters.chrome")("clusters"),
        })
      : tFor(locale, "clusters.detail")("menuLabel");
  await userEvent.click(await screen.findByRole("button", { name: label }));
  await userEvent.click(screen.getByRole("menuitem", { name: t("action") }));
  await screen.findByRole("alertdialog");
}
const writes = (ctx: ReturnType<typeof context>) =>
  ctx.requests.filter((request) => request.operationName === "UnregisterTenantCluster");
beforeEach(() => {
  vi.clearAllMocks();
  state.allowed = true;
  state.push.mockImplementation(() => {});
  localStorage.clear();
});
describe.each(locales)("Unregister flow in %s", (locale) => {
  it.each(["list", "detail"] as const)(
    "%s uses the exact target and refreshes accepted reads with existing variables",
    async (kind) => {
      const ctx = mount(locale, kind),
        t = tFor(locale);
      await review(locale, kind);
      expect(screen.getByRole("alertdialog")).toHaveTextContent(t("title", { slug: cluster.slug }));
      const before = ctx.requests[0];
      await userEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", { name: t("action") })
      );
      await waitFor(() =>
        expect(feedback.success).toHaveBeenCalledWith(t("accepted", { slug: cluster.slug }))
      );
      expect(writes(ctx)[0].variables).toEqual({ input: { id: cluster.id } });
      expect(
        ctx.requests
          .filter((r) => r.operationName === before.operationName)
          .every((r) => JSON.stringify(r.variables) === JSON.stringify(before.variables))
      ).toBe(true);
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
      expect(feedback.error).not.toHaveBeenCalled();
      if (kind === "detail")
        await waitFor(() => expect(state.push).toHaveBeenCalledWith("/clusters"));
    }
  );
  it.each(
    (["list", "detail"] as const).flatMap((kind) =>
      (["refused", "transport", "no-message", "refused-read-failed"] as const).map(
        (mode) => [kind, mode] as const
      )
    )
  )("%s/%s preserves the review and performs no read after refusal", async (kind, mode) => {
    const ctx = mount(locale, kind, mode),
      t = tFor(locale);
    await review(locale, kind);
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: t("action") })
    );
    await waitFor(() =>
      expect(feedback.error).toHaveBeenCalledWith(
        mode === "transport"
          ? "RAW_PROVIDER_TRANSPORT_LITERAL"
          : mode === "no-message"
            ? t("failed")
            : "RAW_SERVER_REFUSAL_LITERAL"
      )
    );
    expect(screen.getByRole("alertdialog")).toHaveTextContent(cluster.slug);
    expect(ctx.requests).toHaveLength(2);
    expect(writes(ctx)).toHaveLength(1);
    expect(feedback.success).not.toHaveBeenCalled();
    expect(feedback.warning).not.toHaveBeenCalled();
    expect(state.push).not.toHaveBeenCalled();
    ctx.setMode("ok");
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: t("action") })
    );
    await waitFor(() =>
      expect(feedback.success).toHaveBeenCalledWith(t("accepted", { slug: cluster.slug }))
    );
    expect(writes(ctx)[1].variables).toEqual({ input: { id: cluster.id } });
  });
  it.each(["list", "detail"] as const)(
    "%s keeps accepted unregister distinct from failed refresh",
    async (kind) => {
      const ctx = mount(locale, kind, "refresh-failed"),
        t = tFor(locale);
      await review(locale, kind);
      await userEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", { name: t("action") })
      );
      await waitFor(() =>
        expect(feedback.success).toHaveBeenCalledWith(t("accepted", { slug: cluster.slug }))
      );
      expect(feedback.warning).toHaveBeenCalledWith(t("refreshWarning"));
      expect(feedback.error).not.toHaveBeenCalled();
      expect(writes(ctx)).toHaveLength(1);
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    }
  );
  it("keeps accepted unregister when detail navigation fails", async () => {
    const ctx = mount(locale, "detail"),
      t = tFor(locale);
    state.push.mockImplementation(() => {
      throw new Error("RAW_NAV_FAILURE_LITERAL");
    });
    await review(locale, "detail");
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: t("action") })
    );
    await waitFor(() => expect(feedback.warning).toHaveBeenCalledWith(t("navigationWarning")));
    expect(feedback.success).toHaveBeenCalledWith(t("accepted", { slug: cluster.slug }));
    expect(feedback.error).not.toHaveBeenCalled();
    expect(writes(ctx)).toHaveLength(1);
  });
  it.each(["list", "detail"] as const)("%s cancels without a write or refresh", async (kind) => {
    const ctx = mount(locale, kind);
    await review(locale, kind);
    await userEvent.click(
      screen.getByRole("button", { name: tFor(locale, "shared.confirmation")("cancel") })
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(writes(ctx)).toHaveLength(0);
    expect(ctx.requests).toHaveLength(1);
  });
  it("withdraws reviews on actual permission or observed identity withdrawal", async () => {
    const ctx = mount(locale, "detail");
    await review(locale, "detail");
    ctx.setMode("replacement");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] });
    });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await review(locale, "detail");
    state.allowed = false;
    ctx.result.rerender(<ClusterDetailClient slug={cluster.slug} />);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(writes(ctx)).toHaveLength(0);
  });
  it("does not navigate a newer detail target after an old accepted reply", async () => {
    const ctx = mount(locale, "detail", "deferred"),
      t = tFor(locale);
    await review(locale, "detail");
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: t("action") })
    );
    await waitFor(() => expect(ctx.waiting()).toBe(true));
    ctx.result.rerender(<ClusterDetailClient slug="NEXT_SLUG_LITERAL" />);
    await screen.findByRole("heading", { name: "NEXT_NAME_LITERAL" });
    await act(async () => ctx.release());
    await waitFor(() => expect(feedback.success).toHaveBeenCalled());
    expect(state.push).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "NEXT_NAME_LITERAL" })).toBeTruthy();
    expect(writes(ctx)[0].variables).toEqual({ input: { id: cluster.id } });
  });
  it("withdraws an actual reviewed row before any mutation and requires a fresh review when it returns", async () => {
    const ctx = mount(locale, "detail");
    await review(locale, "detail");
    ctx.setMode("withdrawn");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] });
    });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(writes(ctx)).toHaveLength(0);
    ctx.setMode("ok");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] });
    });
    await screen.findByRole("heading", { name: cluster.name });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(writes(ctx)).toHaveLength(0);
  });
  it("does not navigate after a pending accepted reply crosses route ABA", async () => {
    const ctx = mount(locale, "detail", "deferred"),
      t = tFor(locale);
    await review(locale, "detail");
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: t("action") })
    );
    await waitFor(() => expect(ctx.waiting()).toBe(true));
    ctx.result.rerender(<ClusterDetailClient slug="NEXT_SLUG_LITERAL" />);
    await screen.findByRole("heading", { name: "NEXT_NAME_LITERAL" });
    ctx.result.rerender(<ClusterDetailClient slug={cluster.slug} />);
    await screen.findByRole("heading", { name: cluster.name });
    await act(async () => ctx.release());
    await waitFor(() => expect(feedback.success).toHaveBeenCalled());
    expect(state.push).not.toHaveBeenCalled();
    expect(writes(ctx)).toHaveLength(1);
    expect(writes(ctx)[0].variables).toEqual({ input: { id: cluster.id } });
  });
  it("rejects captured review callbacks after source ABA", async () => {
    const ctx = context(locale),
      onUnregister = vi.fn().mockResolvedValue(undefined);
    const hook = renderHook(({ source }) => useUnregisterReview([cluster], source), {
      wrapper: ctx.Wrapper,
      initialProps: { source: "A" },
    });
    act(() => hook.result.current.open(cluster));
    const stale = hook.result.current.confirm;
    hook.rerender({ source: "B" });
    hook.rerender({ source: "A" });
    await act(async () => expect(await stale(onUnregister)).toBe(false));
    expect(onUnregister).not.toHaveBeenCalled();
    expect(feedback.error).toHaveBeenCalledWith(tFor(locale)("sourceChanged"));
  });
  it("retains the same reviewed target across locale changes and an old completion cannot close a newer review", async () => {
    const ctx = context("en");
    let resolve: (() => void) | undefined;
    const pending = new Promise<void>((done) => {
      resolve = done;
    });
    const onUnregister = vi.fn<(target: AstroliftTenantCluster) => Promise<void>>(() => pending);
    const content = (locale: string, current: typeof cluster) => (
      <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
        <ClusterDetail
          {...detailProps({
            slug: current.slug,
            cluster: current,
            renderLiveStats: () => null,
            onUnregister,
          })}
        />
      </NextIntlClientProvider>
    );
    const result = render(content("en", cluster), { wrapper: ctx.Wrapper });
    await review("en", "detail");
    result.rerender(content(locale, cluster));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      tFor(locale)("title", { slug: cluster.slug })
    );
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: tFor(locale)("action") })
    );
    const next = {
      ...cluster,
      id: "c0ffee00-0000-4000-8000-000000000002",
      slug: "NEXT_SLUG_LITERAL",
      name: "NEXT_NAME_LITERAL",
    };
    result.rerender(content(locale, next));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await review(locale, "detail");
    await act(async () => resolve?.());
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      tFor(locale)("title", { slug: next.slug })
    );
    expect(onUnregister).toHaveBeenCalledTimes(1);
    expect(onUnregister.mock.calls[0][0]).toMatchObject({ id: cluster.id, slug: cluster.slug });
  });
  it("refuses an old detail callback after route ABA before any mutation", async () => {
    const ctx = context(locale);
    const hook = renderHook(({ slug }) => useClusterDetail(slug), {
      wrapper: ctx.Wrapper,
      initialProps: { slug: cluster.slug },
    });
    await waitFor(() => expect(hook.result.current.cluster?.id).toBe(cluster.id));
    const stale = hook.result.current.onUnregister;
    hook.rerender({ slug: "NEXT_SLUG_LITERAL" });
    await waitFor(() => expect(hook.result.current.cluster?.slug).toBe("NEXT_SLUG_LITERAL"));
    hook.rerender({ slug: cluster.slug });
    await waitFor(() => expect(hook.result.current.cluster?.id).toBe(cluster.id));
    await act(async () => expect(stale(cluster)).rejects.toThrow(tFor(locale)("sourceChanged")));
    expect(writes(ctx)).toHaveLength(0);
    expect(state.push).not.toHaveBeenCalled();
  });
  it("has genuine ICU copy and hydrates a locale-bound reviewed target", async () => {
    const messages = catalogs[locale].clusters.unregister;
    expect(Object.keys(messages)).toEqual(Object.keys(catalogs.en.clusters.unregister));
    for (const [key, value] of Object.entries(messages)) {
      expect(() => parseMessage(value as string)).not.toThrow();
      expect(tFor(locale)(key, { slug: "RAW_SLUG_LITERAL" })).not.toContain("{slug}");
      if (locale !== "en") expect(value).not.toBe(catalogs.en.clusters.unregister[key]);
    }
    const container = document.createElement("div");
    document.body.append(container);
    const ctx = context(locale);
    const tree = (
      <ctx.Wrapper>
        <ClusterDetail
          {...detailProps({ slug: cluster.slug, cluster, renderLiveStats: () => null })}
        />
      </ctx.Wrapper>
    );
    container.innerHTML = renderToString(tree);
    const before = container.textContent,
      recoverable = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, tree, { onRecoverableError: recoverable });
      });
      expect(container.textContent).toBe(before);
      expect(recoverable).not.toHaveBeenCalled();
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });
});
