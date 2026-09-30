import { MockedProvider } from "@apollo/client/testing/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, type IntlError } from "next-intl";
import type { ReactNode } from "react";
import { userEvent } from "storybook/test";
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
import {
  CRON_JOBS,
  ENVIRONMENTS,
  INGRESS,
  RESYNC,
  RUN_JOB,
  WEBHOOK_DEPLOYS,
  WEBHOOK_DEPLOYS_PAUSED,
} from "./app-settings-members.fixtures";
import { IngressControlsView } from "./IngressControls";
import { ResyncSourceView } from "./ResyncSource";
import { RunScheduledJobView } from "./RunScheduledJob";
import { WebhookDeploysPauseView } from "./WebhookDeploysPause";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function Providers({
  children,
  locale,
  onError,
}: {
  children: ReactNode;
  locale: "fr" | "ja" | "es" | "de";
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
describe("translated operational settings", () => {
  it.each([
    ["fr", "Suspendre les déploiements par webhook", "Motif (facultatif)"],
    ["ja", "Webhook デプロイを一時停止", "理由（任意）"],
  ] as const)(
    "%s retains the exact optional pause reason through failure and retry",
    async (locale, pause, reasonLabel) => {
      const onPause = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
      const onError = vi.fn();
      render(
        <Providers locale={locale} onError={onError}>
          <WebhookDeploysPauseView {...WEBHOOK_DEPLOYS} onPause={onPause} />
        </Providers>
      );
      fireEvent.click(screen.getByRole("button", { name: pause }));
      const dialog = screen.getByRole("alertdialog");
      expect(onPause).not.toHaveBeenCalled();
      const input = within(dialog).getByLabelText(reasonLabel);
      fireEvent.change(input, { target: { value: "keep this exact user reason" } });
      fireEvent.click(within(dialog).getByRole("button", { name: pause }));
      await waitFor(() =>
        expect(onPause).toHaveBeenCalledExactlyOnceWith("keep this exact user reason")
      );
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(input).toHaveValue("keep this exact user reason");
      fireEvent.click(within(dialog).getByRole("button", { name: pause }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(onPause).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it("preserves French pause actor/reason and resumes without another confirmation", () => {
    const onResume = vi.fn();
    const onError = vi.fn();
    render(
      <Providers locale="fr" onError={onError}>
        <WebhookDeploysPauseView {...WEBHOOK_DEPLOYS_PAUSED} onResume={onResume} />
      </Providers>
    );
    expect(screen.getByText("leo@example.com")).toBeInTheDocument();
    expect(screen.getByText(WEBHOOK_DEPLOYS_PAUSED.pauseReason)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reprendre les déploiements par webhook" }));
    expect(onResume).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();
  });
  it("runs the selected Japanese cronjob in the selected actual environment and keeps its jobs link", async () => {
    const onRun = vi.fn();
    const onError = vi.fn();
    render(
      <Providers locale="ja" onError={onError}>
        <RunScheduledJobView {...RUN_JOB} onRun={onRun} />
      </Providers>
    );
    expect(screen.getByRole("link", { name: "ジョブページ" })).toHaveAttribute(
      "href",
      "/apps/checkout/jobs"
    );
    expect(onRun).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("combobox", { name: "ジョブ" }));
    await userEvent.click(
      screen.getByRole("option", { name: (name) => name.includes(CRON_JOBS[1].slug) })
    );
    await userEvent.click(screen.getByRole("combobox", { name: "環境" }));
    await userEvent.click(screen.getByRole("option", { name: ENVIRONMENTS[1].name }));
    fireEvent.click(screen.getByRole("button", { name: "今すぐ実行" }));
    expect(onRun).toHaveBeenCalledExactlyOnceWith(CRON_JOBS[1].slug, ENVIRONMENTS[1].name);
    expect(onError).not.toHaveBeenCalled();
  });
  it("targets the actual Spanish ingress environment and disables its in-flight toggle", () => {
    const onToggle = vi.fn();
    const onError = vi.fn();
    const frame = (busyIds: string[]) => (
      <Providers locale="es" onError={onError}>
        <IngressControlsView
          {...INGRESS}
          envs={[ENVIRONMENTS[0]]}
          busyIds={busyIds}
          onToggle={onToggle}
        />
      </Providers>
    );
    const view = render(frame([]));
    expect(screen.getByText(ENVIRONMENTS[0].name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Pausar" }));
    expect(onToggle).toHaveBeenCalledExactlyOnceWith(ENVIRONMENTS[0]);
    view.rerender(frame([ENVIRONMENTS[0].id]));
    expect(screen.getByRole("button", { name: "Pausar" })).toBeDisabled();
    expect(onError).not.toHaveBeenCalled();
  });
  it("keeps source resync admission while rendering German manifest help", () => {
    const onResync = vi.fn();
    const onError = vi.fn();
    const frame = (loading: boolean) => (
      <Providers locale="de" onError={onError}>
        <ResyncSourceView {...RESYNC} loading={loading} onResync={onResync} />
      </Providers>
    );
    const view = render(frame(false));
    expect(screen.getByText(/Liest astrolift.toml/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mit Quellcode erneut synchronisieren" }));
    expect(onResync).toHaveBeenCalledTimes(1);
    view.rerender(frame(true));
    expect(screen.getByRole("button", { name: "Wird erneut synchronisiert…" })).toBeDisabled();
    expect(onError).not.toHaveBeenCalled();
  });
  it.each(Object.entries(catalogs))(
    "%s renders every operational message and both audit rich tags without fallback",
    (locale, messages) => {
      const onError = vi.fn();
      const settings = createTranslator({ locale, messages: messages.apps.settings, onError });
      for (const key of ["title", "loading", "description"] as const)
        expect(settings.rich(key, { slug: () => "checkout" })).toBeTruthy();
      for (const group of ["resync", "ingress", "webhookDeploys", "runJob"] as const) {
        const t = createTranslator({ locale, messages: messages.apps.settings[group], onError });
        for (const key of Object.keys(en.apps.settings[group]))
          expect(
            t.rich(key as Parameters<typeof t.rich>[0], {
              who: () => "leo@example.com",
              when: () => "TIME",
            })
          ).toBeTruthy();
      }
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
