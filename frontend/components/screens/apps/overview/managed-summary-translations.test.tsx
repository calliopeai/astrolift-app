import { fireEvent, render, screen, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, type IntlError } from "next-intl";
import { type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import {
  CONNECTION,
  DEPTH,
  EMAIL,
  OBJECT_STORE,
  OBJECTS,
  POSTGRES,
  QUEUE,
} from "./app-overview-cards-b.fixtures";
import {
  ListObjectsDialogView,
  ManagedServicesSummaryView,
  QueueDepthDialogView,
  RevealConnectionDialogView,
  SendTestEmailDialogView,
} from "./ManagedServicesSummaryCard";
import { EmailDeliveryPanel } from "@/components/screens/email-delivery/EmailDeliveryPanel";
import {
  DELIVERY_PANEL,
  DELIVERY_TEST,
} from "@/components/screens/email-delivery/EmailDeliveryPanel.stories";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function Providers({
  children,
  locale,
  onError,
}: {
  children: ReactNode;
  locale: keyof typeof catalogs;
  onError: (error: IntlError) => void;
}) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="UTC"
      now={new Date("2026-09-30T12:00:00Z")}
      onError={onError}
    >
      {children}
    </NextIntlClientProvider>
  );
}
function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}
describe("translated managed service dialogs", () => {
  it("keeps the raw French read failure visible and retries without presenting an empty service list", () => {
    const onRetry = vi.fn();
    const onError = vi.fn();
    render(
      <Providers locale="fr" onError={onError}>
        <ManagedServicesSummaryView
          loading={false}
          error="upstream read denied"
          onRetry={onRetry}
          services={[]}
          managedServicesHref="/apps/storefront/managed-services"
          dialogs={{
            reveal: () => null,
            sendEmail: () => null,
            objects: () => null,
            depth: () => null,
          }}
        />
      </Providers>
    );
    expect(
      screen.getByText(fr.apps.settings.managedServicesSummary.loadFailed)
    ).toBeInTheDocument();
    expect(screen.getByText("upstream read denied")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });
  it.each(["fr", "ja"] as const)(
    "%s keeps reviewed provider acceptance visible in the existing quick-action dialog",
    (locale) => {
      const onError = vi.fn(),
        onClose = vi.fn();
      render(
        <Providers locale={locale} onError={onError}>
          <SendTestEmailDialogView
            svc={EMAIL}
            open
            onOpenChange={onClose}
            body={<EmailDeliveryPanel {...DELIVERY_PANEL} current={DELIVERY_TEST} />}
          />
        </Providers>
      );
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(screen.getByText(catalogs[locale].emailDelivery.acceptedHelp)).toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it("formats French object quantities and snapshot age without translating actual object keys", () => {
    const onError = vi.fn();
    const result = {
      ...OBJECTS,
      cacheAgeSeconds: 90,
      objects: [{ ...OBJECTS.objects[0], sizeBytes: 1536 }],
    };
    render(
      <Providers locale="fr" onError={onError}>
        <ListObjectsDialogView
          svc={OBJECT_STORE}
          open
          onOpenChange={vi.fn()}
          result={result}
          loading={false}
          onRefresh={vi.fn()}
        />
      </Providers>
    );
    expect(screen.getByText(result.objects[0].key)).toBeInTheDocument();
    expect(screen.getByText("1,5 KB")).toBeInTheDocument();
    const age = new Intl.NumberFormat("fr", {
      style: "unit",
      unit: "minute",
      unitDisplay: "narrow",
      maximumFractionDigits: 0,
    }).format(2);
    expect(screen.getByText(`L’instantané date de ${age}.`, { exact: false })).toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();
  });
  it("uses German grouping for queue depth without changing counts", () => {
    const onError = vi.fn();
    render(
      <Providers locale="de" onError={onError}>
        <QueueDepthDialogView
          svc={QUEUE}
          open
          onOpenChange={vi.fn()}
          result={DEPTH}
          loading={false}
          onRefresh={vi.fn()}
        />
      </Providers>
    );
    expect(screen.getByText("1.284")).toBeInTheDocument();
    expect(screen.getByText("17")).toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();
  });
  it("keeps connection metadata visible and copies only the actual supplied reference shim for a masked secret row", () => {
    const onCopy = vi.fn();
    const onError = vi.fn();
    render(
      <Providers locale="ja" onError={onError}>
        <RevealConnectionDialogView
          svc={POSTGRES}
          open
          onOpenChange={vi.fn()}
          revealed={CONNECTION}
          loading={false}
          onCopy={onCopy}
        />
      </Providers>
    );
    expect(screen.getByText(CONNECTION.connectionSecretRef!)).toBeInTheDocument();
    expect(screen.getByText(CONNECTION.keys[0].value)).toBeInTheDocument();
    const secret = CONNECTION.keys.find((key) => key.isSecret)!;
    const row = screen.getByText(secret.key).closest("li")!;
    expect(within(row).getByText("••••")).toBeInTheDocument();
    expect(screen.queryByText(secret.value)).not.toBeInTheDocument();
    fireEvent.click(
      within(row).getByRole("button", {
        name: ja.apps.settings.managedServicesSummary.revealDialog.copy,
      })
    );
    expect(onCopy).toHaveBeenCalledExactlyOnceWith(secret.value);
    expect(onError).not.toHaveBeenCalled();
  });
  it.each(Object.entries(catalogs))(
    "renders every %s managed summary message without fallback",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({
        locale,
        messages: messages.apps.settings.managedServicesSummary,
        onError,
      });
      for (const key of leaves(en.apps.settings.managedServicesSummary))
        expect(
          t(key as Parameters<typeof t>[0], {
            name: "actual-service",
            kind: "postgres",
            status: "active",
            action: "connection.reveal",
            when: "08:00",
            age: "2 min",
            recipient: "recipient@example.test",
            transport: "smtp",
          })
        ).toBeTruthy();
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
