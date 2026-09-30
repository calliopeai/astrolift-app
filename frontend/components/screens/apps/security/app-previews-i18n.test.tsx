import { readFileSync } from "node:fs";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { ReactNode } from "react";
import { useAppPreviews } from "./use-app-previews";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { beforeEach, expect, it, vi } from "vitest";
import { useLocalListState } from "@/components/list/use-list-state";
import { APP_PREVIEWS_LIST } from "../deployments/app-deployments-list";
import { AppPreviewsScreen } from "./AppPreviewsScreen";
import { PREVIEWS } from "./app-security-previews.fixtures";
const authority = vi.hoisted(() => ({ granted: true }));
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toasts }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => authority.granted, loading: false }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/apps/storefront/deployments",
  useRouter: () => ({ replace: vi.fn() }),
}));
const locales = ["en", "es", "fr", "de", "pt-BR", "ja", "ko", "zh-Hans"];
beforeEach(() => {
  authority.granted = true;
  vi.clearAllMocks();
});
it.each(locales)(
  "%s renders preview amounts, TTL and raw branch submissions with translated controls",
  async (locale) => {
    const messages = JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
    const t = createTranslator({ locale, messages, namespace: "apps.previews" });
    const create = vi.fn(async () => true);
    const row = {
      ...PREVIEWS.rows[0],
      branch: "feature/raw-branch",
      ttlUntil: new Date(Date.now() + (2 * 24 + 5) * 3600000 + 30000).toISOString(),
      lastDeployedAt: "2026-09-27T14:05:00Z",
      estimatedDailyCostUsd: null,
    };
    function View() {
      const list = useLocalListState(APP_PREVIEWS_LIST, { view: "previews" });
      return (
        <AppPreviewsScreen
          {...PREVIEWS}
          rows={[row]}
          list={list}
          configHref="/apps/storefront/config"
          onCreate={create}
          spend={{
            dailyTotal: 1234.5,
            monthlyProjection: 37035,
            liveCount: 3,
            priced: 1,
            unpriced: 2,
            approximate: 1,
          }}
        />
      );
    }
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <View />
      </NextIntlClientProvider>
    );
    expect(screen.getByPlaceholderText(t("list.search"))).toBeVisible();
    expect(screen.getByText("feature/raw-branch")).toBeVisible();
    expect(screen.getByText(t("countdown.daysHours", { days: 2, hours: 5 }))).toBeVisible();
    expect(screen.getByText(t("costUnavailable"))).toBeVisible();
    expect(screen.getByText(t("spend.priced", { priced: 1, total: 3 }))).toBeVisible();
    expect(
      screen.getByText(
        new Intl.NumberFormat(locale, { style: "currency", currency: "USD" })
          .format(1234.5)
          .replace(/\s+/g, " ")
      )
    ).toBeVisible();
    expect(screen.getByText(/SKU/)).toHaveTextContent(t("spend.overcount", { count: 1 }));
    expect(screen.getByTitle(row.status)).toHaveTextContent(t(`status.${row.status}`));
    fireEvent.click(screen.getByRole("button", { name: t("create.action") }));
    expect(screen.getByRole("heading", { name: t("create.title") })).toBeVisible();
    fireEvent.change(screen.getByLabelText(t("create.branch")), {
      target: { value: " feature/preserved " },
    });
    fireEvent.click(screen.getByRole("button", { name: t("create.action") }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("feature/preserved"));
    view.unmount();
  }
);
it("hides deploy actions when permission is absent and renders invalid TTL without an invented countdown", () => {
  authority.granted = false;
  const messages = JSON.parse(readFileSync("messages/es.json", "utf8"));
  const t = createTranslator({ locale: "es", messages, namespace: "apps.previews" });
  function View() {
    const list = useLocalListState(APP_PREVIEWS_LIST);
    return (
      <AppPreviewsScreen
        {...PREVIEWS}
        list={list}
        canDeploy={false}
        rows={[{ ...PREVIEWS.rows[0], ttlUntil: "invalid" }]}
        configHref="/apps/storefront/config"
      />
    );
  }
  const view = render(
    <NextIntlClientProvider locale="es" messages={messages} timeZone="UTC">
      <View />
    </NextIntlClientProvider>
  );
  expect(screen.queryByRole("button", { name: t("create.action") })).not.toBeInTheDocument();
  expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /More/ })).not.toBeInTheDocument();
  view.unmount();
});

it.each(locales)(
  "%s preserves actual preview mutation inputs and authoritative error text",
  async (locale) => {
    const messages = JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
    const t = createTranslator({ locale, messages, namespace: "apps.previews" });
    const requests: { name: string; variables: Record<string, unknown> }[] = [];
    let outcome: "success" | "missing" | "deny" = "success";
    const client = new ApolloClient({
      cache: new InMemoryCache(),
      link: new ApolloLink(
        (operation) =>
          new Observable((observer) => {
            requests.push({ name: operation.operationName ?? "", variables: operation.variables });
            queueMicrotask(() => {
              let data: Record<string, unknown>;
              if (operation.operationName === "GetApp") data = { astroliftApp: PREVIEWS.app };
              else if (operation.operationName === "ListPreviewEnvironments")
                data = { astroliftPreviewEnvironments: [] };
              else if (operation.operationName === "ListPreviewEnvironmentsPage")
                data = {
                  astroliftPreviewEnvironmentsPage: { items: [], nextCursor: null, totalCount: 0 },
                };
              else
                data = {
                  createPreviewEnvironment:
                    outcome === "success"
                      ? { ok: true, errors: [] }
                      : {
                          ok: false,
                          errors: [
                            {
                              code: "PERMISSION_DENIED",
                              message: outcome === "deny" ? "Owner grant required" : null,
                            },
                          ],
                        },
                };
              observer.next({ data });
              observer.complete();
            });
          })
      ),
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ApolloProvider client={client}>
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          {children}
        </NextIntlClientProvider>
      </ApolloProvider>
    );
    const hook = renderHook(() => useAppPreviews("storefront"), { wrapper });
    await waitFor(() => expect(hook.result.current.app).not.toBeNull());
    await act(async () =>
      expect(await hook.result.current.onCreate("feature/raw-branch")).toBe(true)
    );
    expect(requests.find((r) => r.name === "CreatePreviewEnvironment")?.variables).toEqual({
      input: { appSlug: PREVIEWS.app!.slug, branch: "feature/raw-branch" },
    });
    expect(toasts.success).toHaveBeenCalledWith(
      t("toasts.created", { branch: "feature/raw-branch" })
    );
    outcome = "missing";
    await act(async () =>
      expect(await hook.result.current.onCreate("feature/raw-branch")).toBe(false)
    );
    expect(toasts.error).toHaveBeenLastCalledWith(t("toasts.createFailed"));
    outcome = "deny";
    await act(async () =>
      expect(await hook.result.current.onCreate("feature/raw-branch")).toBe(false)
    );
    expect(toasts.error).toHaveBeenLastCalledWith("Owner grant required");
    hook.unmount();
    client.stop();
  }
);
