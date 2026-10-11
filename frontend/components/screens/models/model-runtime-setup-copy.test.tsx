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
const keys = [
  "title",
  "declarationNotice",
  "loading",
  "retry",
  "unavailable",
  "declared",
  "unconfigured",
  "configure",
  "close",
  "image",
  "version",
  "packageVersion",
  "cpuRequestCeiling",
  "memoryRequestCeiling",
  "defaultMaxModelLen",
  "maxModelLenCeiling",
  "defaultMaxNumSeqs",
  "maxNumSeqsCeiling",
  "gpuCountCeiling",
  "architecture",
  "selectors",
  "selectorKey",
  "selectorValue",
  "removeSelector",
  "addSelector",
  "supportedDtypes",
  "defaultDtype",
  "hardwareCertified",
  "hardwareEvidence",
  "evidenceHelp",
  "attestation",
  "save",
  "saving",
  "saved",
  "savedRefreshFailed",
  "failed",
  "unconfirmed",
  "dtype",
  "maxModelLen",
  "maxNumSeqs",
  "servingHelp",
  "declaredDefault",
] as const;
function DeclarationCopy() {
  const t = useTranslations("models.shared.runtimeSetup");
  return (
    <section>
      <h1>{t("title")}</h1>
      <p>{t("declarationNotice")}</p>
      <label>
        <input type="checkbox" />
        {t("attestation")}
      </label>
      <p>{t("saved")}</p>
      <p>{t("unconfirmed")}</p>
    </section>
  );
}
describe("operator runtime declaration copy", () => {
  it.each(locales)("%s has exact keys, valid literal ICU and genuine localized copy", (locale) => {
    const messages = catalogs[locale].models.shared.runtimeSetup;
    expect(Object.keys(messages).sort()).toEqual([...keys].sort());
    const t = createTranslator({
      locale,
      messages,
      onError: (error) => {
        throw error;
      },
    });
    for (const key of keys) {
      expect(messages[key].trim(), key).not.toBe("");
      if (locale !== "en") expect(messages[key], key).not.toBe(en.models.shared.runtimeSetup[key]);
      expect(
        parse(messages[key]).every((node) => node.type === TYPE.literal),
        key
      ).toBe(true);
      expect(t(key), key).toBe(messages[key]);
    }
    expect(messages.version).toContain("vLLM");
  });
  it.each(locales)(
    "%s renders attestation and uncertain save outcome without fallback",
    (locale) => {
      const errors: Error[] = [];
      const copy = catalogs[locale].models.shared.runtimeSetup;
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          onError={(error) => errors.push(error)}
        >
          <DeclarationCopy />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("heading", { name: copy.title })).toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: copy.attestation })).toBeInTheDocument();
      expect(screen.getByText(copy.declarationNotice)).toBeInTheDocument();
      expect(screen.getByText(copy.saved)).toBeInTheDocument();
      expect(screen.getByText(copy.unconfirmed)).toBeInTheDocument();
      expect(errors).toEqual([]);
    }
  );
});
