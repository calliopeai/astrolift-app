import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";

import { AuditLogScreen, type AuditLogScreenProps } from "./AuditLogScreen";
import { AUDIT_LIST, auditVariables, localizedAuditList } from "./audit-list";
import { AUDIT } from "./fixtures";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };

function Audit({ saveRetention }: Pick<AuditLogScreenProps, "saveRetention">) {
  const t = useTranslations("lists.audit");
  const list = useLocalListState(localizedAuditList(t), {
    filters: { decision: "DENY", since: "7d" },
  });
  return (
    <AuditLogScreen
      {...AUDIT}
      list={list}
      saveRetention={saveRetention}
      events={{ ...AUDIT.events, items: [], hasMore: false }}
    />
  );
}

function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}

describe("audit localization", () => {
  it.each([
    [
      "es",
      "Conservación",
      "Conservar eventos de auditoría durante (días)",
      "Guardar",
      "Ningún evento de auditoría coincide",
    ],
    [
      "fr",
      "Conservation",
      "Conserver les événements d’audit pendant (jours)",
      "Enregistrer",
      "Aucun événement d’audit correspondant",
    ],
    ["ko", "보관 기간", "감사 이벤트 보관 기간(일)", "저장", "일치하는 감사 이벤트가 없습니다"],
  ] as const)(
    "uses %s filter/retention labels without closing an unsuccessful save",
    async (locale, retention, label, save, empty) => {
      const onError = vi.fn();
      const saveRetention = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
      render(
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
          <Audit saveRetention={saveRetention} />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(empty)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: retention }));
      const dialog = await screen.findByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText(label), { target: { value: "180" } });
      fireEvent.click(within(dialog).getByRole("button", { name: save }));
      await waitFor(() => expect(saveRetention).toHaveBeenCalledWith(180));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole("button", { name: save }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(saveRetention).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it.each(Object.entries(catalogs))(
    "preserves actual audit filter values and renders all %s messages",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.lists.audit, onError });
      for (const key of leaves(en.lists.audit))
        expect(
          t(key as Parameters<typeof t>[0], {
            days: 365,
            count: 1284,
            rows: 2500,
            min: 1,
            max: 2557,
          })
        ).toBeTruthy();
      const definition = localizedAuditList((key) => t(key as Parameters<typeof t>[0]));
      expect(
        definition.fields.map(({ key, options }) => ({
          key,
          values: options?.map(({ value }) => value),
        }))
      ).toEqual(
        AUDIT_LIST.fields.map(({ key, options }) => ({
          key,
          values: options?.map(({ value }) => value),
        }))
      );
      expect(definition.views.map(({ key, filters }) => ({ key, filters }))).toEqual(
        AUDIT_LIST.views.map(({ key, filters }) => ({ key, filters }))
      );
      expect(
        auditVariables(definition.views[2].filters, "team.create", {
          pageSize: 100,
          after: null,
          now: Date.UTC(2026, 8, 30),
        })
      ).toMatchObject({ filter: { decision: ["DENY"] }, search: "team.create" });
      expect(t("eventsCount", { count: 1 })).not.toContain("{count");
      expect(t("eventsCount", { count: 2000 })).not.toContain("{count");
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
