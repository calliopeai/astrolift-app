import { readFileSync } from "node:fs";
import path from "node:path";
import {
  ApolloClient,
  ApolloLink,
  InMemoryCache,
  Observable,
  type Operation,
} from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeSettingsClient } from "@/app/(app)/settings/home/home-settings-client";
import { BOTH, homeProps, NO_ACCESS } from "@/components/home/fixtures";
import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";
import { applyServerHomePrefs, parseHomePrefs, STORAGE_KEY } from "@/lib/home-prefs";
import { setUiPrefsStatus, type ServerUiPrefs } from "@/lib/ui-prefs-sync";
import { locales } from "@/i18n/config";
import { UiPreferencesBridge } from "@/providers/UiPreferencesBridge";
import { HomeLayoutSettings } from "./HomeLayoutSettings";

const state = vi.hoisted(() => ({
  modules: new Set(["apps", "agents"]),
  granted: new Set<string>(),
  error: vi.fn(),
  success: vi.fn(),
  appearance: vi.fn(),
  theme: vi.fn(),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "actual-org-id" }, loading: false }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useModules: () => ({
    loading: false,
    modules: state.modules,
    canView: (key: string) => state.modules.has(key),
  }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ loading: false, granted: state.granted }),
}));
vi.mock("sonner", () => ({ toast: { error: state.error, success: state.success } }));
vi.mock("@/providers/AppearanceProvider", () => ({
  useAppearance: () => ({ applyServerAppearance: state.appearance }),
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ setTheme: state.theme }) }));

const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
const server: ServerUiPrefs = {
  homeLayout: "apps",
  homeLayoutAsked: true,
  fleetView: "orbit",
  workflowView: "transit",
  appView: "auto",
  flowParticles: true,
  motion: "system",
  restrictedSettings: "show",
  restrictedSettingsChoice: null,
  restrictedSettingsOrgDefault: "show",
  appearance: {},
};
beforeEach(() => {
  state.modules = new Set(["apps", "agents"]);
  state.granted = new Set(ASTROLIFT_PERMISSIONS);
  state.error.mockClear();
  state.success.mockClear();
  applyServerHomePrefs(server);
  setUiPrefsStatus("local");
});
const tFor = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "home" });
const radio = (key: string) =>
  screen.getAllByRole("radio").find((value) => (value as HTMLInputElement).value === key)!;

function actualConsumer(
  locale: string,
  mode:
    | "ok"
    | "raw-refusal"
    | "missing-message"
    | "offline"
    | "read-failure"
    | "pending-read" = "ok"
) {
  const requests: Operation[] = [];
  let releaseRead = () => {};
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push(operation);
          if (operation.operationName === "MyUiPreferences") {
            if (mode === "read-failure") {
              observer.error(new Error("ORIGINAL_READ_UNAVAILABLE"));
              return;
            }
            const finish = () => {
              observer.next({
                data: {
                  astroliftMyUiPreferences: { __typename: "AstroliftUiPreferences", ...server },
                },
              });
              observer.complete();
            };
            if (mode === "pending-read") releaseRead = finish;
            else finish();
            return;
          }
          if (operation.operationName !== "UpdateMyUiPreferences")
            throw new Error("Unexpected preferences operation");
          if (mode === "offline") {
            observer.error(new Error("ORIGINAL_NETWORK_UNAVAILABLE"));
            return;
          }
          const refused = mode === "raw-refusal" || mode === "missing-message";
          observer.next({
            data: {
              updateMyUiPreferences: {
                ok: !refused,
                errors:
                  mode === "raw-refusal"
                    ? [
                        {
                          code: "DENIED",
                          field: null,
                          message: "RAW_SERVER_REFUSAL",
                        },
                      ]
                    : [],
                data: refused
                  ? null
                  : {
                      __typename: "AstroliftUiPreferences",
                      ...server,
                      ...operation.variables.input,
                    },
              },
            },
          });
          observer.complete();
        })
    ),
  });
  const intlErrors = vi.fn();
  const tree = (nextLocale: string) => (
    <NextIntlClientProvider
      locale={nextLocale}
      messages={catalogs[nextLocale]}
      timeZone="UTC"
      onError={intlErrors}
    >
      <ApolloProvider client={client}>
        <UiPreferencesBridge />
        <HomeSettingsClient />
      </ApolloProvider>
    </NextIntlClientProvider>
  );
  const view = render(tree(locale));
  return {
    ...view,
    requests,
    intlErrors,
    releaseRead: () => releaseRead(),
    changeLocale: (nextLocale: string) => view.rerender(tree(nextLocale)),
    stop: () => {
      view.unmount();
      client.stop();
    },
  };
}

describe("Home layout settings locale and actual persistence", () => {
  it.each(locales)(
    "%s translates available choices and reset without altering actual identities",
    (locale) => {
      const props = homeProps(BOTH, "apps"),
        change = vi.fn(),
        reset = vi.fn(),
        errors = vi.fn(),
        t = tFor(locale);
      const view = render(
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={errors}>
          <HomeLayoutSettings
            {...props}
            savedLayout="apps"
            onLayoutChange={change}
            onResetLayout={reset}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("group", { name: t("layoutLegend") })).toBeInTheDocument();
      expect(screen.getByText(t("layoutSettings.description"))).toBeInTheDocument();
      fireEvent.click(radio("agents"));
      expect(change).toHaveBeenCalledExactlyOnceWith("agents");
      fireEvent.click(
        screen.getByRole("button", {
          name: t("layoutSettings.resetDefault", { layout: t("layouts.builder.title") }),
        })
      );
      expect(reset).toHaveBeenCalledOnce();
      view.rerender(
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={errors}>
          <HomeLayoutSettings
            {...homeProps(NO_ACCESS)}
            savedLayout={null}
            onLayoutChange={change}
            onResetLayout={reset}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(t("layoutSettings.noAccess"))).toBeInTheDocument();
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it("preserves an explicitly supplied default-layout label", () => {
    const props = homeProps(BOTH, "apps"),
      t = tFor("ja");
    render(
      <NextIntlClientProvider locale="ja" messages={catalogs.ja}>
        <HomeLayoutSettings
          {...props}
          layouts={props.layouts.map((layout) =>
            layout.key === "builder" ? { ...layout, title: "EXACT_CUSTOM_DEFAULT" } : layout
          )}
          savedLayout="apps"
          onLayoutChange={vi.fn()}
          onResetLayout={vi.fn()}
        />
      </NextIntlClientProvider>
    );
    expect(
      screen.getByRole("button", {
        name: t("layoutSettings.resetDefault", { layout: "EXACT_CUSTOM_DEFAULT" }),
      })
    ).toBeInTheDocument();
  });

  it.each(locales)(
    "%s saves and resets through the actual bridge with exact preference patches",
    async (locale) => {
      const view = actualConsumer(locale),
        t = tFor(locale);
      try {
        await waitFor(() => expect(radio("apps")).toBeChecked());
        fireEvent.click(radio("agents"));
        await waitFor(() =>
          expect(
            view.requests.filter((request) => request.operationName === "UpdateMyUiPreferences")
          ).toHaveLength(1)
        );
        await waitFor(() => expect(radio("agents")).toBeChecked());
        fireEvent.click(
          screen.getByRole("button", {
            name: t("layoutSettings.resetDefault", { layout: t("layouts.builder.title") }),
          })
        );
        await waitFor(() => expect(radio("builder")).toBeChecked());
        expect(
          view.requests
            .filter((request) => request.operationName === "UpdateMyUiPreferences")
            .map((request) => request.variables)
        ).toEqual([
          { input: { homeLayout: "agents", homeLayoutAsked: true } },
          { input: { homeLayout: null, homeLayoutAsked: true } },
        ]);
        expect(parseHomePrefs(window.localStorage.getItem(STORAGE_KEY))).toEqual({
          layout: null,
          asked: true,
        });
        expect(state.error).not.toHaveBeenCalled();
        expect(view.intlErrors).not.toHaveBeenCalled();
      } finally {
        view.stop();
      }
    }
  );

  describe.each(["raw-refusal", "missing-message"] as const)("%s", (mode) => {
    it.each(locales)(
      "%s restores the server choice after a refused write and preserves its diagnostic",
      async (locale) => {
        const view = actualConsumer(locale, mode);
        try {
          await waitFor(() => expect(radio("apps")).toBeChecked());
          fireEvent.click(radio("agents"));
          await waitFor(() =>
            expect(state.error).toHaveBeenCalledExactlyOnceWith(
              mode === "raw-refusal"
                ? "RAW_SERVER_REFUSAL"
                : tFor(locale)("layoutSettings.saveFailed")
            )
          );
          await waitFor(() => expect(radio("apps")).toBeChecked());
          expect(
            view.requests.filter((request) => request.operationName === "UpdateMyUiPreferences")
          ).toHaveLength(1);
          expect(parseHomePrefs(window.localStorage.getItem(STORAGE_KEY))).toEqual({
            layout: "apps",
            asked: true,
          });
          expect(state.success).not.toHaveBeenCalled();
          expect(view.intlErrors).not.toHaveBeenCalled();
        } finally {
          view.stop();
        }
      }
    );
  });

  it("keeps a browser choice on transport failure and retains it across locale changes without resubmitting", async () => {
    const view = actualConsumer("fr", "offline");
    try {
      await waitFor(() => expect(radio("apps")).toBeChecked());
      fireEvent.click(radio("agents"));
      await waitFor(() =>
        expect(
          view.requests.filter((request) => request.operationName === "UpdateMyUiPreferences")
        ).toHaveLength(1)
      );
      expect(radio("agents")).toBeChecked();
      view.changeLocale("ja");
      expect(radio("agents")).toBeChecked();
      expect(screen.getByText(tFor("ja")("layoutSettings.description"))).toBeInTheDocument();
      expect(
        view.requests.filter((request) => request.operationName === "UpdateMyUiPreferences")
      ).toHaveLength(1);
      expect(parseHomePrefs(window.localStorage.getItem(STORAGE_KEY))).toEqual({
        layout: "agents",
        asked: true,
      });
      expect(state.success).not.toHaveBeenCalled();
      expect(state.error).not.toHaveBeenCalled();
      expect(view.intlErrors).not.toHaveBeenCalled();
    } finally {
      view.stop();
    }
  });

  it("holds controls while the first account read is pending, then uses its actual saved layout", async () => {
    applyServerHomePrefs({ ...server, homeLayout: null, homeLayoutAsked: false });
    const view = actualConsumer("de", "pending-read");
    try {
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      await act(async () => view.releaseRead());
      await waitFor(() => expect(radio("apps")).toBeChecked());
      expect(view.requests).toHaveLength(1);
      expect(view.intlErrors).not.toHaveBeenCalled();
    } finally {
      view.stop();
    }
  });

  it("uses the existing browser preference after an unavailable account read without claiming account success", async () => {
    applyServerHomePrefs({ ...server, homeLayout: "agents" });
    const view = actualConsumer("es", "read-failure");
    try {
      await waitFor(() => expect(radio("agents")).toBeChecked());
      expect(screen.getByText(tFor("es")("layoutSettings.description"))).toBeInTheDocument();
      expect(
        view.requests.filter((request) => request.operationName === "UpdateMyUiPreferences")
      ).toHaveLength(0);
      expect(state.success).not.toHaveBeenCalled();
      expect(view.intlErrors).not.toHaveBeenCalled();
    } finally {
      view.stop();
    }
  });

  it("narrows actual offered choices after access changes without automatically rewriting the preference", async () => {
    const view = actualConsumer("fr");
    try {
      await waitFor(() => expect(radio("apps")).toBeChecked());
      fireEvent.click(radio("agents"));
      await waitFor(() => expect(radio("agents")).toBeChecked());
      state.modules = new Set(["apps"]);
      view.changeLocale("ja");
      expect(
        screen.getAllByRole("radio").map((value) => (value as HTMLInputElement).value)
      ).toEqual(["apps"]);
      expect(radio("apps")).toBeChecked();
      expect(
        view.requests.filter((request) => request.operationName === "UpdateMyUiPreferences")
      ).toHaveLength(1);
      expect(parseHomePrefs(window.localStorage.getItem(STORAGE_KEY))).toEqual({
        layout: "agents",
        asked: true,
      });
      expect(view.intlErrors).not.toHaveBeenCalled();
    } finally {
      view.stop();
    }
  });
});
