import { readFileSync } from "node:fs";
import path from "node:path";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { AccessCardView, AccessEditorView } from "@/components/screens/apps/security/AccessCard";
import {
  ACCESS_RESTRICTED,
  EDITOR_CHANGED,
} from "@/components/screens/apps/security/app-security-previews.fixtures";
import { AppSecurityScreen } from "@/components/screens/apps/security/AppSecurityScreen";
import {
  SECURITY,
  SIGNING_EVENT,
} from "@/components/screens/apps/security/app-security-previews.fixtures";
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
  return ast
    .flatMap<unknown>((node) => {
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
    })
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
}

// Same spelling is meaningful for these cognates and the technical IP label.
const unchanged = new Set([
  "de:columns.name",
  "de:createSheet.name",
  "fr:columns.actions",
  ...locales.map((locale) => `${locale}:lastUsedCell.ip`),
]);
describe("deploy-token translations", () => {
  it.each(locales)("%s covers the complete namespace with matching ICU arguments", (locale) => {
    const source = leaves(catalogs.en.apps.tokens);
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

describe("app supply-chain translations", () => {
  const same = new Set(["sbom.title", "scan.columns.cve", "fr:sbom.format", "de:sbom.format"]);
  it.each(locales)(
    "%s translates the whole security namespace and preserves ICU inputs",
    (locale) => {
      const source = leaves(catalogs.en.apps.security);
      const translated = leaves(catalogs[locale].apps.security);
      expect(Object.keys(translated).sort()).toEqual(Object.keys(source).sort());
      for (const [key, text] of Object.entries(source)) {
        expect(argumentsOf(parse(translated[key])), `${locale}:${key}`).toEqual(
          argumentsOf(parse(text))
        );
        if (locale !== "en" && !same.has(key) && !same.has(`${locale}:${key}`))
          expect(translated[key], `${locale}:${key}`).not.toBe(text);
        for (const technical of [
          "image.signed",
          "image.scanned",
          "sbom.generated",
          "image_signing",
          "image_scan",
          "sbom_multiarch",
          "RegisteredApp.securityPolicy",
          "Sigstore",
          "Cosign",
          "cosign",
          "Rekor",
          "promote-deploy",
        ]) {
          if (text.includes(technical))
            expect(translated[key], `${locale}:${key}`).toContain(technical);
        }
      }
    }
  );
});

describe("translated supply-chain controls", () => {
  it.each(locales)(
    "%s keeps threshold values numeric and shows translated severity and timestamps",
    async (locale) => {
      const messages = catalogs[locale];
      const t = createTranslator({ locale, messages, namespace: "apps.security" });
      const save = vi.fn(async () => true);
      const view = render(
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          <AppSecurityScreen {...SECURITY} access={null} tabs={null} onSavePolicy={save} />
        </NextIntlClientProvider>
      );
      const threshold = screen.getByRole("combobox", { name: t("policy.highThreshold") });
      fireEvent.change(threshold, { target: { value: "10" } });
      expect(
        within(threshold).getByRole("option", { name: t("policy.thresholdOption", { count: 10 }) })
      ).toHaveValue("10");
      fireEvent.click(screen.getByRole("button", { name: t("policy.save") }));
      await waitFor(() =>
        expect(save).toHaveBeenCalledWith(expect.objectContaining({ blockOnHighCveThreshold: 10 }))
      );
      expect(screen.getAllByText(t("scan.critical")).length).toBeGreaterThan(0);
      expect(screen.queryByText("critical", { exact: true })).not.toBeInTheDocument();
      const signedAt = new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "numeric",
        timeZoneName: "short",
        timeZone: "UTC",
      }).format(new Date(SIGNING_EVENT.occurredAt));
      expect(screen.getAllByText(signedAt).length).toBeGreaterThan(0);
      expect(screen.getByPlaceholderText(t("scan.searchPlaceholder"))).toBeInTheDocument();
      view.unmount();
    }
  );

  it("changes existing findings labels when the active locale changes", () => {
    const view = render(
      <NextIntlClientProvider locale="en" messages={catalogs.en} timeZone="UTC">
        <AppSecurityScreen {...SECURITY} access={null} tabs={null} />
      </NextIntlClientProvider>
    );
    expect(screen.getByPlaceholderText("Search CVEs, packages…")).toBeInTheDocument();
    view.rerender(
      <NextIntlClientProvider locale="ja" messages={catalogs.ja} timeZone="UTC">
        <AppSecurityScreen {...SECURITY} access={null} tabs={null} />
      </NextIntlClientProvider>
    );
    expect(screen.queryByPlaceholderText("Search CVEs, packages…")).not.toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(catalogs.ja.apps.security.scan.searchPlaceholder)
    ).toBeInTheDocument();
    view.unmount();
  });
});

describe("translated edge-access editor", () => {
  it.each(locales)(
    "%s preserves identities and formats preview counts and validation",
    (locale) => {
      const messages = catalogs[locale];
      const t = createTranslator({ locale, messages, namespace: "apps.security.access" });
      const setUsers = vi.fn();
      const setGroups = vi.fn();
      const preview = {
        ...EDITOR_CHANGED.preview!,
        allowed: 1,
        total: 1234,
        losing: ["unchanged@example.com"],
      };
      const view = render(
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          <AccessCardView
            {...ACCESS_RESTRICTED}
            editor={
              <AccessEditorView
                {...EDITOR_CHANGED}
                preview={preview}
                setUsers={setUsers}
                setGroups={setGroups}
              />
            }
          />
        </NextIntlClientProvider>
      );
      expect(
        screen.getByText(t("preview", { allowed: 1, total: 1234 }).replace(/\s+/g, " "))
      ).toBeVisible();
      expect(
        screen.getByText(t("losingAccess", { count: 1, users: "unchanged@example.com" }))
      ).toBeVisible();
      const email = screen.getByLabelText(t("usersEmail"));
      fireEvent.change(email, { target: { value: "invalid-address" } });
      fireEvent.keyDown(email, { key: "Enter" });
      expect(screen.getByText(t("invalidEmail"))).toBeVisible();
      expect(setUsers).not.toHaveBeenCalled();
      const group = EDITOR_CHANGED.groups[0];
      fireEvent.click(screen.getByRole("button", { name: t("remove", { value: group }) }));
      expect(setGroups).toHaveBeenCalledWith(
        EDITOR_CHANGED.groups.filter((value) => value !== group)
      );
      view.unmount();
    }
  );
});

it("does not invent edge-access preview counts when the API omits them", () => {
  const view = render(
    <NextIntlClientProvider locale="en" messages={catalogs.en} timeZone="UTC">
      <AccessEditorView
        {...EDITOR_CHANGED}
        preview={{ ...EDITOR_CHANGED.preview!, allowed: undefined, total: undefined }}
      />
    </NextIntlClientProvider>
  );
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.queryByText(/undefined|NaN/)).not.toBeInTheDocument();
  view.unmount();
});

describe("app secret translations", () => {
  const same = new Set([
    "attached.noTeam",
    "attached.noPrefix",
    "attached.keyCountUnknown",
    "attached.perEnvBadge",
    "setVia.cli",
    "es:source.literal",
    "fr:title",
    "fr:columns.source",
    "fr:columns.actions",
    "fr:scope.production",
    "de:attached.columns.team",
    "de:history.system",
    ...["es", "fr", "de", "pt-BR"].map((locale) => `${locale}:setVia.web`),
  ]);
  it.each(locales)(
    "%s covers the full secrets namespace, including plural and technical parameters",
    (locale) => {
      const source = leaves(catalogs.en.apps.secrets);
      const translated = leaves(catalogs[locale].apps.secrets);
      expect(Object.keys(translated).sort()).toEqual(Object.keys(source).sort());
      for (const [key, text] of Object.entries(source)) {
        expect(argumentsOf(parse(translated[key])), `${locale}:${key}`).toEqual(
          argumentsOf(parse(text))
        );
        if (locale !== "en" && !same.has(key) && !same.has(`${locale}:${key}`))
          expect(translated[key], `${locale}:${key}`).not.toBe(text);
        for (const technical of [
          "[env]",
          "manifest_raw_staged",
          ".env",
          "KEY=value",
          "CLI",
          "Enter",
          "Esc",
        ]) {
          // Keyboard labels are localized where the locale uses a translated key name.
          if (technical === "Enter" || technical === "Esc") continue;
          if (text.includes(technical))
            expect(translated[key], `${locale}:${key}`).toContain(technical);
        }
      }
    }
  );
});

describe("app preview translations", () => {
  const same = new Set([
    "columns.ttl",
    "es:columns.hostname",
    "de:columns.hostname",
    "pt-BR:columns.hostname",
    "de:columns.status",
    "pt-BR:columns.status",
    "fr:columns.actions",
    "es:manual",
    "pt-BR:manual",
    "pt-BR:columns.pr",
    "pt-BR:columns.prBranch",
    "es:countdown.daysHours",
    "pt-BR:countdown.daysHours",
  ]);
  it.each(locales)("%s covers the entire preview namespace without English seed copy", (locale) => {
    const source = leaves(catalogs.en.apps.previews);
    const translated = leaves(catalogs[locale].apps.previews);
    expect(Object.keys(translated).sort()).toEqual(Object.keys(source).sort());
    for (const [key, text] of Object.entries(source)) {
      expect(argumentsOf(parse(translated[key])), `${locale}:${key}`).toEqual(
        argumentsOf(parse(text))
      );
      if (locale !== "en" && !same.has(key) && !same.has(`${locale}:${key}`))
        expect(translated[key], `${locale}:${key}`).not.toBe(text);
      for (const identifier of ["pr-N.", "TTL", "SKU"])
        if (text.includes(identifier))
          expect(translated[key], `${locale}:${key}`).toContain(identifier);
    }
  });
});
