import { fireEvent, render, screen } from "@testing-library/react";
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

import { ActivityTimelineView } from "./ActivityTimeline";
import { ACTIVITY } from "./app-overview-cards-b.fixtures";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };

describe("app activity translations", () => {
  it.each([
    ["es", "Despliegues", "Buscar actividad", "Todos", "1 evento de 6", "Ningún evento coincide"],
    [
      "fr",
      "Déploiements",
      "Rechercher dans l’activité",
      "Tous",
      "1 événement sur 6",
      "Aucun événement ne correspond",
    ],
    [
      "ja",
      "デプロイ",
      "アクティビティを検索",
      "すべて",
      "6件中1件のイベント",
      "に一致するイベントはありません",
    ],
  ] as const)(
    "uses %s labels while retaining event filtering and payload identifiers",
    (locale, deploy, search, all, count, noMatch) => {
      const onError = vi.fn();
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          now={new Date()}
          onError={onError}
        >
          <ActivityTimelineView {...ACTIVITY} />
        </NextIntlClientProvider>
      );
      fireEvent.click(screen.getByRole("button", { name: deploy }));
      expect(screen.getByText("web:3f9c2a1 → succeeded")).toBeInTheDocument();
      expect(screen.queryByText("DATABASE_URL rotated")).not.toBeInTheDocument();
      expect(screen.getByText(count)).toBeInTheDocument();
      fireEvent.change(screen.getByRole("textbox", { name: search }), {
        target: { value: "no-such-image" },
      });
      expect(screen.getByText(new RegExp(noMatch))).toHaveTextContent("no-such-image");
      fireEvent.change(screen.getByRole("textbox", { name: search }), { target: { value: "" } });
      fireEvent.click(screen.getByRole("button", { name: all }));
      expect(screen.getByText("DATABASE_URL rotated")).toBeInTheDocument();
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it.each(Object.entries(catalogs))(
    "renders every %s activity message and its rich filter badge",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.apps.overview.activity, onError });
      const { filters: _filters, ...copy } = en.apps.overview.activity;
      for (const key of Object.keys(copy) as Array<keyof typeof copy>)
        expect(
          t.rich(key, {
            count: 1,
            total: 2000,
            query: "sha-123",
            filter: "deploy",
            badge: (chunks) => chunks,
          })
        ).toBeTruthy();
      for (const key of Object.keys(en.apps.overview.activity.filters) as Array<
        keyof typeof en.apps.overview.activity.filters
      >)
        expect(t(`filters.${key}`)).toBeTruthy();
      expect(
        t.rich("noMatchesQuery", { query: "sha-123", filter: "deploy", badge: (chunks) => chunks })
      ).toContain("sha-123");
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
