import { readFileSync } from "node:fs";
import path from "node:path";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { DeployTokensScreen } from "@/components/screens/apps/secrets/DeployTokensScreen";
import {
  TOKENS_SCREEN,
  TOKEN_REVEAL,
} from "@/components/screens/apps/secrets/app-secrets-tokens.fixtures";
import { APP_DEPLOY_TOKENS_LIST } from "@/components/screens/apps/secrets/deploy-tokens-list";
import { useLocalListState } from "@/components/list/use-list-state";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/apps/storefront/tokens",
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));
vi.mock("@/components/PageShell", () => ({
  PageShell: ({
    title,
    children,
    actions,
  }: {
    title: ReactNode;
    children: ReactNode;
    actions: ReactNode;
  }) => (
    <div>
      <h1>{title}</h1>
      {actions}
      {children}
    </div>
  ),
}));

const locales = ["en", "es", "fr", "de", "pt-BR", "ja", "ko", "zh-Hans"];
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
function leaves(node: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(node).flatMap(([key, value]) =>
      typeof value === "string"
        ? [[prefix + key, value]]
        : Object.entries(leaves(value as Record<string, unknown>, `${prefix}${key}.`))
    )
  );
}
function argumentsOf(ast: MessageFormatElement[]): unknown[] {
  return ast.flatMap<unknown>((node) => {
    if (node.type === 0 || node.type === 7) return [];
    if (node.type === 6 || node.type === 5)
      return [
        [
          node.type,
          node.value,
          Object.fromEntries(
            Object.entries(node.options).map(([key, option]) => [key, argumentsOf(option.value)])
          ),
        ],
      ];
    if (node.type === 8) return [[node.type, node.value, argumentsOf(node.children)]];
    return [[node.type, node.value]];
  });
}
const source = leaves(catalogs.en.apps.tokens);

// Same spelling is meaningful for these cognates and the technical IP label.
const unchanged = new Set([
  "de:columns.name",
  "de:createSheet.name",
  "fr:columns.actions",
  ...locales.map((locale) => `${locale}:lastUsedCell.ip`),
]);
describe("deploy-token translations", () => {
  it.each(locales)("%s covers the complete namespace with matching ICU arguments", (locale) => {
    const translated = leaves(catalogs[locale].apps.tokens);
    expect(Object.keys(translated).sort()).toEqual(Object.keys(source).sort());
    for (const [key, text] of Object.entries(source)) {
      expect(argumentsOf(parse(translated[key])), `${locale}:${key}`).toEqual(
        argumentsOf(parse(text))
      );
      if (locale !== "en" && !unchanged.has(`${locale}:${key}`))
        expect(translated[key], `${locale}:${key}`).not.toBe(text);
    }
  });

  it.each(locales)(
    "%s renders actual token controls, dates and exact reveal duration",
    (locale) => {
      const messages = catalogs[locale];
      const tr = createTranslator({ locale, messages, namespace: "apps.tokens" });
      function View() {
        const list = useLocalListState(APP_DEPLOY_TOKENS_LIST);
        return (
          <DeployTokensScreen
            {...TOKENS_SCREEN}
            list={list}
            tabs={null}
            reveal={{ ...TOKEN_REVEAL, rotationGraceSeconds: 90 }}
          />
        );
      }
      const view = render(
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          <View />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("heading", { name: tr("title"), hidden: true })).toBeVisible();
      expect(screen.getByPlaceholderText(tr("searchPlaceholder"))).toBeInTheDocument();
      const dialog = screen.getByRole("alertdialog");
      const duration = `${tr("graceDuration.minutes", { count: 1 })} ${tr("graceDuration.seconds", { count: 30 })}`;
      expect(
        within(dialog).getByText(tr("revealDialog.graceNote", { window: duration }))
      ).toBeVisible();
      fireEvent.click(within(dialog).getByRole("button", { name: tr("revealDialog.done") }));
      const expires = TOKENS_SCREEN.rows.find((token) => token.expiresAt)?.expiresAt;
      expect(expires).toBeTruthy();
      const date = new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      }).format(new Date(expires!));
      expect(screen.getAllByText(date).length).toBeGreaterThan(0);
      const lastUsed = TOKENS_SCREEN.rows.find((token) => token.lastUsedAt)?.lastUsedAt;
      expect(lastUsed).toBeTruthy();
      const stamp = new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "numeric",
        timeZoneName: "short",
        timeZone: "UTC",
      }).format(new Date(lastUsed!));
      expect(screen.getAllByText(stamp).length).toBeGreaterThan(0);
      view.unmount();
    }
  );
});
