import { render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";

import { UPTIME } from "./app-overview-cards-a.fixtures";
import { OWNERSHIP } from "./app-overview-panels.fixtures";
import { OwnershipPanel } from "./OwnershipPanel";
import { UptimeCardView } from "./UptimeCard";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}

describe("complete app overview catalogs", () => {
  it("renders French ownership empty states and retains the actual settings destination", () => {
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale="fr" messages={fr} onError={onError}>
        <OwnershipPanel {...OWNERSHIP} projectName="" teamName="" accesses={[]} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText("Aucun projet")).toBeInTheDocument();
    expect(screen.getByText("Aucune autre équipe n’a accès")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Gérer" })).toHaveAttribute(
      "href",
      OWNERSHIP.settingsHref
    );
    expect(onError).not.toHaveBeenCalled();
  });

  it("renders the uptime probe clock in Japanese and the configured time zone", () => {
    const onError = vi.fn();
    const at = "2026-09-30T18:34:56Z";
    render(
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="Asia/Tokyo" onError={onError}>
        <UptimeCardView {...UPTIME} uptime={{ ...UPTIME.uptime!, lastCheckedAt: at }} />
      </NextIntlClientProvider>
    );
    const clock = new Intl.DateTimeFormat("ja", {
      timeZone: "Asia/Tokyo",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
    }).format(new Date(at));
    expect(screen.getByText(clock)).toHaveAttribute("dateTime", at);
    expect(screen.getByText("確認済み", { exact: false })).toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(Object.entries(catalogs))(
    "renders all %s overview messages, including prior missing branches",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.apps.overview, onError });
      for (const key of leaves(en.apps.overview))
        expect(
          t.rich(key as Parameters<typeof t.rich>[0], {
            tag: "sha-123",
            count: 1,
            total: 2000,
            hours: 24,
            previous: 1,
            current: 2,
            relative: "1m",
            state: "future_state_v2",
            pct: 98,
            when: "08:00",
            query: "sha-123",
            filter: "deploy",
            badge: (chunks) => chunks,
          })
        ).toBeTruthy();
      expect(t("latestDeploy.confirmTitle", { tag: "sha-123" })).toContain("sha-123");
      expect(t("health.downReason", { pct: 98, hours: 24 })).toContain("98");
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
