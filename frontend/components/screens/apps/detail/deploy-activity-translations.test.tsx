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

import { ACTIVITY } from "./app-detail-shell.fixtures";
import { DeployActivityStrip } from "./DeployActivityStrip";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };

describe("translated deployment activity", () => {
  it.each([
    ["fr", "Tout voir", "Échec"],
    ["ko", "모두 보기", "실패"],
  ] as const)(
    "retains actual %s tile targets and translated status descriptions",
    (locale, all, failed) => {
      const onError = vi.fn();
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          now={new Date()}
          onError={onError}
        >
          <DeployActivityStrip {...ACTIVITY} />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("link", { name: all })).toHaveAttribute(
        "href",
        `${ACTIVITY.appHref}/deployments`
      );
      const deployment = ACTIVITY.deployments.find((d) => d.status === "failed")!;
      const tile = screen.getByRole("link", {
        name: (label) =>
          label.includes(failed) && label.startsWith(deployment.imageTag.slice(0, 10)),
      });
      expect(tile).toHaveAttribute(
        "href",
        `${ACTIVITY.appHref}/deployments?deployment=${deployment.id}`
      );
      expect(tile.getAttribute("aria-label")).toContain(deployment.imageTag.slice(0, 10));
      expect(tile.getAttribute("title")).toContain(deployment.environmentName);
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it.each(Object.entries(catalogs))(
    "renders the %s strip's rich numeric messages",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({
        locale,
        messages: messages.apps.detail.deployActivity,
        onError,
      });
      for (const key of Object.keys(en.apps.detail.deployActivity) as Array<
        keyof typeof en.apps.detail.deployActivity
      >)
        expect(t.rich(key, { count: 2, limit: 24, mono: (chunks) => chunks })).toBeTruthy();
      expect(t.rich("latest", { limit: 24, mono: (chunks) => chunks })).toContain("24");
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
