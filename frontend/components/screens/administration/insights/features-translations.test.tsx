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

import { FeaturesScreen } from "./FeaturesScreen";
import { FEATURES } from "./fixtures";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };

describe("feature controls in the selected language", () => {
  it.each([
    ["es", "Cambiar admin.cost_enabled", "Activar función", "Activar Admin · Cost Enabled"],
    [
      "fr",
      "Basculer admin.cost_enabled",
      "Activer la fonctionnalité",
      "Activer Admin · Cost Enabled",
    ],
    [
      "ja",
      "admin.cost_enabledを切り替える",
      "機能を有効にする",
      "Admin · Cost Enabledを有効にしますか",
    ],
  ] as const)(
    "keeps a failed %s confirmation open and retries the same flag",
    async (locale, toggle, confirm, heading) => {
      const onError = vi.fn();
      const toggleFlag = vi
        .fn()
        .mockRejectedValueOnce(new Error("maintenance"))
        .mockResolvedValueOnce(undefined);
      render(
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
          <FeaturesScreen {...FEATURES} toggleFlag={toggleFlag} />
        </NextIntlClientProvider>
      );
      fireEvent.click(screen.getByRole("switch", { name: toggle }));
      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).getByRole("heading").textContent).toContain(heading);
      fireEvent.click(within(dialog).getByRole("button", { name: confirm }));
      await waitFor(() => expect(toggleFlag).toHaveBeenCalledTimes(1));
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      await waitFor(() =>
        expect(within(dialog).getByRole("button", { name: confirm })).toBeEnabled()
      );
      fireEvent.click(within(dialog).getByRole("button", { name: confirm }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(toggleFlag).toHaveBeenNthCalledWith(1, FEATURES.runtimeFlags[1]);
      expect(toggleFlag).toHaveBeenNthCalledWith(2, FEATURES.runtimeFlags[1]);
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it.each(Object.entries(catalogs))(
    "renders every %s feature message, preserving arguments and rich tags",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({
        locale,
        messages,
        namespace: "administration.features",
        onError,
      });
      for (const key of Object.keys(en.administration.features) as Array<
        keyof typeof en.administration.features
      >) {
        const translated = t.rich(key, {
          key: "admin.cost_enabled",
          env: "ASTROLIFT_COST",
          code: (chunks) => chunks,
        });
        expect(translated).toBeTruthy();
        if (locale !== "en")
          expect(messages.administration.features[key]).not.toBe(en.administration.features[key]);
      }
      expect(t("requiresRedeployLabel", { key: "gpu.model_hosting" })).toContain(
        "gpu.model_hosting"
      );
      expect(
        t.rich("requiresRedeploy", { env: "ASTROLIFT_GPU_MODEL_HOSTING", code: (chunks) => chunks })
      ).toContain("ASTROLIFT_GPU_MODEL_HOSTING");
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
