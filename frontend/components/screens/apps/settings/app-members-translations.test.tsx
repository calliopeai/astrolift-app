import { MockedProvider } from "@apollo/client/testing/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  createTranslator,
  NextIntlClientProvider,
  useTranslations,
  type IntlError,
} from "next-intl";
import { userEvent } from "storybook/test";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

import { ACCESS_ROWS, membersProps } from "./app-access-members.fixtures";
import {
  APP_ACCESS_LIST,
  type AccessRow,
  localizedAppAccessList,
  viewSources,
} from "./app-access-rows";
import { AppMembersScreen, type AppMembersScreenProps } from "./AppMembersScreen";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const errorNotice = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: errorNotice } }));
beforeEach(() => errorNotice.mockClear());
function Members({ row, onRemove }: { row: AccessRow } & Pick<AppMembersScreenProps, "onRemove">) {
  const t = useTranslations("apps.members");
  const list = useLocalListState(localizedAppAccessList(t));
  return <AppMembersScreen {...membersProps(list, { rows: [row], totalCount: 1, onRemove })} />;
}
function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}

function translatedMembers(
  locale: keyof typeof catalogs,
  row: AccessRow,
  onRemove: AppMembersScreenProps["onRemove"],
  onError: (error: IntlError) => void
) {
  return (
    <MockedProvider>
      <PermissionsProvider
        value={{ granted: new Set(["org.manage_members", "app.update"]), loading: false }}
      >
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          now={new Date("2026-09-30T12:00:00Z")}
          onError={onError}
        >
          <TooltipProvider>
            <Members row={row} onRemove={onRemove} />
          </TooltipProvider>
        </NextIntlClientProvider>
      </PermissionsProvider>
    </MockedProvider>
  );
}

describe("translated people with app access", () => {
  it.each([
    ["fr", "binding", "Retirer le rôle", "Retirer le rôle"],
    ["ja", "team_share", "チームへの共有を終了", "共有を終了"],
  ] as const)(
    "confirms and retries the exact %s %s source after a rejected removal",
    async (locale, kind, remove, confirm) => {
      const row = ACCESS_ROWS.find(
        (r) => r.kind === kind && (r.kind !== "team_share" || !r.share.isHome)
      )!;
      const onRemove = vi
        .fn()
        .mockRejectedValueOnce(new Error("SOURCE_SCOPE_DENIED"))
        .mockResolvedValueOnce(undefined);
      const onError = vi.fn();
      render(translatedMembers(locale, row, onRemove, onError));
      const tableRow = screen.getByRole("row", {
        name: (name) => name.includes(row.principal.name) && name.includes(row.role.name),
      });
      await userEvent.click(within(tableRow).getByRole("button"));
      await userEvent.click(screen.getByRole("menuitem", { name: remove }));
      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).getByRole("heading")).toHaveTextContent(row.principal.name);
      expect(within(dialog).getByRole("heading")).toHaveTextContent(
        kind === "binding" ? row.role.slug : "checkout"
      );
      expect(onRemove).not.toHaveBeenCalled();
      fireEvent.click(within(dialog).getByRole("button", { name: confirm }));
      await waitFor(() => expect(onRemove).toHaveBeenCalledExactlyOnceWith(row));
      await waitFor(() =>
        expect(errorNotice).toHaveBeenCalledExactlyOnceWith("SOURCE_SCOPE_DENIED")
      );
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole("button", { name: confirm }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(onRemove).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it("labels the Spanish home-team share and keeps its removal disabled", async () => {
    const row = ACCESS_ROWS.find((r) => r.kind === "team_share" && r.share.isHome)!;
    const onRemove = vi.fn();
    const onError = vi.fn();
    render(translatedMembers("es", row, onRemove, onError));
    expect(screen.getByText("Equipo de origen")).toBeInTheDocument();
    const tableRow = screen.getByRole("row", {
      name: (name) => name.includes(row.principal.name) && name.includes(row.role.name),
    });
    await userEvent.click(within(tableRow).getByRole("button"));
    const remove = screen.getByRole("menuitem", {
      name: "Finalizar acceso compartido con el equipo",
    });
    expect(remove).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(remove);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onRemove).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(Object.entries(catalogs))(
    "renders all %s current/retained member copy with unchanged source view keys",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.apps.members, onError });
      for (const key of leaves(en.apps.members))
        expect(
          t.rich(key as Parameters<typeof t.rich>[0], {
            slug: "checkout",
            app: key === "summary" ? () => "checkout" : "checkout",
            target: "Payments",
            role: "app-viewer",
            team: "storefront",
          })
        ).toBeTruthy();
      // The summary uses a rich app tag, while the share warning uses a plain argument.
      expect(t("removeConfirm.shareTitle", { target: "Payments", app: "checkout" })).toContain(
        "checkout"
      );
      const localized = localizedAppAccessList((key) => t(key as Parameters<typeof t>[0]));
      expect(localized.views.map(({ key, filters }) => ({ key, filters }))).toEqual(
        APP_ACCESS_LIST.views.map(({ key, filters }) => ({ key, filters }))
      );
      expect(localized.views.map(({ key }) => viewSources(key))).toEqual([
        { bindings: true, shares: true },
        { bindings: true, shares: false },
        { bindings: true, shares: false },
        { bindings: false, shares: true },
      ]);
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
