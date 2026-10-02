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

import { PREVIEWS, previewDetailProps, previewsProps } from "./previews.fixtures";
import { PREVIEWS_LIST, localizedPreviewsList, previewsVariables } from "./previews-list";
import { PreviewDetailScreen } from "./PreviewDetail";
import { PreviewsScreen, type PreviewsScreenProps } from "./PreviewsScreen";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const errorNotice = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: errorNotice } }));
beforeEach(() => errorNotice.mockClear());
function Previews({ tearDown }: Pick<PreviewsScreenProps, "tearDown">) {
  const t = useTranslations("lists.previews");
  const list = useLocalListState(localizedPreviewsList(t));
  return (
    <PreviewsScreen
      {...previewsProps({ rows: [PREVIEWS[0]], totalCount: 1 })}
      list={list}
      tearDown={tearDown}
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

describe("translated global previews", () => {
  it.each([
    ["fr", "Supprimer l’aperçu"],
    ["ja", "プレビューを削除"],
  ] as const)(
    "requires %s teardown confirmation and retains the actual preview across rejection/retry",
    async (locale, remove) => {
      const onError = vi.fn();
      const tearDown = vi
        .fn()
        .mockRejectedValueOnce(new Error("PREVIEW_SCOPE_DENIED"))
        .mockResolvedValueOnce(undefined);
      render(
        <MockedProvider>
          <PermissionsProvider value={{ granted: new Set(["app.deploy"]), loading: false }}>
            <NextIntlClientProvider
              locale={locale}
              messages={catalogs[locale]}
              timeZone="UTC"
              now={new Date("2026-09-30T12:00:00Z")}
              onError={onError}
            >
              <TooltipProvider>
                <Previews tearDown={tearDown} />
              </TooltipProvider>
            </NextIntlClientProvider>
          </PermissionsProvider>
        </MockedProvider>
      );
      const preview = PREVIEWS[0];
      const row = screen.getByRole("row", {
        name: (name) =>
          name.includes(preview.registeredAppSlug) && name.includes(preview.namespace),
      });
      await userEvent.click(within(row).getByRole("button"));
      await userEvent.click(screen.getByRole("menuitem", { name: remove }));
      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).getByRole("heading")).toHaveTextContent(`#${preview.prNumber}`);
      for (const value of [preview.hostname, preview.namespace, preview.branch])
        expect(dialog).toHaveTextContent(value);
      expect(tearDown).not.toHaveBeenCalled();
      fireEvent.click(within(dialog).getByRole("button", { name: remove }));
      await waitFor(() => expect(tearDown).toHaveBeenCalledExactlyOnceWith(preview));
      await waitFor(() =>
        expect(errorNotice).toHaveBeenCalledExactlyOnceWith("PREVIEW_SCOPE_DENIED")
      );
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole("button", { name: remove }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(tearDown).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it("formats German resources and USD while preserving caveats and loading the exact failed-preview logs", () => {
    const onError = vi.fn();
    const preview = {
      ...PREVIEWS[3],
      environmentStatus: "available",
      environment: {
        ...PREVIEWS[0].environment!,
        previewId: PREVIEWS[3].id,
        previewVersion: PREVIEWS[3].version,
        appSlug: PREVIEWS[3].registeredAppSlug,
        namespace: PREVIEWS[3].namespace,
      },
    };
    const onLoadLogs = vi.fn();
    render(
      <NextIntlClientProvider
        locale="de"
        messages={de}
        timeZone="UTC"
        now={new Date("2026-09-30T12:00:00Z")}
        onError={onError}
      >
        <TooltipProvider>
          <PreviewDetailScreen {...previewDetailProps({ preview, onLoadLogs })} />
        </TooltipProvider>
      </NextIntlClientProvider>
    );
    expect(screen.getByText("2,50")).toBeInTheDocument();
    expect(screen.getByText("4,00 GiB")).toBeInTheDocument();
    expect(screen.getByText("9,87 $", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("ungefähr")).toBeInTheDocument();
    for (const note of preview.estimatedCostNotes)
      expect(screen.getByText(note)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Logs öffnen" }));
    expect(onLoadLogs).toHaveBeenCalledOnce();
    expect(document.querySelector(`a[href="/apps/${preview.registeredAppSlug}/logs"]`)).toBeNull();
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(Object.entries(catalogs))(
    "renders all %s preview messages with the literal hostname pattern and stable status/owner filters",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.lists.previews, onError });
      for (const key of leaves(en.lists.previews))
        expect(
          t.rich(key as Parameters<typeof t.rich>[0], {
            count: 20,
            pr: 412,
            namespace: "preview-checkout-412",
            hostname: "pr-412-checkout.example",
            branch: "release/mobile",
            id: "p-0001",
          })
        ).toBeTruthy();
      expect(t("description")).toContain("pr-<n>-<app>.pr.<org>.<base-zone>");
      const localized = localizedPreviewsList((key) => t(key as Parameters<typeof t>[0]));
      expect(
        localized.fields.map(({ key, options }) => ({
          key,
          values: options?.map(({ value }) => value),
        }))
      ).toEqual(
        PREVIEWS_LIST.fields.map(({ key, options }) => ({
          key,
          values: options?.map(({ value }) => value),
        }))
      );
      for (const definition of [PREVIEWS_LIST, localized]) {
        const state = parseListState(
          definition,
          "view=mine&status=running&app=checkout&after=page2"
        );
        expect(previewsVariables(effectiveFilters(definition, state), state)).toMatchObject({
          appSlug: "checkout",
          filter: { openedBy: ["me"], status: ["running"] },
          after: "page2",
        });
      }
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
