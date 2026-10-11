import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider, createTranslator } from "next-intl";
import { describe, it, expect, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { AppShell } from "@/components/shell/AppShell";
import { EmailDeliveryPanel } from "./EmailDeliveryPanel";
import { DELIVERY_PANEL, DELIVERY_TEST } from "./EmailDeliveryPanel.stories";

const keys =
  `title intro checking supportUnavailable denied refresh service sender identity account region sourceVersion recipient subject body optional review reviewHelp reviewed send retry sending uncertain acceptedUnverified acceptedHelp noTracking simulatorHelp currentRequest startAnother history emptyHistory historyUnavailable createdAt observedAt providerMessageId status reason submitting accepted unknown failed suppressed delivered deferred bounced complained rejected observationTimedOut unrecognizedStatus domainTitle domainHelp apps appSearch services selectApp selectService noApps noServices selectionUnavailable match different unconfirmedDomain mailDns dnsHelp mx spf dmarc dkim selector lookup invalidSelector lookupLoading lookupUnavailable recordsEmpty perspective checkedAt records recoverHelp invalidRecipient invalidContent dkimRecordType`.split(
    " "
  );
const catalogues = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
describe("email delivery public copy", () => {
  it.each(Object.entries(catalogues))(
    "%s renders translated mobile navigation labels",
    (locale, messages) => {
      expect(Object.keys(messages.responsiveShell).sort()).toEqual([
        "close",
        "navigation",
        "projects",
      ]);
      const t = createTranslator({ locale, messages, namespace: "responsiveShell" });
      for (const key of ["close", "navigation", "projects"] as const)
        expect(t(key)).not.toMatch(/[{}]/);
      const onError = vi.fn();
      render(
        <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
          <AppShell mainRail={<nav />} projectsRail={<aside />}>
            <p>content</p>
          </AppShell>
        </NextIntlClientProvider>
      );
      fireEvent.click(screen.getByRole("button", { name: messages.responsiveShell.navigation }));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: messages.responsiveShell.close }));
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it.each(Object.entries(catalogues))(
    "%s has the exact copy map and renders acceptance separately from observation",
    (locale, messages) => {
      expect(Object.keys(messages.emailDelivery).sort()).toEqual([...keys].sort());
      const t = createTranslator({ locale, messages, namespace: "emailDelivery" });
      keys.forEach((key) => {
        const value = t(key as keyof typeof en.emailDelivery);
        expect(value.trim()).not.toBe("");
        expect(value).not.toMatch(/[{}]/);
      });
      if (locale !== "en")
        expect(
          Object.entries(messages.emailDelivery).filter(
            ([key, value]) => value === en.emailDelivery[key as keyof typeof en.emailDelivery]
          ).length
        ).toBeLessThan(4);
      const onError = vi.fn();
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={messages}
          timeZone="UTC"
          onError={onError}
        >
          <EmailDeliveryPanel
            {...DELIVERY_PANEL}
            current={{ ...DELIVERY_TEST, eventTrackingConfigured: false }}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(messages.emailDelivery.acceptedHelp)).toBeInTheDocument();
      expect(screen.getByText(messages.emailDelivery.noTracking)).toBeInTheDocument();
      expect(screen.getByText(messages.emailDelivery.simulatorHelp)).toBeInTheDocument();
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it("does not enable sends from stale or refused support even with a stale action flag", () => {
    const send = vi.fn();
    const { rerender } = render(
      <NextIntlClientProvider locale="en" messages={en}>
        <EmailDeliveryPanel
          {...DELIVERY_PANEL}
          support={{ ...DELIVERY_PANEL.support!, allowed: false }}
          reviewed
          canSend
          onSend={send}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("button", { name: en.emailDelivery.send })).toBeDisabled();
    rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <EmailDeliveryPanel {...DELIVERY_PANEL} supportLoading reviewed canSend onSend={send} />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("button", { name: en.emailDelivery.send })).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
  });
  it("retains the request and locks message edits through an unknown reply", () => {
    const draft = vi.fn();
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <EmailDeliveryPanel
          {...DELIVERY_PANEL}
          requestId={DELIVERY_TEST.requestId}
          locked
          message="uncertain"
          draft={{
            recipient: DELIVERY_TEST.recipient,
            subject: "private-subject",
            body: "private-body",
          }}
          onDraft={draft}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(en.emailDelivery.uncertain)).toBeInTheDocument();
    expect(screen.getByLabelText(en.emailDelivery.recipient)).toBeDisabled();
    const history = screen.getByRole("table", { name: en.emailDelivery.history });
    expect(within(history).queryByText("private-subject")).not.toBeInTheDocument();
    expect(within(history).queryByText("private-body")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.emailDelivery.retry }));
    expect(draft).not.toHaveBeenCalled();
  });
});
