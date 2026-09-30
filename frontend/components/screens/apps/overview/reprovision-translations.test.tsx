import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

import { REPROVISION } from "./app-overview-banners.fixtures";
import { ReprovisionCalloutView } from "./ReprovisionCallout";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}

describe("reprovision translations and unknown states", () => {
  it.each([
    [
      "fr",
      "Le provisionnement nécessite votre attention",
      "Reprovisionnez pour rétablir l’application.",
    ],
    ["ja", "プロビジョニングの確認が必要です", "再プロビジョニングして復旧してください。"],
  ] as const)(
    "uses a translated fallback for a future %s state with no missing-message error",
    (locale, headline, body) => {
      const onError = vi.fn();
      render(
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
          <ReprovisionCalloutView
            {...REPROVISION}
            reprovision={{ ...REPROVISION.reprovision, state: "future_state", reason: "" }}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(headline)).toBeInTheDocument();
      expect(screen.getByText(body)).toBeInTheDocument();
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it("keeps a rejected French reprovision open for a successful retry", async () => {
    const onConfirm = vi
      .fn()
      .mockRejectedValueOnce(new Error("not authorized"))
      .mockResolvedValueOnce(undefined);
    render(
      <NextIntlClientProvider locale="fr" messages={fr}>
        <ReprovisionCalloutView {...REPROVISION} onConfirm={onConfirm} />
      </NextIntlClientProvider>
    );
    fireEvent.click(screen.getByRole("button", { name: "Reprovisionner" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent("checkout");
    fireEvent.click(within(dialog).getByRole("button", { name: "Oui, reprovisionner" }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Oui, reprovisionner" })).toBeEnabled()
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Oui, reprovisionner" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(onConfirm).toHaveBeenCalledTimes(2);
  });

  it.each(Object.entries(catalogs))(
    "renders every %s reprovision message and preserves the app/error arguments",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.apps.detail.reprovision, onError });
      for (const key of leaves(en.apps.detail.reprovision))
        expect(
          t(key as Parameters<typeof t>[0], { minutes: 7, slug: "checkout", message: "DENIED" })
        ).toBeTruthy();
      expect(t("confirm.title", { slug: "checkout" })).toContain("checkout");
      expect(t("toast.partial", { message: "DENIED" })).toContain("DENIED");
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
