import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClusterSettingsClient } from "@/app/(app)/clusters/[slug]/settings/cluster-settings-client";
import { locales } from "@/i18n/config";
import { CentralAuthView } from "./CentralAuth";
import { CLUSTER, CENTRAL_AUTH } from "./fixtures";
import type { CentralAuthDraft } from "./types";

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
  useSearchParams: () => new URLSearchParams("section=central-auth"),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", locale + ".json"), "utf8")),
  ])
);
function tFor(locale: string, namespace = "clusterSettings.centralAuth") {
  return createTranslator({ locale, messages: catalogs[locale], namespace });
}
const publicConfig = {
  discovery_url: "https://LITERAL_ISSUER.test.invalid/.well-known/openid-configuration",
  client_id: "LITERAL_CLIENT_ID",
  auth_proxy_host: "auth.LITERAL_HOST.test.invalid",
  jwks_uri: "https://LITERAL_JWKS.test.invalid/current",
  logout_url: "https://LITERAL_ISSUER.test.invalid/logout",
  upstream_connector: "LITERAL_CONNECTOR",
  client_secret_set: true,
  cookie_secret_set: false,
  gateway_secret_set: false,
};
const cluster = {
  ...CLUSTER,
  __typename: "AstroliftTenantCluster",
  oidcAuthConfig: publicConfig,
  organizationSlug: "LITERAL_ORG",
  region: "LITERAL_REGION",
  isActive: true,
  managedAt: null,
  lastHeartbeatAt: null,
  heartbeatStatus: "UNKNOWN",
  heartbeatAgeSeconds: null,
  createdByUsername: "LITERAL_ACTOR",
  lastBootstrapRun: null,
};
type Mode =
  | "accepted"
  | "refused"
  | "fallback"
  | "transport"
  | "refresh-failed"
  | "refused-refresh-failed"
  | "read-failed"
  | "read-null"
  | "source-withdrawn"
  | "next-target"
  | "deferred-write";
type Request = { operationName: string; variables: Record<string, unknown> };
function context(locale: string, initialMode: Mode = "accepted", initialConfigured = true) {
  let mode = initialMode;
  const requests: Request[] = [];
  let reads = 0;
  let savedConfig: typeof publicConfig | null = initialConfigured ? publicConfig : null;
  let release: (() => void) | undefined;
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const request = JSON.parse(String(init?.body)) as Request;
    requests.push(request);
    let data;
    if (request.operationName === "GetCluster") {
      reads++;
      if (
        mode === "read-failed" ||
        (reads > 1 && ["refresh-failed", "refused-refresh-failed"].includes(mode))
      )
        throw new Error("RAW_CLUSTER_READ_ERROR");
      data = {
        astroliftCluster:
          mode === "read-null"
            ? null
            : {
                ...cluster,
                id: request.variables.slug === "next" ? "NEXT_CLUSTER_ID" : cluster.id,
                slug: request.variables.slug === "next" ? "next" : cluster.slug,
                oidcAuthConfig:
                  mode === "source-withdrawn"
                    ? null
                    : request.variables.slug === "next"
                      ? {
                          ...publicConfig,
                          client_id: "NEXT_CLIENT_ID",
                          auth_proxy_host: "auth.next.test.invalid",
                        }
                      : savedConfig,
              },
      };
    } else if (request.operationName === "UpdateTenantCluster") {
      if (mode === "deferred-write")
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (mode === "transport") throw new Error("RAW_MUTATION_TRANSPORT_ERROR");
      const ok = !["refused", "fallback", "refused-refresh-failed"].includes(mode);
      const input = request.variables.input as {
        id: string;
        oidcAuthConfig: Record<string, string>;
      };
      if (ok) {
        const { client_secret, ...publicFields } = input.oidcAuthConfig;
        savedConfig = {
          ...publicConfig,
          ...publicFields,
          client_secret_set: !!client_secret || publicConfig.client_secret_set,
        };
        if (!publicFields.jwks_uri) delete (savedConfig as Partial<typeof publicConfig>).jwks_uri;
      }
      data = {
        updateTenantCluster: {
          ok,
          errors:
            ok || mode === "fallback"
              ? []
              : [
                  {
                    code: "PERMISSION_DENIED",
                    message: "RAW_SERVER_REFUSAL",
                    field: "oidcAuthConfig",
                  },
                ],
          data: ok ? { ...cluster, oidcAuthConfig: savedConfig } : null,
        },
      };
    } else throw new Error("Unexpected operation: " + request.operationName);
    return new Response(JSON.stringify({ data }), {
      headers: { "Content-Type": "application/json" },
    });
  });
  const client = new ApolloClient({
    link: new HttpLink({ uri: "https://test.invalid/graphql/", fetch: fetcher as typeof fetch }),
    cache: new InMemoryCache(),
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-09-30T16:00:00Z")}
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
    release: () => release?.(),
    waiting: () => !!release,
  };
}
async function openEdit(locale: string) {
  await userEvent.click(await screen.findByRole("button", { name: tFor(locale)("edit") }));
}
async function draft(locale: string, secret = "TEST_ONLY_TYPED_SECRET") {
  const t = tFor(locale);
  const host = screen.getByLabelText(t("authHost"));
  await userEvent.clear(host);
  await userEvent.type(host, "auth.REVIEWED_HOST.test.invalid");
  if (secret) await userEvent.type(screen.getByLabelText(t("clientSecret")), secret);
  return { host, secret: screen.getByLabelText(t("clientSecret")) };
}
async function save(locale: string, keyboard = false) {
  const button = screen.getByRole("button", { name: tFor(locale, "shared.settings")("save") });
  if (keyboard) {
    button.focus();
    await userEvent.keyboard("{Enter}");
  } else await userEvent.click(button);
}
beforeEach(() => {
  vi.clearAllMocks();
  state.allowed = true;
});

describe.each(locales)("Connected central OIDC auth in %s", (locale) => {
  const t = tFor(locale),
    shared = tFor(locale, "shared.settings"),
    source = tFor(locale, "clusterSettings.source");
  it("saves the exact reviewed literal config with keyboard, retains unedited metadata and clears typed secret after acceptance", async () => {
    const ctx = context(locale);
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    await openEdit(locale);
    await draft(locale);
    await save(locale, true);
    await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("saved")));
    const mutation = ctx.requests.find((r) => r.operationName === "UpdateTenantCluster")!;
    expect(mutation.variables).toEqual({
      input: {
        id: CLUSTER.id,
        oidcAuthConfig: {
          discovery_url: publicConfig.discovery_url,
          client_id: publicConfig.client_id,
          auth_proxy_host: "auth.REVIEWED_HOST.test.invalid",
          jwks_uri: publicConfig.jwks_uri,
          logout_url: publicConfig.logout_url,
          upstream_connector: publicConfig.upstream_connector,
          client_secret: "TEST_ONLY_TYPED_SECRET",
        },
      },
    });
    expect(
      ctx.requests.filter((r) => r.operationName === "GetCluster").map((r) => r.variables)
    ).toEqual([{ slug: CLUSTER.slug }, { slug: CLUSTER.slug }]);
    expect(screen.queryByLabelText(t("clientSecret"))).toBeNull();
    await openEdit(locale);
    expect(screen.getByLabelText(t("clientSecret"))).toHaveValue("");
    expect(state.error).not.toHaveBeenCalled();
    expect(state.warning).not.toHaveBeenCalled();
  });
  it("a blank secret is omitted, optional JWKS is removed, and public extras remain literal", async () => {
    const ctx = context(locale);
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    await openEdit(locale);
    await draft(locale, "");
    await userEvent.clear(screen.getByLabelText(t("jwksOptional")));
    await save(locale);
    await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("saved")));
    const config = (
      ctx.requests.find((r) => r.operationName === "UpdateTenantCluster")?.variables.input as {
        oidcAuthConfig: Record<string, string>;
      }
    ).oidcAuthConfig;
    expect(config).not.toHaveProperty("client_secret");
    expect(config).not.toHaveProperty("jwks_uri");
    expect(Object.keys(config).some((key) => key.endsWith("_set"))).toBe(false);
    expect(config.logout_url).toBe(publicConfig.logout_url);
    expect(config.upstream_connector).toBe(publicConfig.upstream_connector);
  });
  it.each(["refused", "refused-refresh-failed", "fallback", "transport"] as const)(
    "%s preserves the reviewed draft and raw outcome without a read or acceptance warning",
    async (mode) => {
      const ctx = context(locale, mode);
      render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
      await openEdit(locale);
      await draft(locale);
      await save(locale);
      await waitFor(() =>
        expect(state.error).toHaveBeenCalledWith(
          mode === "transport"
            ? "RAW_MUTATION_TRANSPORT_ERROR"
            : mode === "fallback"
              ? t("saveFailed")
              : "RAW_SERVER_REFUSAL"
        )
      );
      expect(screen.getByLabelText(t("clientSecret"))).toHaveValue("TEST_ONLY_TYPED_SECRET");
      expect(screen.getByLabelText(t("authHost"))).toHaveValue("auth.REVIEWED_HOST.test.invalid");
      expect(ctx.requests.filter((r) => r.operationName === "GetCluster")).toHaveLength(1);
      expect(state.success).not.toHaveBeenCalled();
      expect(state.warning).not.toHaveBeenCalled();
    }
  );
  it("a committed update survives failed refresh, closes the editor, clears its typed secret and exposes the real read retry", async () => {
    const ctx = context(locale, "refresh-failed");
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    await openEdit(locale);
    await draft(locale);
    await save(locale);
    await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("saved")));
    expect(state.warning).toHaveBeenCalledWith(t("refreshWarning"));
    expect(state.error).not.toHaveBeenCalled();
    expect(screen.queryByLabelText(t("clientSecret"))).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("RAW_CLUSTER_READ_ERROR");
    ctx.setMode("accepted");
    await userEvent.click(screen.getByRole("button", { name: source("retry") }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    await openEdit(locale);
    expect(screen.getByLabelText(t("clientSecret"))).toHaveValue("");
  });
  it("Cancel discards typed secrets without writing or refreshing", async () => {
    const ctx = context(locale);
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    await openEdit(locale);
    await draft(locale);
    await userEvent.click(screen.getByRole("button", { name: shared("cancel") }));
    await openEdit(locale);
    expect(screen.getByLabelText(t("clientSecret"))).toHaveValue("");
    expect(screen.getByLabelText(t("authHost"))).toHaveValue(publicConfig.auth_proxy_host);
    expect(ctx.requests).toHaveLength(1);
  });
  it("clears reviewed draft on confirmed source withdrawal, with no fabricated healthy config or save", async () => {
    const ctx = context(locale);
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    await openEdit(locale);
    await draft(locale);
    ctx.setMode("source-withdrawn");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] });
    });
    expect(screen.getByText(t("notConfigured"))).toBeTruthy();
    expect(screen.queryByLabelText(t("clientSecret"))).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: t("setup") }));
    expect(screen.getByLabelText(t("clientSecret"))).toHaveValue("");
    expect(screen.getByLabelText(t("authHost"))).toHaveValue("");
    expect(ctx.requests.every((r) => r.operationName === "GetCluster")).toBe(true);
  });
  it("failed initial read admits no form, Retry uses actual unchanged variables, and a later confirmed null removes the editor", async () => {
    const ctx = context(locale, "read-failed");
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    expect(await screen.findByRole("alert")).toHaveTextContent("RAW_CLUSTER_READ_ERROR");
    expect(screen.queryByRole("button", { name: t("edit") })).toBeNull();
    ctx.setMode("accepted");
    await userEvent.click(screen.getByRole("button", { name: source("retry") }));
    await openEdit(locale);
    await draft(locale);
    ctx.setMode("read-null");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] });
    });
    expect(screen.queryByLabelText(t("clientSecret"))).toBeNull();
    expect(screen.getByText(source("notFound", { slug: CLUSTER.slug }))).toBeTruthy();
    expect(ctx.requests.every((r) => r.operationName === "GetCluster")).toBe(true);
  });
  it("SSR and hydration preserve literal provider callback/issuer metadata and the actual secret-set flags", async () => {
    const errors: unknown[] = [];
    const element = (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-09-30T16:00:00Z")}
        timeZone="Asia/Tokyo"
      >
        <CentralAuthView {...CENTRAL_AUTH} view={publicConfig} />
      </NextIntlClientProvider>
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(element);
    document.body.append(container);
    const html = container.innerHTML;
    let root: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, element, { onRecoverableError: (error) => errors.push(error) });
    });
    expect(container.textContent).toContain("https://<auth host>/oauth2/callback");
    expect(container.textContent).toContain(publicConfig.discovery_url);
    expect(container.textContent).toContain(publicConfig.client_id);
    expect(container.textContent).not.toContain("TEST_ONLY_TYPED_SECRET");
    expect(container.textContent).toContain(
      t("secretBadge", { label: t("clientSecret"), state: t("secretSet") })
    );
    expect(errors).toEqual([]);
    expect(container.innerHTML).toBe(html);
    await act(async () => root!.unmount());
    container.remove();
  });
});

it("an already cached next cluster resets the actual card without retaining the old credential/draft", async () => {
  const ctx = context("de");
  ctx.client.writeQuery({
    query: (await import("@/graphql/clusters/clusters.queries")).GET_CLUSTER,
    variables: { slug: "next" },
    data: {
      astroliftCluster: {
        ...cluster,
        id: "NEXT_CLUSTER_ID",
        slug: "next",
        oidcAuthConfig: {
          ...publicConfig,
          client_id: "NEXT_CLIENT_ID",
          auth_proxy_host: "auth.next.test.invalid",
        },
      },
    },
  });
  const view = render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
  await openEdit("de");
  await draft("de");
  view.rerender(<ClusterSettingsClient slug="next" />);
  expect(screen.queryByLabelText(tFor("de")("clientSecret"))).toBeNull();
  await openEdit("de");
  expect(screen.getByLabelText(tFor("de")("clientSecret"))).toHaveValue("");
  expect(screen.getByLabelText(tFor("de")("clientId"))).toHaveValue("NEXT_CLIENT_ID");
  expect(ctx.requests.filter((r) => r.operationName === "UpdateTenantCluster")).toHaveLength(0);
});
it("a late prior-target save cannot close or clear a new target's draft", async () => {
  let resolveSave: ((ok: boolean) => void) | undefined;
  const onSave = vi.fn(
    () =>
      new Promise<boolean>((resolve) => {
        resolveSave = resolve;
      })
  );
  const view = (id: string) => (
    <NextIntlClientProvider locale="es" messages={catalogs.es}>
      <CentralAuthView {...CENTRAL_AUTH} clusterId={id} onSave={onSave} />
    </NextIntlClientProvider>
  );
  const result = render(view("first"));
  await openEdit("es");
  await draft("es");
  await save("es");
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  result.rerender(view("second"));
  await openEdit("es");
  await draft("es", "TEST_ONLY_NEW_TARGET_SECRET");
  await act(async () => resolveSave?.(true));
  expect(screen.getByLabelText(tFor("es")("clientSecret"))).toHaveValue(
    "TEST_ONLY_NEW_TARGET_SECRET"
  );
  expect(screen.getByLabelText(tFor("es")("authHost"))).toHaveValue(
    "auth.REVIEWED_HOST.test.invalid"
  );
});
it("the existing permission withdrawal clears the editor, disables reopening, and sends no write", async () => {
  const ctx = context("fr");
  const result = render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
  await openEdit("fr");
  await draft("fr");
  state.allowed = false;
  result.rerender(<ClusterSettingsClient slug={CLUSTER.slug} />);
  expect(screen.queryByLabelText(tFor("fr")("clientSecret"))).toBeNull();
  const edit = screen.getByRole("button", { name: tFor("fr")("edit") });
  expect(edit).toBeDisabled();
  await userEvent.click(edit);
  expect(screen.queryByLabelText(tFor("fr")("clientSecret"))).toBeNull();
  expect(ctx.requests.filter((r) => r.operationName === "UpdateTenantCluster")).toHaveLength(0);
  state.allowed = true;
  result.rerender(<ClusterSettingsClient slug={CLUSTER.slug} />);
  await openEdit("fr");
  expect(screen.getByLabelText(tFor("fr")("clientSecret"))).toHaveValue("");
});
it("an actual delayed prior-target HttpLink write cannot close the newly opened target editor or carry its secret into it", async () => {
  const ctx = context("de", "deferred-write");
  const result = render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
  await openEdit("de");
  await draft("de");
  await save("de");
  await waitFor(() => expect(ctx.waiting()).toBe(true));
  result.rerender(<ClusterSettingsClient slug="next" />);
  await openEdit("de");
  expect(screen.getByLabelText(tFor("de")("clientSecret"))).toHaveValue("");
  ctx.setMode("accepted");
  await act(async () => ctx.release());
  await waitFor(() => expect(state.success).toHaveBeenCalledWith(tFor("de")("saved")));
  expect(screen.getByLabelText(tFor("de")("clientSecret"))).toHaveValue("");
  expect(screen.getByLabelText(tFor("de")("clientId"))).toHaveValue("NEXT_CLIENT_ID");
  const writes = ctx.requests.filter((r) => r.operationName === "UpdateTenantCluster");
  expect(writes).toHaveLength(1);
  expect((writes[0].variables.input as { id: string }).id).toBe(CLUSTER.id);
  expect(ctx.requests.filter((r) => r.operationName === "GetCluster").at(-1)?.variables).toEqual({
    slug: "next",
  });
});
it.each(locales)(
  "%s keeps an omitted secret-presence flag unconfirmed instead of claiming unset",
  (locale) => {
    const t = tFor(locale);
    render(
      <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
        <CentralAuthView
          {...CENTRAL_AUTH}
          view={{ ...publicConfig, client_secret_set: undefined }}
        />
      </NextIntlClientProvider>
    );
    expect(
      screen.getByText(t("secretBadge", { label: t("clientSecret"), state: t("secretUnknown") }))
    ).toBeTruthy();
  }
);
it("locale change retains same-target reviewed fields and technical input IDs, then refused save retains that draft", async () => {
  const onSave = vi.fn(async (_draft: CentralAuthDraft) => false);
  const view = (locale: string) => (
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
      <CentralAuthView {...CENTRAL_AUTH} onSave={onSave} />
    </NextIntlClientProvider>
  );
  const result = render(view("en"));
  await openEdit("en");
  await draft("en");
  result.rerender(view("ja"));
  expect(screen.getByLabelText(tFor("ja")("clientSecret"))).toHaveValue("TEST_ONLY_TYPED_SECRET");
  expect(screen.getByLabelText(tFor("ja")("clientSecret")).id).toBe("oidc-client-secret");
  await save("ja");
  expect(screen.getByLabelText(tFor("ja")("clientSecret"))).toHaveValue("TEST_ONLY_TYPED_SECRET");
  expect(onSave.mock.calls[0][0].authHost).toBe("auth.REVIEWED_HOST.test.invalid");
});
function shape(nodes: MessageFormatElement[]): string[] {
  return nodes
    .flatMap((node) => {
      if (node.type === 0) return [];
      if (node.type === 8) return ["tag:" + node.value, ...shape(node.children)];
      if (node.type === 5 || node.type === 6)
        return [
          "arg:" + node.value,
          "options:" + Object.keys(node.options).sort().join(","),
          ...Object.values(node.options).flatMap((option) => shape(option.value)),
        ];
      if (node.type === 7) return ["pound"];
      return ["arg:" + node.value + ":" + node.type];
    })
    .sort();
}
it.each(locales)(
  "%s has genuine complete central-auth copy with exact ICU/rich arguments",
  (locale) => {
    const base = catalogs.en.clusterSettings.centralAuth as Record<string, string>,
      translated = catalogs[locale].clusterSettings.centralAuth as Record<string, string>;
    expect(Object.keys(translated)).toEqual(Object.keys(base));
    for (const [key, message] of Object.entries(translated)) {
      expect(shape(parse(message))).toEqual(shape(parse(base[key])));
      if (locale !== "en" && key !== "secretBadge") expect(message).not.toBe(base[key]);
    }
  }
);
