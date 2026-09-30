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

import { FRAME } from "./app-frame.fixtures";
import { AppFrame } from "./AppFrame";
import { AppTabsView } from "./AppTabs";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}

describe("translated app navigation and load states", () => {
  it("renders Japanese tab labels with the same active logs link", () => {
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale="ja" messages={ja} onError={onError}>
        <AppTabsView slug="checkout" basePath="/apps" pathname="/apps/checkout/logs" />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("navigation", { name: "アプリのセクション" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ログとメトリクス" })).toHaveAttribute(
      "href",
      "/apps/checkout/logs"
    );
    expect(screen.getByRole("link", { name: "ログとメトリクス" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(screen.getByRole("link", { name: "アクセス" })).toHaveAttribute(
      "href",
      "/apps/checkout/access"
    );
    expect(onError).not.toHaveBeenCalled();
  });

  it("uses a translated French retry and preserves the query error and callback", () => {
    const onRetry = vi.fn();
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale="fr" messages={fr} onError={onError}>
        <AppFrame {...FRAME} app={null} error="NETWORK_UNAVAILABLE" onRetry={onRetry}>
          body
        </AppFrame>
      </NextIntlClientProvider>
    );
    expect(screen.getByText("Impossible de charger cette application")).toBeInTheDocument();
    expect(screen.getByText("NETWORK_UNAVAILABLE")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Réessayer" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("body")).not.toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(Object.entries(catalogs))(
    "renders every %s frame/tab/common message including prior missing keys",
    (locale, messages) => {
      const onError = vi.fn();
      const copy = {
        frame: messages.apps.frame,
        tabs: messages.apps.tabs,
        common: messages.apps.common,
      };
      const t = createTranslator({ locale, messages: copy, onError });
      for (const key of leaves({
        frame: en.apps.frame,
        tabs: en.apps.tabs,
        common: en.apps.common,
      }))
        expect(
          t(key as Parameters<typeof t>[0], {
            count: 7,
            cluster: "edge-gke",
            slug: "checkout",
            name: "Checkout",
          })
        ).toBeTruthy();
      expect(t("frame.clusterTitle", { cluster: "edge-gke" })).toContain("edge-gke");
      expect(t("common.notFoundSlug", { slug: "checkout" })).toContain("checkout");
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
