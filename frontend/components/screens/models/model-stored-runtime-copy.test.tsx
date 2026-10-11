import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { describe, expect, it } from "vitest";
import { locales } from "@/i18n/config";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const english = {
  keepStored: "Keep the stored value",
  storedValue: "Stored value: {value}",
  notRecorded: "Not recorded",
  preserveHelp:
    "Leaving a control blank preserves its stored value. Changes require current runtime admission and a model restart; saving does not prove readiness.",
};
function StoredCopy() {
  const t = useTranslations("models.shared.storedControls");
  return (
    <section>
      <label>
        <input />
        {t("keepStored")}
      </label>
      <p>{t("storedValue", { value: "float32" })}</p>
      <p>{t("notRecorded")}</p>
      <p>{t("preserveHelp")}</p>
    </section>
  );
}
describe("stored runtime controls copy", () => {
  it("matches the approved four-key English map", () => {
    expect(en.models.shared.storedControls).toEqual(english);
  });
  it.each(locales)("%s preserves exact keys and ICU arguments", (locale) => {
    const copy = catalogs[locale].models.shared.storedControls;
    expect(Object.keys(copy).sort()).toEqual(Object.keys(english).sort());
    const t = createTranslator({
      locale,
      messages: copy,
      onError: (error) => {
        throw error;
      },
    });
    for (const key of Object.keys(english) as (keyof typeof english)[]) {
      expect(copy[key].trim()).not.toBe("");
      if (locale !== "en") expect(copy[key]).not.toBe(english[key]);
      const argumentsFound = parse(copy[key]).filter((node) => node.type !== TYPE.literal);
      expect(argumentsFound).toEqual(
        key === "storedValue" ? [{ type: TYPE.argument, value: "value" }] : []
      );
      expect(t(key, { value: "float32" })).toBe(copy[key].replace("{value}", "float32"));
    }
  });
  it.each(locales)("%s renders preservation and readiness limits without fallback", (locale) => {
    const errors: Error[] = [];
    const copy = catalogs[locale].models.shared.storedControls;
    render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        onError={(error) => errors.push(error)}
      >
        <StoredCopy />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("textbox", { name: copy.keepStored })).toBeInTheDocument();
    expect(screen.getByText(copy.storedValue.replace("{value}", "float32"))).toBeInTheDocument();
    expect(screen.getByText(copy.notRecorded)).toBeInTheDocument();
    expect(screen.getByText(copy.preserveHelp)).toBeInTheDocument();
    expect(errors).toEqual([]);
  });
});
