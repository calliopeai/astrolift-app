import { readFileSync } from "node:fs";
import path from "node:path";
import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";

type Messages = { [key: string]: string | Messages };
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
function leaves(messages: Messages, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(messages).flatMap(([key, value]) =>
      typeof value === "string"
        ? [[prefix + key, value]]
        : Object.entries(leaves(value, `${prefix}${key}.`))
    )
  );
}
function argumentsOf(message: string): string[] {
  return parse(message)
    .flatMap((element) => {
      if (element.type === TYPE.literal) return [];
      expect(element.type).toBe(TYPE.argument);
      return element.type === TYPE.argument ? [element.value] : [];
    })
    .sort();
}
const values = {
  page: 3,
  total: 8,
  name: "endpoint-guid-label",
  chars: 4000,
  tokens: 128,
  seconds: 30,
  latency: 250,
  ok: 2,
};
const parameterizedKeys = ["page", "selected", "relayAdvisory", "observed", "batchCount"];

function Notices() {
  const t = useTranslations("playground");
  return (
    <section>
      <h1>{t("title")}</h1>
      <p>{t("singlePrompt")}</p>
      <p>{t("localOnly")}</p>
      <p>{t("relayAdvisory", values)}</p>
      <p role="status">{t("readiness.READY")}</p>
      <p role="alert">{t("errors.timedOut")}</p>
    </section>
  );
}

describe("real playground catalogues", () => {
  it.each(locales)("%s has complete keys and preserves bounded relay interpolation", (locale) => {
    const source = leaves(catalogs.en.playground);
    const translated = leaves(catalogs[locale].playground);
    expect(Object.keys(source)).toHaveLength(76);
    expect(Object.keys(translated).sort()).toEqual(Object.keys(source).sort());
    const onError = vi.fn();
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "playground",
      onError,
    });
    for (const [key, text] of Object.entries(source)) {
      expect(translated[key].trim(), key).not.toBe("");
      expect(argumentsOf(translated[key]), key).toEqual(argumentsOf(text));
      if (locale !== "en" && `${locale}:${key}` !== "pt-BR:prompt") {
        expect(translated[key], `${locale}:${key}`).not.toBe(text);
      }
      const rendered = t(key, values);
      expect(rendered).not.toBe(`playground.${key}`);
      expect(rendered).not.toMatch(/\{\w+\}/);
    }
    for (const key of parameterizedKeys) {
      const rendered = t(key, values);
      for (const name of argumentsOf(source[key])) {
        expect(rendered, `${locale}:${key}:${name}`).toContain(
          String(values[name as keyof typeof values])
        );
      }
    }
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(locales)("%s renders actual readiness and timeout notices without fallback", (locale) => {
    const onError = vi.fn();
    const { unmount } = render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={onError}
      >
        <Notices />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("heading").textContent).toBe(catalogs[locale].playground.title);
    expect(screen.getByRole("status").textContent).toBe(
      catalogs[locale].playground.readiness.READY
    );
    expect(screen.getByRole("alert").textContent).toBe(catalogs[locale].playground.errors.timedOut);
    expect(onError).not.toHaveBeenCalled();
    unmount();
  });
});
