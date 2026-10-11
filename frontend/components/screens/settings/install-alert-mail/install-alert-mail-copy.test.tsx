import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider, createTranslator } from "next-intl";
import { it, expect, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { InstallAlertMailPanel } from "./InstallAlertMailPanel";
import { ALERT_PANEL, ALERT_TEST } from "./InstallAlertMailPanel.stories";
const keys =
  "title intro refresh event preferences checking unavailable unsupported sender recipient transport tls checked reviewHelp review sending send reviewed request uncertain sourceChanged recordUnverified storageUnavailable another history outcome created reason reserved sent accepted failed unknown acceptance empty emptyHelp historyUnavailable event0 event1 event2 event3 event4 event5 event6 event7 open".split(
    " "
  );
it.each(Object.entries({ en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt }))(
  "%s exact translated copy renders own-mailbox acceptance without delivery promise",
  (locale, messages) => {
    expect(Object.keys(messages.installAlertMail).sort()).toEqual([...keys].sort());
    const t = createTranslator({ locale, messages, namespace: "installAlertMail" });
    for (const key of keys) {
      expect(t(key as keyof typeof en.installAlertMail)).not.toMatch(/[{}]/);
    }
    if (locale !== "en")
      expect(
        Object.entries(messages.installAlertMail).filter(
          ([key, value]) => value === en.installAlertMail[key as keyof typeof en.installAlertMail]
        ).length
      ).toBeLessThan(3);
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC" onError={onError}>
        <InstallAlertMailPanel {...ALERT_PANEL} current={ALERT_TEST} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(messages.installAlertMail.acceptance)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(8);
    expect(onError).not.toHaveBeenCalled();
  }
);
it.each([
  { support: { ...ALERT_PANEL.support!, allowed: false } },
  { loading: true },
  { supportError: true },
])("refused/loading support cannot expose enabled send from stale flags", (over) => {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <InstallAlertMailPanel {...ALERT_PANEL} {...over} canSend canReview />
    </NextIntlClientProvider>
  );
  expect(screen.getByRole("button", { name: en.installAlertMail.send })).toBeDisabled();
  expect(screen.getByRole("button", { name: en.installAlertMail.review })).toBeDisabled();
});
