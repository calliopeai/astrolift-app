import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import {
  parse as parseMessage,
  type MessageFormatElement,
} from "@formatjs/icu-messageformat-parser";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildSchema, parse, validate } from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClusterSettingsClient } from "@/app/(app)/clusters/[slug]/settings/cluster-settings-client";
import { locales } from "@/i18n/config";
import { CLUSTER, INGRESS_AUTH } from "./fixtures";
import { IngressAuthView } from "./IngressAuth";

const state = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  allowed: true,
}));
vi.mock("sonner", () => ({ toast: state }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    granted: new Set(state.allowed ? ["cluster.update"] : []),
    loading: false,
    can: () => state.allowed,
  }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/clusters/prod-west/settings",
  useSearchParams: () => new URLSearchParams("section=ingress-auth"),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const tFor = (locale: string) =>
  createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "clusterSettings.ingressAuth",
  });
const saved = {
  user_pool_arn: "arn:aws:cognito-idp:REGION:ACCOUNT:userpool/POOL_LITERAL",
  user_pool_client_id: "CLIENT_LITERAL",
  user_pool_domain: "DOMAIN_LITERAL",
};
const cluster = {
  ...CLUSTER,
  __typename: "AstroliftTenantCluster",
  oidcAuthConfig: null,
  albAuthConfig: saved,
  organizationSlug: "ORG_LITERAL",
  region: "REGION_LITERAL",
  isActive: true,
  managedAt: null,
  lastHeartbeatAt: null,
  heartbeatStatus: "UNKNOWN",
  heartbeatAgeSeconds: null,
  createdByUsername: "ACTOR_LITERAL",
  lastBootstrapRun: null,
};
type Mode =
  | "ok"
  | "refused"
  | "refused-read-failed"
  | "no-message"
  | "transport"
  | "refresh-failed"
  | "reconcile-refused"
  | "reconcile-transport"
  | "partial"
  | "unknown-count"
  | "pools-failed"
  | "clients-failed"
  | "read-null"
  | "read-failed"
  | "source-withdrawn"
  | "deferred";
type Request = { operationName: string; variables: Record<string, unknown> };
function context(locale: string, initialMode: Mode = "ok", initialConfigured = true, count = 1) {
  let mode = initialMode,
    currentConfig: typeof saved | null = initialConfigured ? saved : null,
    reads = 0,
    release: (() => void) | undefined;
  const requests: Request[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://cognito.test.invalid/graphql/",
      fetch: async (_url, options) => {
        const request = JSON.parse(String(options?.body));
        requests.push(request);
        expect(validate(schema, parse(request.query))).toEqual([]);
        let data;
        if (request.operationName === "GetCluster") {
          reads++;
          if (
            mode === "read-failed" ||
            (reads > 1 && ["refresh-failed", "refused-read-failed"].includes(mode))
          )
            throw new Error("RAW_CLUSTER_READ_ERROR");
          const next = request.variables.slug === "next";
          data = {
            astroliftCluster:
              mode === "read-null"
                ? null
                : {
                    ...cluster,
                    id: next ? "NEXT_CLUSTER_ID" : cluster.id,
                    slug: next ? "next" : cluster.slug,
                    albAuthConfig:
                      mode === "source-withdrawn"
                        ? null
                        : next
                          ? {
                              ...saved,
                              user_pool_client_id: "NEXT_CLIENT_LITERAL",
                              user_pool_domain: "NEXT_DOMAIN_LITERAL",
                            }
                          : currentConfig,
                  },
          };
        } else if (request.operationName === "UpdateTenantCluster") {
          if (mode === "deferred")
            await new Promise<void>((resolve) => {
              release = resolve;
            });
          if (mode === "transport") throw new Error("RAW_SAVE_TRANSPORT_ERROR");
          const ok = !["refused", "no-message", "refused-read-failed"].includes(mode);
          if (ok) currentConfig = request.variables.input.albAuthConfig;
          data = {
            updateTenantCluster: {
              ok,
              errors:
                ok || mode === "no-message"
                  ? []
                  : [
                      {
                        code: "PERMISSION_DENIED",
                        message: "RAW_SAVE_REFUSAL",
                        field: "albAuthConfig",
                      },
                    ],
              data: ok ? { ...cluster, albAuthConfig: currentConfig } : null,
            },
          };
        } else if (request.operationName === "ReconcileClusterIngresses") {
          if (mode === "reconcile-transport") throw new Error("RAW_RECONCILE_TRANSPORT_ERROR");
          const ok = mode !== "reconcile-refused";
          data = {
            reconcileClusterIngresses: {
              ok,
              errors: ok
                ? []
                : [
                    {
                      code: "PERMISSION_DENIED",
                      message: "RAW_RECONCILE_REFUSAL",
                      field: "clusterId",
                    },
                  ],
              data: ok
                ? {
                    reconciledCount: mode === "unknown-count" ? null : count,
                    skippedCount: mode === "partial" ? 2 : 0,
                    errors: mode === "partial" ? ["RAW_NAMESPACE_FAILURE"] : [],
                  }
                : null,
            },
          };
        } else if (request.operationName === "CognitoUserPools") {
          if (mode === "pools-failed") throw new Error("RAW_POOLS_READ_ERROR");
          data = {
            astroliftCognitoUserPools: [
              {
                poolId: "POOL_LITERAL",
                poolArn: saved.user_pool_arn,
                name: "POOL_NAME_LITERAL",
                domain: "DOMAIN_LITERAL",
                region: "REGION_LITERAL",
              },
              {
                poolId: "NO_DOMAIN_POOL",
                poolArn: "arn:aws:cognito-idp:REGION:ACCOUNT:userpool/NO_DOMAIN_POOL",
                name: "NO_DOMAIN_NAME_LITERAL",
                domain: null,
                region: "REGION_LITERAL",
              },
            ],
          };
        } else if (request.operationName === "CognitoUserPoolClients") {
          if (mode === "clients-failed") throw new Error("RAW_CLIENTS_READ_ERROR");
          data = {
            astroliftCognitoUserPoolClients: [
              { clientId: "CLIENT_LITERAL", clientName: "CLIENT_NAME_LITERAL" },
            ],
          };
        } else throw new Error("Unexpected operation " + request.operationName);
        return Response.json({ data });
      },
    }),
  });
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-09-30T12:00:00Z")}
        timeZone="America/Costa_Rica"
      >
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    Wrapper,
    client,
    requests,
    setMode: (next: Mode) => {
      mode = next;
    },
    waiting: () => !!release,
    release: () => release?.(),
  };
}
function mount(locale: string, mode: Mode = "ok", configured = true, count = 1) {
  const ctx = context(locale, mode, configured, count);
  const result = render(<ClusterSettingsClient slug={cluster.slug} />, { wrapper: ctx.Wrapper });
  return { ...ctx, result };
}
async function edit(locale: string) {
  await userEvent.click(await screen.findByRole("button", { name: tFor(locale)("edit") }));
  await userEvent.click(screen.getByRole("button", { name: tFor(locale)("advanced") }));
}
async function draft(locale: string) {
  const input = screen.getByRole("textbox", { name: tFor(locale)("poolDomain") });
  await userEvent.clear(input);
  await userEvent.type(input, "REVIEWED_DOMAIN_LITERAL");
  return input;
}
function mutations(requests: Request[]) {
  return requests.filter((r) =>
    ["UpdateTenantCluster", "ReconcileClusterIngresses"].includes(r.operationName)
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  state.allowed = true;
  localStorage.clear();
});

describe.each(locales)("Cognito connected review in %s", (locale) => {
  it.each([1, 3])(
    "preserves exact two-stage input and reported count %s without promising active traffic protection",
    async (count) => {
      const ctx = mount(locale, "ok", true, count);
      const t = tFor(locale);
      expect(await screen.findByText(t("configured"))).toBeTruthy();
      expect(screen.getByText(t("rolloutNotice"))).toBeTruthy();
      await edit(locale);
      await draft(locale);
      const save = screen.getByRole("button", { name: t("saveApply") });
      if (count === 1) {
        save.focus();
        await userEvent.keyboard("{Enter}");
      } else await userEvent.click(save);
      await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("applied", { count })));
      expect(state.success).toHaveBeenCalledWith(t("saved"));
      expect(state.error).not.toHaveBeenCalled();
      expect(
        mutations(ctx.requests).map((r) => ({ name: r.operationName, input: r.variables.input }))
      ).toEqual([
        {
          name: "UpdateTenantCluster",
          input: {
            id: cluster.id,
            albAuthConfig: { ...saved, user_pool_domain: "REVIEWED_DOMAIN_LITERAL" },
          },
        },
        { name: "ReconcileClusterIngresses", input: { clusterId: cluster.id } },
      ]);
      expect(
        ctx.requests.filter((r) => r.operationName === "GetCluster").map((r) => r.variables)
      ).toEqual([{ slug: cluster.slug }, { slug: cluster.slug }]);
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: t("saveApply") })).toBeNull()
      );
    }
  );
  it.each(["refused", "refused-read-failed", "no-message", "transport"] as const)(
    "%s keeps the rejected draft and does no reconciliation or refresh",
    async (mode) => {
      const ctx = mount(locale, mode);
      const t = tFor(locale);
      await edit(locale);
      const field = await draft(locale);
      await userEvent.click(screen.getByRole("button", { name: t("saveApply") }));
      await waitFor(() =>
        expect(state.error).toHaveBeenCalledWith(
          mode === "no-message"
            ? t("saveFailed")
            : mode === "transport"
              ? "RAW_SAVE_TRANSPORT_ERROR"
              : "RAW_SAVE_REFUSAL"
        )
      );
      expect(field).toHaveValue("REVIEWED_DOMAIN_LITERAL");
      expect(screen.getByRole("button", { name: t("saveApply") })).toBeEnabled();
      expect(state.success).not.toHaveBeenCalled();
      expect(state.warning).not.toHaveBeenCalled();
      expect(mutations(ctx.requests)).toHaveLength(1);
      expect(ctx.requests.filter((r) => r.operationName === "GetCluster")).toHaveLength(1);
      ctx.setMode("ok");
      await userEvent.click(screen.getByRole("button", { name: t("saveApply") }));
      await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("applied", { count: 1 })));
      expect(
        mutations(ctx.requests)
          .filter((r) => r.operationName === "UpdateTenantCluster")
          .map((r) => r.variables.input)
      ).toEqual([
        {
          id: cluster.id,
          albAuthConfig: { ...saved, user_pool_domain: "REVIEWED_DOMAIN_LITERAL" },
        },
        {
          id: cluster.id,
          albAuthConfig: { ...saved, user_pool_domain: "REVIEWED_DOMAIN_LITERAL" },
        },
      ]);
    }
  );
  it.each(["refresh-failed", "reconcile-refused", "reconcile-transport", "unknown-count"] as const)(
    "%s retains the committed config and separates unconfirmed rollout",
    async (mode) => {
      const ctx = mount(locale, mode);
      const t = tFor(locale);
      await edit(locale);
      await draft(locale);
      await userEvent.click(screen.getByRole("button", { name: t("saveApply") }));
      await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("saved")));
      const reason =
        mode === "reconcile-refused" ? "RAW_RECONCILE_REFUSAL" : "RAW_RECONCILE_TRANSPORT_ERROR";
      await waitFor(() =>
        expect(state.warning).toHaveBeenCalledWith(
          mode === "refresh-failed"
            ? t("refreshWarning")
            : mode === "unknown-count"
              ? t("savedRolloutUnconfirmed")
              : t("savedReconcileFailed", { reason })
        )
      );
      expect(state.error).not.toHaveBeenCalled();
      expect(mutations(ctx.requests)).toHaveLength(2);
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: t("saveApply") })).toBeNull()
      );
      if (mode !== "refresh-failed")
        expect(state.success).not.toHaveBeenCalledWith(t("applied", { count: 1 }));
    }
  );
  it("reports partial and skipped Ingresses as unconfirmed coverage", async () => {
    mount(locale, "partial");
    const t = tFor(locale);
    await userEvent.click(await screen.findByRole("button", { name: t("apply") }));
    await waitFor(() => expect(state.warning).toHaveBeenCalledWith(t("partial", { count: 1 })));
    expect(state.warning).toHaveBeenCalledWith(t("skipped", { count: 2 }));
  });
  it("only closes a disable operation after its config write is accepted", async () => {
    const ctx = mount(locale, "refused");
    const t = tFor(locale);
    await edit(locale);
    await draft(locale);
    await userEvent.click(screen.getByRole("switch", { name: t("disable") }));
    await waitFor(() => expect(state.error).toHaveBeenCalledWith("RAW_SAVE_REFUSAL"));
    expect(screen.getByRole("textbox", { name: t("poolDomain") })).toHaveValue(
      "REVIEWED_DOMAIN_LITERAL"
    );
    expect(screen.getByRole("button", { name: t("saveApply") })).toBeEnabled();
    expect(mutations(ctx.requests)).toHaveLength(1);
    ctx.setMode("ok");
    await userEvent.click(screen.getByRole("switch", { name: t("disable") }));
    await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("removed", { count: 1 })));
    expect(mutations(ctx.requests).map((r) => r.variables.input)).toEqual([
      { id: cluster.id, albAuthConfig: null },
      { id: cluster.id, albAuthConfig: null },
      { clusterId: cluster.id },
    ]);
    expect(screen.getByText(t("noConfig"))).toBeTruthy();
  });
  it("keeps literal failed pool/client diagnostics and retries only the actual source", async () => {
    const ctx = mount(locale, "pools-failed");
    const t = tFor(locale);
    await userEvent.click(await screen.findByRole("button", { name: t("edit") }));
    expect(await screen.findByText("RAW_POOLS_READ_ERROR")).toBeTruthy();
    expect(screen.getByText(t("poolsUnavailable"))).toBeTruthy();
    ctx.setMode("ok");
    await userEvent.click(screen.getByRole("button", { name: t("retry") }));
    await waitFor(() => expect(screen.queryByText("RAW_POOLS_READ_ERROR")).toBeNull());
    expect(
      ctx.requests.filter((r) => r.operationName === "CognitoUserPools").map((r) => r.variables)
    ).toEqual([{ clusterId: cluster.id }, { clusterId: cluster.id }]);
    ctx.setMode("clients-failed");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["CognitoUserPoolClients"] }).catch(() => {});
    });
    expect(await screen.findByText("RAW_CLIENTS_READ_ERROR")).toBeTruthy();
    expect(screen.getByText(t("clientsUnavailable"))).toBeTruthy();
    ctx.setMode("ok");
    await userEvent.click(screen.getByRole("button", { name: t("retry") }));
    await waitFor(() => expect(screen.queryByText("RAW_CLIENTS_READ_ERROR")).toBeNull());
    expect(
      ctx.requests
        .filter((r) => r.operationName === "CognitoUserPoolClients")
        .every((r) => r.variables.clusterId === cluster.id && r.variables.poolId === "POOL_LITERAL")
    ).toBe(true);
    expect(mutations(ctx.requests)).toHaveLength(0);
  });
  it("Cancel discards no server state and a new target cannot inherit a previous draft", async () => {
    const ctx = mount(locale);
    const t = tFor(locale);
    await edit(locale);
    await draft(locale);
    await userEvent.click(screen.getByRole("button", { name: t("cancel") }));
    expect(mutations(ctx.requests)).toHaveLength(0);
    await edit(locale);
    expect(screen.getByRole("textbox", { name: t("poolDomain") })).toHaveValue(
      saved.user_pool_domain
    );
    await draft(locale);
    ctx.result.rerender(<ClusterSettingsClient slug="next" />);
    await waitFor(() => expect(screen.queryByRole("button", { name: t("saveApply") })).toBeNull());
    await edit(locale);
    expect(screen.getByRole("textbox", { name: t("poolDomain") })).toHaveValue(
      "NEXT_DOMAIN_LITERAL"
    );
    expect(mutations(ctx.requests)).toHaveLength(0);
  });
  it("same-target cached read failure keeps the draft but confirmed source withdrawal clears it", async () => {
    const ctx = mount(locale);
    const t = tFor(locale);
    await edit(locale);
    await draft(locale);
    ctx.setMode("read-failed");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] }).catch(() => {});
    });
    expect(screen.getByRole("textbox", { name: t("poolDomain") })).toHaveValue(
      "REVIEWED_DOMAIN_LITERAL"
    );
    ctx.setMode("source-withdrawn");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] });
    });
    await waitFor(() => expect(screen.queryByRole("button", { name: t("saveApply") })).toBeNull());
    expect(screen.getByText(t("noConfig"))).toBeTruthy();
  });
  it("a confirmed missing cluster removes the draft, while initial failed reads offer actual retry", async () => {
    const ctx = mount(locale, "read-failed");
    expect(await screen.findByText("RAW_CLUSTER_READ_ERROR")).toBeTruthy();
    expect(mutations(ctx.requests)).toHaveLength(0);
    ctx.setMode("ok");
    await userEvent.click(
      screen.getByRole("button", { name: catalogs[locale].clusterSettings.source.retry })
    );
    await edit(locale);
    await draft(locale);
    ctx.setMode("read-null");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] });
    });
    expect(screen.queryByRole("button", { name: tFor(locale)("saveApply") })).toBeNull();
    expect(mutations(ctx.requests)).toHaveLength(0);
  });
  it("a pool without a known domain clears the previous domain and cannot silently enable it", async () => {
    const ctx = mount(locale);
    const t = tFor(locale);
    await userEvent.click(await screen.findByRole("button", { name: t("edit") }));
    const pool = screen.getByRole("combobox", { name: t("poolArn") });
    await userEvent.click(pool);
    await userEvent.clear(pool);
    await userEvent.type(pool, "NO_DOMAIN_POOL");
    await userEvent.click(await screen.findByText("NO_DOMAIN_NAME_LITERAL"));
    expect(screen.getByRole("textbox", { name: t("poolDomain") })).toHaveValue("");
    expect(screen.getByRole("combobox", { name: t("clientId") })).toHaveValue("");
    await userEvent.click(screen.getByRole("button", { name: t("saveApply") }));
    expect(state.error).toHaveBeenCalledWith(t("required"));
    expect(mutations(ctx.requests)).toHaveLength(0);
    await waitFor(() =>
      expect(
        ctx.requests.some(
          (r) =>
            r.operationName === "CognitoUserPoolClients" && r.variables.poolId === "NO_DOMAIN_POOL"
        )
      ).toBe(true)
    );
  });
  it("the existing parent permission fence prevents writes without inventing new authority", async () => {
    state.allowed = false;
    const ctx = mount(locale);
    const t = tFor(locale);
    expect(await screen.findByRole("button", { name: t("edit") })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: t("edit") }));
    expect(ctx.requests.map((r) => r.operationName)).toEqual(["GetCluster"]);
  });
});

it("a late committed old-cluster save never reconciles a new target or closes its new review", async () => {
  const ctx = mount("en", "deferred");
  const t = tFor("en");
  await edit("en");
  await draft("en");
  await userEvent.click(screen.getByRole("button", { name: t("saveApply") }));
  await waitFor(() => expect(ctx.waiting()).toBe(true));
  ctx.result.rerender(<ClusterSettingsClient slug="next" />);
  await waitFor(() => expect(screen.queryByRole("button", { name: t("saveApply") })).toBeNull());
  await edit("en");
  const nextDraft = await draft("en");
  await act(async () => ctx.release());
  await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("saved")));
  expect(state.warning).toHaveBeenCalledWith(t("savedRolloutUnconfirmed"));
  expect(mutations(ctx.requests)).toHaveLength(1);
  expect(nextDraft).toHaveValue("REVIEWED_DOMAIN_LITERAL");
  expect(screen.getByRole("button", { name: t("saveApply") })).toBeEnabled();
});
it("source withdrawal and restoration cannot revive a pending old review's reconciliation", async () => {
  const ctx = mount("en", "deferred");
  const t = tFor("en");
  await edit("en");
  await draft("en");
  await userEvent.click(screen.getByRole("button", { name: t("saveApply") }));
  await waitFor(() => expect(ctx.waiting()).toBe(true));
  ctx.setMode("source-withdrawn");
  await act(async () => {
    await ctx.client.refetchQueries({ include: ["GetCluster"] });
  });
  ctx.setMode("ok");
  await act(async () => {
    await ctx.client.refetchQueries({ include: ["GetCluster"] });
  });
  await act(async () => ctx.release());
  await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("saved")));
  expect(state.warning).toHaveBeenCalledWith(t("savedRolloutUnconfirmed"));
  expect(mutations(ctx.requests)).toHaveLength(1);
});
function args(nodes: MessageFormatElement[]): string[] {
  return nodes
    .flatMap((n): string[] => {
      if (n.type === 0 || n.type === 7) return [];
      if (n.type === 8) return [`tag:${n.value}`, ...args(n.children)];
      if (n.type === 5 || n.type === 6)
        return [`${n.type}:${n.value}`, ...Object.values(n.options).flatMap((o) => args(o.value))];
      return [`${n.type}:${n.value}`];
    })
    .sort();
}
describe.each(locales)("Cognito request-local copy in %s", (locale) => {
  it("preserves ICU shapes, literal identifiers and real translations", () => {
    const canonical = catalogs.en.clusterSettings.ingressAuth,
      translated = catalogs[locale].clusterSettings.ingressAuth,
      t = tFor(locale);
    expect(Object.keys(translated)).toEqual(Object.keys(canonical));
    for (const [key, message] of Object.entries(canonical)) {
      expect(args(parseMessage(translated[key]))).toEqual(args(parseMessage(message as string)));
      if (locale !== "en" && !(locale === "de" && key === "domain"))
        expect(translated[key]).not.toBe(message);
      expect(
        t(key, {
          provider: "RAW_PROVIDER_LITERAL",
          value: "RAW_ARN_LITERAL",
          reason: "RAW_SERVER_LITERAL",
          count: 1234,
        })
      ).not.toContain("{count}");
    }
    const result = render(
      <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
        <IngressAuthView {...INGRESS_AUTH} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(INGRESS_AUTH.existing!.user_pool_arn)).toBeTruthy();
    expect(screen.getByText(INGRESS_AUTH.existing!.user_pool_client_id)).toBeTruthy();
    expect(screen.getByText(INGRESS_AUTH.existing!.user_pool_domain)).toBeTruthy();
    result.rerender(
      <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
        <IngressAuthView {...INGRESS_AUTH} providerPluginSlug="RAW_PROVIDER_LITERAL" />
      </NextIntlClientProvider>
    );
    expect(
      screen.getByText(t("providerUnsupported", { provider: "RAW_PROVIDER_LITERAL" }))
    ).toBeTruthy();
  });
  it("locale changes preserve the same literal source draft while source replacement resets it", async () => {
    const view = (language: string, sourceKey: string) => (
      <NextIntlClientProvider locale={language} messages={catalogs[language]}>
        <IngressAuthView {...INGRESS_AUTH} editing sourceKey={sourceKey} />
      </NextIntlClientProvider>
    );
    const result = render(view("en", "SAME_SOURCE"));
    const domain = screen.getByRole("textbox", { name: tFor("en")("poolDomain") });
    await userEvent.clear(domain);
    await userEvent.type(domain, "RAW_REVIEWED_DOMAIN_LITERAL");
    result.rerender(view(locale, "SAME_SOURCE"));
    expect(screen.getByRole("textbox", { name: tFor(locale)("poolDomain") })).toHaveValue(
      "RAW_REVIEWED_DOMAIN_LITERAL"
    );
    expect(screen.getByRole("button", { name: tFor(locale)("saveApply") })).toBeEnabled();
    result.rerender(view(locale, "NEW_SOURCE"));
    expect(screen.getByRole("textbox", { name: tFor(locale)("poolDomain") })).toHaveValue(
      INGRESS_AUTH.existing!.user_pool_domain
    );
  });
  it.each(["gcp", "azure", "k8s_native"])(
    "unsupported %s metadata never renders live auth controls",
    (provider) => {
      render(
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
          <IngressAuthView {...INGRESS_AUTH} providerPluginSlug={provider} isAws={false} />
        </NextIntlClientProvider>
      );
      expect(screen.queryByRole("switch")).toBeNull();
      expect(screen.queryByRole("button")).toBeNull();
      expect(
        screen.getByText(
          tFor(locale)(provider === "k8s_native" ? "nativeUnsupported" : provider + "Unsupported")
        )
      ).toBeTruthy();
    }
  );
  it("hydrates saved observations without a request-locale mismatch or active-gate claim", async () => {
    const errors = vi.fn(),
      container = document.createElement("div");
    document.body.appendChild(container);
    const tree = (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-09-30T12:00:00Z")}
        timeZone="America/Costa_Rica"
      >
        <IngressAuthView {...INGRESS_AUTH} />
      </NextIntlClientProvider>
    );
    container.innerHTML = renderToString(tree);
    const before = container.textContent;
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, tree, { onRecoverableError: errors });
      });
      expect(errors).not.toHaveBeenCalled();
      expect(container.textContent).toBe(before);
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });
});
