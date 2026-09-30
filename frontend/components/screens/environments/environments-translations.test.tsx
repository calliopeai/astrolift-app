import { MockedProvider } from "@apollo/client/testing/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { userEvent } from "storybook/test";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

import { ENV_PROD, ENVIRONMENT, environmentsProps } from "./environments.fixtures";
import {
  ENVIRONMENTS_LIST,
  environmentsVariables,
  localizedEnvironmentsList,
} from "./environments-list";
import { EnvironmentDetail } from "./EnvironmentDetail";
import { EnvironmentsScreen, type EnvironmentsScreenProps } from "./EnvironmentsScreen";

const errorNotice = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: errorNotice } }));
beforeEach(() => errorNotice.mockClear());

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function Environments({ onPause }: Pick<EnvironmentsScreenProps, "onPause">) {
  const t = useTranslations("lists.environments");
  const list = useLocalListState(localizedEnvironmentsList(t));
  return (
    <EnvironmentsScreen
      {...environmentsProps({ rows: [ENV_PROD], totalCount: 1 })}
      list={list}
      onPause={onPause}
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

describe("translated environments", () => {
  it.each([
    ["fr", "Suspendre", "Suspendre l’environnement"],
    ["ja", "一時停止", "環境を一時停止"],
  ] as const)(
    "requires %s confirmation and keeps a failed pause available for same-target retry",
    async (locale, pause, confirm) => {
      const onError = vi.fn();
      const onPause = vi
        .fn()
        .mockRejectedValueOnce(new Error("OWNER_SCOPE_DENIED"))
        .mockResolvedValueOnce(undefined);
      render(
        <MockedProvider>
          <PermissionsProvider value={{ granted: new Set(["app.deploy"]), loading: false }}>
            <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
              <TooltipProvider>
                <Environments onPause={onPause} />
              </TooltipProvider>
            </NextIntlClientProvider>
          </PermissionsProvider>
        </MockedProvider>
      );
      const row = screen.getByRole("row", {
        name: (name) => name.includes(ENV_PROD.name) && name.includes(ENV_PROD.registeredAppSlug),
      });
      await userEvent.click(within(row).getByRole("button"));
      await userEvent.click(screen.getByRole("menuitem", { name: pause }));
      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).getByRole("heading")).toHaveTextContent(
        `${ENV_PROD.registeredAppSlug}/${ENV_PROD.name}`
      );
      expect(within(dialog).getByText(/423/)).toBeInTheDocument();
      expect(onPause).not.toHaveBeenCalled();
      fireEvent.click(within(dialog).getByRole("button", { name: confirm }));
      await waitFor(() => expect(onPause).toHaveBeenCalledExactlyOnceWith(ENV_PROD));
      await waitFor(() =>
        expect(errorNotice).toHaveBeenCalledExactlyOnceWith("OWNER_SCOPE_DENIED")
      );
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole("button", { name: confirm }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(onPause).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it("renders Korean environment detail while retaining actual settings and external targets", () => {
    const onError = vi.fn();
    render(
      <NextIntlClientProvider
        locale="ko"
        messages={ko}
        timeZone="UTC"
        now={new Date("2026-09-30T12:00:00Z")}
        onError={onError}
      >
        <TooltipProvider>
          <EnvironmentDetail {...ENVIRONMENT} />
        </TooltipProvider>
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("link", { name: "열기" })).toHaveAttribute("href", ENV_PROD.url);
    expect(screen.getByText("필요한 승인 수")).toBeInTheDocument();
    for (const setting of ENV_PROD.settings) {
      expect(screen.getByText(setting.key)).toBeInTheDocument();
      expect(screen.getByText(setting.value)).toBeInTheDocument();
    }
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(Object.entries(catalogs))(
    "renders all %s messages and preserves selected owner/kind query semantics",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.lists.environments, onError });
      for (const key of leaves(en.lists.environments))
        expect(
          t.rich(key as Parameters<typeof t.rich>[0], {
            app: "checkout",
            env: "production",
            slug: "checkout",
            id: "e-0001",
            action: "ACTION",
            status: "STATUS",
            name: () => "production",
          })
        ).toBeTruthy();
      const localized = localizedEnvironmentsList((key) => t(key as Parameters<typeof t>[0]));
      expect(localized.fields.map(({ key }) => key)).toEqual(
        ENVIRONMENTS_LIST.fields.map(({ key }) => key)
      );
      expect(localized.views.map(({ key, filters }) => ({ key, filters }))).toEqual(
        ENVIRONMENTS_LIST.views.map(({ key, filters }) => ({ key, filters }))
      );
      for (const definition of [ENVIRONMENTS_LIST, localized]) {
        const state = parseListState(definition, "view=mine&app=checkout&cluster=prod-west&page=2");
        expect(
          environmentsVariables("checkout", {
            ...state,
            filters: effectiveFilters(definition, state),
          })
        ).toMatchObject({
          appSlug: "checkout",
          filter: { owner: ["me"], app: ["checkout"], cluster: ["prod-west"] },
          page: 2,
        });
      }
      const previewState = parseListState(localized, "view=previews");
      expect(
        environmentsVariables(null, {
          ...previewState,
          filters: effectiveFilters(localized, previewState),
        }).filter
      ).toEqual({ kind: ["preview"] });
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
