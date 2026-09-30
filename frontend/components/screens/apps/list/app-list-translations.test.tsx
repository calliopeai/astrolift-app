import { MockedProvider } from "@apollo/client/testing/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";
import { useLocalListState } from "@/components/list/use-list-state";
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

import { APPS_LIST, appsPageVariables, localizedAppsList } from "./apps-list";
import { AppsListScreen, type AppsListScreenProps } from "./AppsListScreen";
import { APPS, listProps } from "./fixtures";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function Apps({ onPushSecrets }: Pick<AppsListScreenProps, "onPushSecrets">) {
  const t = useTranslations("apps.list");
  const status = useTranslations("apps.frame");
  const list = useLocalListState(localizedAppsList(t, status));
  return (
    <AppsListScreen
      {...listProps({ rows: APPS.slice(0, 2), totalCount: 2 })}
      list={list}
      onPushSecrets={onPushSecrets}
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

describe("translated app registry", () => {
  it.each([
    [
      "fr",
      "Envoyer les secrets",
      "Slug du paquet",
      "Environnement (facultatif)",
      "Envoyer à 1 application",
    ],
    ["ja", "シークレットを送信", "バンドルのスラッグ", "環境（任意）", "1 件のアプリに送信"],
  ] as const)(
    "retains %s selection and actual bundle/environment values after an unsuccessful push",
    async (locale, push, bundle, environment, submit) => {
      const onError = vi.fn();
      const onPushSecrets = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
      render(
        <MockedProvider>
          <PermissionsProvider value={{ granted: new Set(["app.create"]), loading: false }}>
            <NextIntlClientProvider
              locale={locale}
              messages={catalogs[locale]}
              timeZone="UTC"
              now={new Date("2026-09-30T12:00:00Z")}
              onError={onError}
            >
              <TooltipProvider>
                <Apps onPushSecrets={onPushSecrets} />
              </TooltipProvider>
            </NextIntlClientProvider>
          </PermissionsProvider>
        </MockedProvider>
      );
      const slug = APPS[0].slug;
      const row = screen.getByRole("row", {
        name: (name) => name.includes(APPS[0].name) && name.includes(slug),
      });
      const checkbox = within(row).getByRole("checkbox");
      fireEvent.click(checkbox);
      fireEvent.click(screen.getByRole("button", { name: push }));
      const dialog = await screen.findByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText(bundle), {
        target: { value: " shared-prod " },
      });
      fireEvent.change(within(dialog).getByLabelText(environment), {
        target: { value: " staging " },
      });
      fireEvent.click(within(dialog).getByRole("button", { name: submit }));
      await waitFor(() =>
        expect(onPushSecrets).toHaveBeenCalledWith([slug], "shared-prod", "staging")
      );
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(within(dialog).getByLabelText(bundle)).toHaveValue(" shared-prod ");
      expect(within(dialog).getByLabelText(environment)).toHaveValue(" staging ");
      fireEvent.click(within(dialog).getByRole("button", { name: submit }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(onPushSecrets).toHaveBeenCalledTimes(2);
      expect(checkbox).not.toBeChecked();
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it.each(Object.entries(catalogs))(
    "renders all %s registry messages while preserving actual query values",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.apps.list, onError });
      const status = createTranslator({ locale, messages: messages.apps.frame, onError });
      for (const key of leaves(en.apps.list)) {
        expect(
          t(key as Parameters<typeof t>[0], {
            count: 2,
            shown: 25,
            total: 100,
            when: "12:00",
            action: "ACTION",
            okCount: 1,
            failedSlugs: "checkout-worker",
          })
        ).toBeTruthy();
      }
      const localized = localizedAppsList(
        (key) => t(key as Parameters<typeof t>[0]),
        (key) => status(key as Parameters<typeof status>[0])
      );
      expect(
        localized.fields.map(({ key, options }) => ({
          key,
          values: options?.map(({ value }) => value),
        }))
      ).toEqual(
        APPS_LIST.fields.map(({ key, options }) => ({
          key,
          values: options?.map(({ value }) => value),
        }))
      );
      for (const definition of [APPS_LIST, localized]) {
        const state = parseListState(
          definition,
          "view=failing&status=ready&kind=service-worker&deploy=degraded&project=checkout&q=worker&page=2"
        );
        expect(
          appsPageVariables({ ...state, filters: effectiveFilters(definition, state) })
        ).toMatchObject({
          filter: {
            failing: true,
            status: ["READY"],
            kind: ["service-worker"],
            deploy: ["DEGRADED"],
            project: ["checkout"],
          },
          page: 2,
          search: "worker",
        });
      }
      expect(t("bulk.failed", { action: "ACTION", failedSlugs: "checkout-worker" })).toContain(
        "checkout-worker"
      );
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
