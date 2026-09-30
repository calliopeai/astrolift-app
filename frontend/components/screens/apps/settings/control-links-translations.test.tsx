import { MockedProvider } from "@apollo/client/testing/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, type IntlError } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { ENV_PROD, WORKLOADS, envControls, workloadOps } from "../controls/app-controls.fixtures";
import { EnvironmentControlsView, WorkloadOpsRowView } from "../controls/ControlsSection";
import { APP, SETTINGS } from "./app-settings-members.fixtures";
import { AppSettingsScreen } from "./AppSettingsScreen";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function Providers({
  children,
  locale,
  onError,
}: {
  children: ReactNode;
  locale: "fr" | "ja";
  onError: (error: IntlError) => void;
}) {
  return (
    <MockedProvider>
      <PermissionsProvider
        value={{ granted: new Set(["app.deploy", "app.update"]), loading: false }}
      >
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          now={new Date("2026-09-30T12:00:00Z")}
          onError={onError}
        >
          <TooltipProvider>{children}</TooltipProvider>
        </NextIntlClientProvider>
      </PermissionsProvider>
    </MockedProvider>
  );
}
function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}
describe("translated app controls and settings destinations", () => {
  it.each([
    ["fr", "Déployer maintenant", "Augmenter les réplicas", "Appliquer"],
    ["ja", "今すぐデプロイ", "レプリカを増やす", "適用"],
  ] as const)(
    "%s retains a rejected image tag and resets rejected replica staging without changing callbacks",
    async (locale, deployLabel, increaseLabel, applyLabel) => {
      const onError = vi.fn();
      const onDeploy = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
      const onApply = vi.fn().mockResolvedValue(false);
      const environment = envControls(ENV_PROD);
      render(
        <Providers locale={locale} onError={onError}>
          <EnvironmentControlsView {...environment} onDeploy={onDeploy} />
          <WorkloadOpsRowView {...workloadOps(WORKLOADS[0])} onApply={onApply} />
        </Providers>
      );
      const tag = screen.getByPlaceholderText(environment.lastTag);
      fireEvent.change(tag, { target: { value: "sha-user-supplied" } });
      fireEvent.click(screen.getByRole("button", { name: deployLabel }));
      await waitFor(() => expect(onDeploy).toHaveBeenCalledExactlyOnceWith("sha-user-supplied"));
      expect(tag).toHaveValue("sha-user-supplied");
      fireEvent.click(screen.getByRole("button", { name: deployLabel }));
      await waitFor(() => expect(tag).toHaveValue(""));
      expect(onDeploy).toHaveBeenCalledTimes(2);
      const apply = screen.getByRole("button", { name: applyLabel });
      expect(apply).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: increaseLabel }));
      fireEvent.click(apply);
      await waitFor(() =>
        expect(onApply).toHaveBeenCalledExactlyOnceWith((WORKLOADS[0].replicas ?? 0) + 1)
      );
      await waitFor(() => expect(apply).toBeDisabled());
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it.each([
    ["fr", false, "Modifier le manifeste TOML", "Modifié il y a 5 minutes"],
    ["fr", true, "Modifier le manifeste TOML", "Modifié il y a 5 minutes"],
    ["ja", false, "TOML マニフェストを編集", "5 分前に変更"],
    ["ja", true, "TOML マニフェストを編集", "5 分前に変更"],
  ] as const)(
    "%s compact=%s localizes the actual section timestamp and retains every app route",
    (locale, compactLinks, label, modified) => {
      const onError = vi.fn();
      const app = {
        ...APP,
        settingsLastModified: {
          deployStrategy: "2026-09-30T11:55:00Z",
          deployTokens: null,
          secrets: null,
          managedServices: null,
          domains: null,
          webhooks: null,
          members: null,
          observability: null,
        },
      };
      render(
        <Providers locale={locale} onError={onError}>
          <AppSettingsScreen {...SETTINGS} app={app} compactLinks={compactLinks} />
        </Providers>
      );
      expect(screen.getByRole("link", { name: (name) => name.includes(label) })).toHaveAttribute(
        "href",
        "/apps/checkout/config"
      );
      expect(screen.getByText(modified)).toBeInTheDocument();
      expect(screen.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(
        [
          "config",
          "tokens",
          "secrets",
          "managed-services",
          "domains",
          "webhooks",
          "members",
          "observability",
        ].map((segment) => `/apps/checkout/${segment}`)
      );
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it.each(Object.entries(catalogs))(
    "%s resolves control ICU quantities and settings link messages without fallback",
    (locale, messages) => {
      const onError = vi.fn();
      const controls = createTranslator({
        locale,
        messages: messages.apps.settings.controls,
        onError,
      });
      const links = createTranslator({ locale, messages: messages.apps.settings.links, onError });
      for (const key of Object.keys(en.apps.settings.controls))
        expect(
          controls(key as Parameters<typeof controls>[0], {
            count: 2,
            ready: "—",
            desired: 3,
            workload: "checkout-web",
            env: "production",
            tag: "sha-123",
            branch: "release/stable",
          })
        ).toBeTruthy();
      for (const key of leaves(en.apps.settings.links))
        expect(links(key as Parameters<typeof links>[0], { when: "TIME" })).toBeTruthy();
      expect(
        controls("toastScaled", { workload: "checkout-web", env: "production", count: 1 })
      ).not.toEqual(
        controls("toastScaled", { workload: "checkout-web", env: "production", count: 2 })
      );
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
