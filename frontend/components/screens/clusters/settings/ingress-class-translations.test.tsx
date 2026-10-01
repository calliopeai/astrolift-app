import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClusterSettingsClient } from "@/app/(app)/clusters/[slug]/settings/cluster-settings-client";
import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { locales } from "@/i18n/config";
import { IngressClassView } from "./CentralAuth";
import { CLUSTER, INGRESS_CLASS } from "./fixtures";

const feedback = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  allowed: true,
}));
vi.mock("sonner", () => ({ toast: feedback }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    granted: new Set(feedback.allowed ? ["cluster.update"] : []),
    loading: false,
    can: () => feedback.allowed,
  }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/clusters/prod-west/settings",
  useSearchParams: () => new URLSearchParams("section=ingress-class"),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", locale + ".json"), "utf8")),
  ])
);
function tFor(locale: string, namespace = "clusterSettings.ingressClass") {
  return createTranslator({ locale, messages: catalogs[locale], namespace });
}
const initialCluster = {
  ...CLUSTER,
  __typename: "AstroliftTenantCluster",
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
  | "deferred";
type Request = { operationName: string; variables: Record<string, unknown> };
function context(
  locale: string,
  initialMode: Mode = "accepted",
  patch: Record<string, unknown> = {}
) {
  let mode = initialMode,
    reads = 0,
    live = { ...initialCluster, ...patch };
  const requests: Request[] = [];
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
            : request.variables.slug === "next"
              ? { ...initialCluster, id: "NEXT_CLUSTER_ID", slug: "next", ingressClass: "nginx" }
              : mode === "source-withdrawn"
                ? { ...live, oidcAuthConfig: null }
                : live,
      };
    } else if (request.operationName === "UpdateTenantCluster") {
      if (mode === "deferred")
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (mode === "transport") throw new Error("RAW_TRANSPORT_ERROR");
      const input = request.variables.input as {
        id: string;
        ingressClass: string;
        syncManifests: boolean;
      };
      const ok = !["refused", "refused-refresh-failed", "fallback"].includes(mode);
      if (ok) live = { ...live, ingressClass: input.ingressClass };
      data = {
        updateTenantCluster: {
          ok,
          errors:
            ok || mode === "fallback"
              ? []
              : [{ code: "PRECONDITION", message: "RAW_SERVER_REFUSAL", field: "ingressClass" }],
          data: ok ? live : null,
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
async function select(locale: string, value: "envoy" | "alb" | "nginx" = "envoy") {
  const t = tFor(locale);
  await userEvent.click(await screen.findByRole("combobox", { name: t("title") }));
  await userEvent.click(screen.getByRole("option", { name: t(`classes.${value}.label`) }));
}
async function review(locale: string, sync = false) {
  const t = tFor(locale);
  await select(locale);
  if (sync) await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: t("change") }));
  return screen.getByRole("alertdialog");
}
async function confirm(locale: string, keyboard = false) {
  const button = within(screen.getByRole("alertdialog")).getByRole("button", {
    name: tFor(locale)("change"),
  });
  if (keyboard) {
    button.focus();
    await userEvent.keyboard("{Enter}");
  } else await userEvent.click(button);
}
beforeEach(() => {
  vi.clearAllMocks();
  feedback.allowed = true;
});
describe.each(locales)("Connected ingress class in %s", (locale) => {
  const t = tFor(locale),
    source = tFor(locale, "clusterSettings.source");
  it.each([false, true])(
    "review sync=%s sends only the exact class/id/opt-in payload and refreshes current variables",
    async (sync) => {
      const ctx = context(locale);
      render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
      const dialog = await review(locale, sync);
      expect(dialog).toHaveTextContent(t(sync ? "syncReview" : "nextDeployReview"));
      expect(dialog.querySelector("h2 span")?.textContent).toBe("envoy");
      await confirm(locale, true);
      await waitFor(() =>
        expect(feedback.success).toHaveBeenCalledWith(t("changed", { target: "envoy" }))
      );
      expect(
        ctx.requests.find((r) => r.operationName === "UpdateTenantCluster")?.variables
      ).toEqual({ input: { id: CLUSTER.id, ingressClass: "envoy", syncManifests: sync } });
      expect(
        ctx.requests.filter((r) => r.operationName === "GetCluster").map((r) => r.variables)
      ).toEqual([{ slug: CLUSTER.slug }, { slug: CLUSTER.slug }]);
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
      expect(feedback.error).not.toHaveBeenCalled();
      expect(feedback.warning).not.toHaveBeenCalled();
    }
  );
  it.each(["refused", "refused-refresh-failed", "transport", "fallback"] as const)(
    "%s retains confirmation and opt-in without refresh or acceptance",
    async (mode) => {
      const ctx = context(locale, mode);
      render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
      await review(locale, true);
      await confirm(locale);
      await waitFor(() =>
        expect(feedback.error).toHaveBeenCalledWith(
          mode === "transport"
            ? "RAW_TRANSPORT_ERROR"
            : mode === "fallback"
              ? t("changeFailed")
              : "RAW_SERVER_REFUSAL"
        )
      );
      expect(screen.getByRole("alertdialog")).toHaveTextContent(t("syncReview"));
      expect(document.querySelector("button[role=checkbox]")).toBeChecked();
      expect(ctx.requests.filter((r) => r.operationName === "GetCluster")).toHaveLength(1);
      expect(feedback.success).not.toHaveBeenCalled();
      expect(feedback.warning).not.toHaveBeenCalled();
    }
  );
  it("accepted change survives failed refresh and exposes the actual raw read/retry without reopening a refused dialog", async () => {
    const ctx = context(locale, "refresh-failed");
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    await review(locale);
    await confirm(locale);
    await waitFor(() =>
      expect(feedback.success).toHaveBeenCalledWith(t("changed", { target: "envoy" }))
    );
    expect(feedback.warning).toHaveBeenCalledWith(t("refreshWarning"));
    expect(feedback.error).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(screen.getByRole("alert")).toHaveTextContent("RAW_CLUSTER_READ_ERROR");
    ctx.setMode("accepted");
    await userEvent.click(screen.getByRole("button", { name: source("retry") }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByRole("combobox", { name: t("title") })).toHaveTextContent(
      t("classes.envoy.label")
    );
  });
  it("cancelled confirmation and cancelled selection cause no RPC/read and preserve literal existing class", async () => {
    const ctx = context(locale);
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    await review(locale, true);
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: tFor(locale, "shared.confirmation")("cancel"),
      })
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(screen.getByRole("checkbox")).toBeChecked();
    await userEvent.click(
      screen.getByRole("button", { name: tFor(locale, "shared.settings")("cancel") })
    );
    expect(screen.getByRole("combobox", { name: t("title") })).toHaveTextContent(
      t("classes.alb.label")
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(ctx.requests).toHaveLength(1);
  });
  it("withdrawn confirmed gate facts invalidate the open review without fabricating new authority", async () => {
    const ctx = context(locale);
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    await review(locale, true);
    ctx.setMode("source-withdrawn");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] });
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    await select(locale);
    expect(screen.getByRole("alert")).toHaveTextContent(t("missingCentral"));
    expect(screen.getByRole("alert")).toHaveTextContent(t("gateRefusal"));
    expect(ctx.requests.every((r) => r.operationName === "GetCluster")).toBe(true);
  });
  it("unknown future current class remains literal, with known choices translated and unchanged tokens", async () => {
    const ctx = context(locale, "accepted", { ingressClass: "FUTURE_PROVIDER_CLASS" });
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    expect(await screen.findByRole("combobox", { name: t("title") })).toHaveTextContent(
      "FUTURE_PROVIDER_CLASS"
    );
    await select(locale, "nginx");
    expect(screen.getByText(t("gateHint", { gate: t("classes.nginx.gate") }))).toBeTruthy();
    expect(ctx.requests.every((r) => r.operationName === "GetCluster")).toBe(true);
  });
  it("failed initial source has no review; Retry reads the same target, and confirmed null removes a pending selection", async () => {
    const ctx = context(locale, "read-failed");
    render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
    expect(await screen.findByRole("alert")).toHaveTextContent("RAW_CLUSTER_READ_ERROR");
    expect(screen.queryByRole("combobox", { name: t("title") })).toBeNull();
    ctx.setMode("accepted");
    await userEvent.click(screen.getByRole("button", { name: source("retry") }));
    await select(locale);
    ctx.setMode("read-null");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] });
    });
    expect(screen.queryByRole("combobox", { name: t("title") })).toBeNull();
    expect(screen.getByText(source("notFound", { slug: CLUSTER.slug }))).toBeTruthy();
    expect(ctx.requests.every((r) => r.operationName === "GetCluster")).toBe(true);
  });
  it("SSR and hydration preserve technical unknown class and request-local copy", async () => {
    const errors: unknown[] = [];
    const element = (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-09-30T16:00:00Z")}
        timeZone="Asia/Tokyo"
      >
        <IngressClassView {...INGRESS_CLASS} ingressClass="FUTURE_PROVIDER_CLASS" />
      </NextIntlClientProvider>
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(element);
    document.body.append(container);
    expect(container.querySelector("h2")?.textContent).toBe(t("title"));
    expect(container.textContent).toContain(t("description"));
    let root: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, element, { onRecoverableError: (error) => errors.push(error) });
    });
    try {
      expect(errors).toEqual([]);
      expect(container.querySelector("h2")?.textContent).toBe(t("title"));
      expect(container.textContent).toContain(t("description"));
      expect(within(container).getByRole("combobox", { name: t("title") })).toHaveTextContent(
        "FUTURE_PROVIDER_CLASS"
      );
    } finally {
      await act(async () => root!.unmount());
      container.remove();
    }
  });
});
it("an already-cached target change removes the prior confirmation and opt-in without any write", async () => {
  const ctx = context("de");
  ctx.client.writeQuery({
    query: GET_CLUSTER,
    variables: { slug: "next" },
    data: {
      astroliftCluster: {
        ...initialCluster,
        id: "NEXT_CLUSTER_ID",
        slug: "next",
        ingressClass: "nginx",
      },
    },
  });
  const result = render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
  await review("de", true);
  result.rerender(<ClusterSettingsClient slug="next" />);
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.getByRole("combobox", { name: tFor("de")("title") })).toHaveTextContent(
    tFor("de")("classes.nginx.label")
  );
  expect(ctx.requests.filter((r) => r.operationName === "UpdateTenantCluster")).toHaveLength(0);
});
it("a delayed accepted HTTP reply cannot move its old review onto a cached different cluster", async () => {
  const ctx = context("ko", "deferred");
  ctx.client.writeQuery({
    query: GET_CLUSTER,
    variables: { slug: "next" },
    data: {
      astroliftCluster: {
        ...initialCluster,
        id: "NEXT_CLUSTER_ID",
        slug: "next",
        ingressClass: "nginx",
      },
    },
  });
  const result = render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
  await review("ko", true);
  await confirm("ko");
  await waitFor(() => expect(ctx.waiting()).toBe(true));
  result.rerender(<ClusterSettingsClient slug="next" />);
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(screen.queryByRole("checkbox")).toBeNull();
  await act(async () => ctx.release());
  await waitFor(() =>
    expect(feedback.success).toHaveBeenCalledWith(tFor("ko")("changed", { target: "envoy" }))
  );
  expect(screen.getByRole("combobox", { name: tFor("ko")("title") })).toHaveTextContent(
    tFor("ko")("classes.nginx.label")
  );
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(
    ctx.requests.filter((r) => r.operationName === "UpdateTenantCluster").map((r) => r.variables)
  ).toEqual([{ input: { id: CLUSTER.id, ingressClass: "envoy", syncManifests: true } }]);
  expect(feedback.error).not.toHaveBeenCalled();
});
it("late old-target dialog completion cannot close the new target review", async () => {
  let finish: (() => void) | undefined;
  const onApply = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      })
  );
  const view = (id: string) => (
    <NextIntlClientProvider locale="es" messages={catalogs.es}>
      <IngressClassView {...INGRESS_CLASS} clusterId={id} onApply={onApply} />
    </NextIntlClientProvider>
  );
  const result = render(view("first"));
  await review("es", true);
  await confirm("es");
  await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
  result.rerender(view("second"));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  await review("es", false);
  await act(async () => finish?.());
  expect(screen.getByRole("alertdialog")).toHaveTextContent(tFor("es")("nextDeployReview"));
  expect(onApply).toHaveBeenCalledWith("envoy", true);
});
it("locale change preserves the current technical target and opt-in in an open refused review", async () => {
  const onApply = vi.fn(async () => {
    throw new Error("RAW_SERVER_REFUSAL");
  });
  const view = (locale: string) => (
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
      <IngressClassView {...INGRESS_CLASS} onApply={onApply} />
    </NextIntlClientProvider>
  );
  const result = render(view("en"));
  await review("en", true);
  result.rerender(view("ja"));
  expect(screen.getByRole("alertdialog")).toHaveTextContent(tFor("ja")("syncReview"));
  expect(screen.getByRole("alertdialog").querySelector("h2 span")?.textContent).toBe("envoy");
  await confirm("ja");
  await waitFor(() => expect(feedback.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL"));
  expect(onApply).toHaveBeenCalledWith("envoy", true);
  expect(screen.getByRole("alertdialog")).toBeTruthy();
});
it("existing permission withdrawal removes the review and disables class changes without a write", async () => {
  const ctx = context("fr");
  const result = render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
  await review("fr", true);
  feedback.allowed = false;
  result.rerender(<ClusterSettingsClient slug={CLUSTER.slug} />);
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(screen.getByRole("combobox", { name: tFor("fr")("title") })).toBeDisabled();
  expect(ctx.requests.filter((r) => r.operationName === "UpdateTenantCluster")).toHaveLength(0);
});
it("empty ALB configuration is an unconfigured advisory just as the server's empty-dictionary gate check", async () => {
  const ctx = context("en", "accepted", { ingressClass: "envoy", albAuthConfig: {} });
  render(<ClusterSettingsClient slug={CLUSTER.slug} />, { wrapper: ctx.Wrapper });
  await select("en", "alb");
  expect(screen.getByRole("alert")).toHaveTextContent(tFor("en")("missingAlb"));
  expect(ctx.requests.every((r) => r.operationName === "GetCluster")).toBe(true);
});
function flattened(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, v]) =>
      typeof v === "string"
        ? [[prefix + key, v]]
        : Object.entries(flattened(v as Record<string, unknown>, prefix + key + "."))
    )
  );
}
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
  "%s has genuine complete ingress-class copy with exact ICU/rich arguments",
  (locale) => {
    const base = flattened(catalogs.en.clusterSettings.ingressClass),
      translated = flattened(catalogs[locale].clusterSettings.ingressClass);
    expect(Object.keys(translated)).toEqual(Object.keys(base));
    for (const [key, message] of Object.entries(translated)) {
      expect(shape(parse(message))).toEqual(shape(parse(base[key])));
      if (locale !== "en") expect(message).not.toBe(base[key]);
    }
  }
);
