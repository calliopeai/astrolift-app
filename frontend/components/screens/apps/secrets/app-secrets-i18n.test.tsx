import { readFileSync } from "node:fs";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useLocalListState } from "@/components/list/use-list-state";
import { SecretHistoryPanelView } from "./SecretHistoryPanel";
import { SecretsScreen } from "./SecretsScreen";
import { SECRETS_SCREEN, HISTORY } from "./app-secrets-tokens.fixtures";
import {
  APP_SECRETS_LIST,
  APP_SECRET_BUNDLES_LIST,
  selectSecrets,
  selectBundles,
} from "./secrets-list";
import { useAppSecrets } from "./use-app-secrets";

const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toasts }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/apps/storefront/secrets",
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));
vi.mock("@/components/PageShell", () => ({
  PageShell: ({
    title,
    children,
    actions,
  }: {
    title: ReactNode;
    children: ReactNode;
    actions: ReactNode;
  }) => (
    <div>
      <h1>{title}</h1>
      {actions}
      {children}
    </div>
  ),
}));
const locales = ["en", "es", "fr", "de", "pt-BR", "ja", "ko", "zh-Hans"];
function catalog(locale: string) {
  return JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
}
beforeEach(() => vi.clearAllMocks());

describe("localized app secret UI", () => {
  it.each(locales)(
    "%s submits the original preview scope and secret value from translated controls",
    async (locale) => {
      const messages = catalog(locale);
      const t = createTranslator({ locale, messages, namespace: "apps.secrets" });
      const submit = vi.fn(async () => true);
      function View() {
        const keysList = useLocalListState(APP_SECRETS_LIST);
        const bundlesList = useLocalListState(APP_SECRET_BUNDLES_LIST);
        const keys = selectSecrets(SECRETS_SCREEN.secrets, keysList.filters, keysList.state);
        const bundles = selectBundles(
          SECRETS_SCREEN.attachments,
          bundlesList.filters,
          bundlesList.state
        );
        return (
          <SecretsScreen
            {...SECRETS_SCREEN}
            keysList={keysList}
            keyRows={keys.rows}
            keyTotal={keys.totalCount}
            bundlesList={bundlesList}
            bundleRows={bundles.rows}
            bundleTotal={bundles.totalCount}
            tabs={null}
            pushToGitHub={null}
            renderHistory={() => null}
            sectionHref={() => "/apps/storefront/secrets"}
            onSetSecret={submit}
          />
        );
      }
      const view = render(
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          <View />
        </NextIntlClientProvider>
      );
      expect(screen.getByPlaceholderText(t("list.searchKeys"))).toBeInTheDocument();
      expect(screen.getAllByText(t("source.managed")).length).toBeGreaterThan(0);
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: t("newSecret") }));
      fireEvent.change(screen.getByLabelText(t("setSheet.keyLabel")), {
        target: { value: "api-key" },
      });
      fireEvent.change(screen.getByLabelText(t("setSheet.valueLabel")), {
        target: { value: "demo-value-preserved" },
      });
      await user.click(screen.getByLabelText(t("setSheet.scope")));
      await user.click(screen.getByRole("option", { name: t("setSheet.branchScope") }));
      fireEvent.change(screen.getByLabelText(t("setSheet.branchLabel")), {
        target: { value: " feature/i18n " },
      });
      await user.click(screen.getByRole("button", { name: t("setSheet.submit") }));
      await waitFor(() =>
        expect(submit).toHaveBeenCalledWith(
          "API_KEY",
          "demo-value-preserved",
          "preview:feature/i18n"
        )
      );
      view.unmount();
    }
  );

  it.each(locales)(
    "%s displays known audit actions and dates while preserving unknown IDs and actor identities",
    (locale) => {
      const messages = catalog(locale);
      const t = createTranslator({ locale, messages, namespace: "apps.secrets.history" });
      const entries = [
        {
          ...HISTORY.entries[0],
          action: "app.secret.rotate",
          timestamp: "2026-09-27T14:05:00Z",
          actor: { id: "actor", username: "same-identity@example.com" },
        },
        {
          ...HISTORY.entries[0],
          action: "constructor",
          timestamp: "2026-09-27T14:06:00Z",
          actor: null,
        },
      ];
      const view = render(
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          <SecretHistoryPanelView {...HISTORY} entries={entries} />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(t("actions.rotate"))).toHaveAttribute("title", "app.secret.rotate");
      expect(screen.getByText("constructor")).toBeInTheDocument();
      expect(screen.getByText(t("system"))).toBeInTheDocument();
      expect(screen.getByText("same-identity@example.com")).toBeInTheDocument();
      const stamp = new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "numeric",
        timeZoneName: "short",
        timeZone: "UTC",
      }).format(new Date(entries[0].timestamp));
      expect(screen.getByText(stamp, { exact: false })).toBeInTheDocument();
      view.unmount();
    }
  );
});

describe("localized secret mutations", () => {
  it.each(locales)(
    "%s retains scope/version, localizes conflict refresh and preserves server denials",
    async (locale) => {
      const messages = catalog(locale);
      const t = createTranslator({ locale, messages, namespace: "apps.secrets" });
      const shared = createTranslator({ locale, messages, namespace: "shared.versionMismatch" });
      const requests: Array<{ name: string; variables: Record<string, unknown> }> = [];
      let outcome = "ok";
      const client = new ApolloClient({
        cache: new InMemoryCache(),
        link: new ApolloLink(
          (operation) =>
            new Observable((observer) => {
              requests.push({
                name: operation.operationName ?? "",
                variables: operation.variables,
              });
              queueMicrotask(() => {
                const data =
                  operation.operationName === "SetAppSecret"
                    ? {
                        setAppSecret:
                          outcome === "ok"
                            ? {
                                ok: true,
                                errors: [],
                                data: {
                                  appSlug: "storefront",
                                  key: "API_KEY",
                                  rawManifestStaged: "",
                                },
                              }
                            : {
                                ok: false,
                                errors:
                                  outcome === "conflict"
                                    ? [{ code: "VERSION_MISMATCH", message: null }]
                                    : [
                                        {
                                          code: "PERMISSION_DENIED",
                                          message: "Owner grant required",
                                        },
                                      ],
                                data: null,
                              },
                      }
                    : {
                        astroliftApp: { id: "app", version: 9 },
                        astroliftEnvironments: [],
                        astroliftAppSecrets: [],
                        astroliftAppSecretBundleAttachments: [],
                        astroliftSecretChangeProposals: [],
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
      const hook = renderHook(() => useAppSecrets("storefront"), { wrapper });
      await waitFor(() => expect(hook.result.current.secretsLoading).toBe(false));
      await act(async () => {
        expect(
          await hook.result.current.onSetSecret("API_KEY", "same-value", "preview:feature/i18n")
        ).toBe(true);
      });
      expect(requests.find((r) => r.name === "SetAppSecret")?.variables).toEqual({
        input: {
          appSlug: "storefront",
          key: "API_KEY",
          value: "same-value",
          scope: "preview:feature/i18n",
          ifMatchVersion: 9,
        },
      });
      expect(toasts.success).toHaveBeenCalledWith(
        t("toasts.setScoped", {
          key: "API_KEY",
          scope: t("scope.previewBranch", { branch: "feature/i18n" }),
        })
      );
      outcome = "conflict";
      await act(async () => {
        expect(await hook.result.current.onSetSecret("API_KEY", "same-value", "production")).toBe(
          false
        );
      });
      expect(toasts.error).toHaveBeenCalledWith(
        shared("changed"),
        expect.objectContaining({ action: expect.objectContaining({ label: shared("refresh") }) })
      );
      const action = toasts.error.mock.calls.at(-1)?.[1]?.action;
      const reads = requests.filter((r) => r.name === "GetAppVersion").length;
      await act(async () => {
        action.onClick();
      });
      await waitFor(() =>
        expect(requests.filter((r) => r.name === "GetAppVersion").length).toBe(reads + 1)
      );
      outcome = "deny";
      await act(async () => {
        expect(await hook.result.current.onSetSecret("API_KEY", "same-value", "production")).toBe(
          false
        );
      });
      expect(toasts.error).toHaveBeenLastCalledWith("Owner grant required");
      hook.unmount();
      client.stop();
    }
  );
});
