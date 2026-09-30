import { MockedProvider } from "@apollo/client/testing/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { type IntlError, createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
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
import { DeregisterPendingBannerView } from "../overview/DeregisterPendingBanner";
import {
  DANGER_ZONE,
  DEREGISTER_PREVIEW,
  FORCE_REDEPLOY,
  FORCE_REDEPLOY_PREVIEW,
} from "./app-settings-members.fixtures";
import { DangerZoneView } from "./DangerZone";
import { ForceRedeployView } from "./ForceRedeploy";
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
        value={{ granted: new Set(["app.delete", "app.deploy", "app.update"]), loading: false }}
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
describe("translated recovery and teardown", () => {
  it.each([
    ["fr", "Désenregistrer l’application", "Objets Kubernetes"],
    ["ja", "アプリの登録を解除", "Kubernetes オブジェクト"],
  ] as const)(
    "%s requires the actual app name and retains it across rejected teardown retry",
    async (locale, remove, group) => {
      const onError = vi.fn();
      const loadPreview = vi.fn();
      const onDeregister = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
      render(
        <Providers locale={locale} onError={onError}>
          <DangerZoneView
            {...DANGER_ZONE}
            preview={DEREGISTER_PREVIEW}
            loadPreview={loadPreview}
            onDeregister={onDeregister}
          />
        </Providers>
      );
      fireEvent.click(screen.getByRole("button", { name: (name) => name.includes(remove) }));
      const dialog = screen.getByRole("alertdialog");
      const button = within(dialog).getByRole("button", { name: remove });
      const input = within(dialog).getByRole("textbox");
      expect(within(dialog).getByRole("heading")).toHaveTextContent("Checkout");
      expect(loadPreview).toHaveBeenCalledTimes(1);
      expect(button).toBeDisabled();
      expect(onDeregister).not.toHaveBeenCalled();
      fireEvent.click(within(dialog).getByRole("button", { name: (name) => name.includes(group) }));
      expect(dialog).toHaveTextContent("prd-us-west-2/checkout");
      expect(dialog).toHaveTextContent("Deployment");
      expect(dialog).toHaveTextContent("web");
      fireEvent.change(input, { target: { value: "checkout" } });
      expect(button).toBeDisabled();
      fireEvent.change(input, { target: { value: "Checkout" } });
      fireEvent.click(button);
      await waitFor(() => expect(onDeregister).toHaveBeenCalledExactlyOnceWith("Checkout"));
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(input).toHaveValue("Checkout");
      fireEvent.click(button);
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(onDeregister).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it.each([
    ["fr", "Forcer le redéploiement"],
    ["ja", "強制再デプロイ"],
  ] as const)(
    "%s keeps the slug, preview provenance and required retry for recovery",
    async (locale, label) => {
      const onError = vi.fn();
      const onForceRedeploy = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
      render(
        <Providers locale={locale} onError={onError}>
          <ForceRedeployView {...FORCE_REDEPLOY} onForceRedeploy={onForceRedeploy} />
        </Providers>
      );
      fireEvent.click(screen.getByRole("button", { name: label }));
      const dialog = screen.getByRole("alertdialog");
      const button = within(dialog).getByRole("button", { name: label });
      const input = within(dialog).getByRole("textbox");
      const actual = FORCE_REDEPLOY_PREVIEW.inFlightDeployments[0];
      for (const value of [actual.environmentName, actual.imageTag, actual.triggeredByDisplay])
        expect(dialog).toHaveTextContent(value);
      expect(within(dialog).getByRole("link")).toHaveAttribute("href", actual.ciRunUrl);
      expect(button).toBeDisabled();
      expect(onForceRedeploy).not.toHaveBeenCalled();
      fireEvent.change(input, { target: { value: "other-app" } });
      expect(button).toBeDisabled();
      fireEvent.change(input, { target: { value: "checkout" } });
      fireEvent.click(button);
      await waitFor(() => expect(onForceRedeploy).toHaveBeenCalledExactlyOnceWith("checkout"));
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(input).toHaveValue("checkout");
      fireEvent.click(button);
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(onForceRedeploy).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it("refuses teardown with an unavailable preview even after the French app name matches", () => {
    const onDeregister = vi.fn();
    render(
      <Providers locale="fr" onError={vi.fn()}>
        <DangerZoneView
          {...DANGER_ZONE}
          previewError={new Error("OWNER_SCOPE_DENIED")}
          onDeregister={onDeregister}
        />
      </Providers>
    );
    fireEvent.click(screen.getByRole("button", { name: "Désenregistrer l’application" }));
    const dialog = screen.getByRole("alertdialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Checkout" } });
    expect(
      within(dialog).getByRole("button", { name: "Désenregistrer l’application" })
    ).toBeDisabled();
    expect(onDeregister).not.toHaveBeenCalled();
  });
  it("keeps the actual Spanish grace countdown and disables cancellation in flight", () => {
    const onCancel = vi.fn();
    const frame = (cancelling: boolean, msRemaining: number | null = 272000) => (
      <NextIntlClientProvider locale="es" messages={es}>
        <DeregisterPendingBannerView
          cancelling={cancelling}
          msRemaining={msRemaining}
          onCancel={onCancel}
        />
      </NextIntlClientProvider>
    );
    const view = render(frame(false));
    expect(screen.getByText(/4:32/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar eliminación" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    view.rerender(frame(true));
    expect(screen.getByRole("button", { name: "Cancelar eliminación" })).toBeDisabled();
    view.rerender(frame(false, null));
    expect(view.container).toBeEmptyDOMElement();
  });
  it.each(Object.entries(catalogs))(
    "%s renders every warning and exact zero/singular/plural ICU branch without fallback",
    (locale, messages) => {
      const onError = vi.fn();
      for (const [catalog, source] of [
        [messages.apps.settings.dangerZone, en.apps.settings.dangerZone],
        [messages.apps.settings.forceRedeploy, en.apps.settings.forceRedeploy],
        [messages.apps.dangerZone.pendingBanner, en.apps.dangerZone.pendingBanner],
      ] as const) {
        const t = createTranslator({ locale, messages: catalog, onError });
        for (const key of leaves(source))
          expect(
            t.rich(key as Parameters<typeof t.rich>[0], {
              name: "Checkout",
              slug: key === "typeToConfirm" ? () => "checkout" : "checkout",
              count: 2,
              workflowId: "deregister-checkout",
              countdown: "4:32",
              actor: "leo@example.com",
              when: "TIME",
            })
          ).toBeTruthy();
      }
      const t = createTranslator({
        locale,
        messages: messages.apps.settings.dangerZone.preview,
        onError,
      });
      expect(t("header", { count: 0 })).not.toEqual(t("header", { count: 1 }));
      expect(t("header", { count: 1 })).not.toEqual(t("header", { count: 2 }));
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
