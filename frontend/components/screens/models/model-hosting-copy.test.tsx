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
  "sourceStep",
  "placementStep",
  "checksStep",
  "host",
  "continue",
  "sourceHelp",
  "connect",
  "connectionName",
  "token",
  "tokenHelp",
  "createToken",
  "connecting",
  "connected",
  "connectFailed",
  "connection",
  "anonymous",
  "connectionsUnavailable",
  "connectionPage",
  "older",
  "newer",
  "retry",
  "manualTitle",
  "repository",
  "revision",
  "useSource",
  "invalidSource",
  "adminChecking",
  "adminRequired",
  "adminUnavailable",
  "accessTitle",
  "accessChecking",
  "accessConfirmed",
  "accessUnknown",
  "accessHelp",
  "openModel",
  "licenseTitle",
  "licenseReview",
  "licenseHelp",
  "runtimeTitle",
  "runtimeHelp",
  "hardwareTitle",
  "hardwareHelp",
  "runtimePending",
  "hardwarePending",
  "changed",
  "savedRefreshFailed",
  "publicCatalog",
  "changeSource",
  "connectionsLoading",
  "noConnections",
  "connectionSaved",
  "supportedArchitectures",
  "clusterConfiguration",
  "reviewResourceRequests",
  "hardwareConfiguration",
] as const;

function ConnectedCopy() {
  const t = useTranslations("models.shared.hosting");
  return (
    <section>
      <h1>{t("title")}</h1>
      <p>{t("connected", { account: "reader-verified" })}</p>
      <p>{t("connectionPage", { page: 2, pages: 7 })}</p>
      <p>{t("connectionsLoading")}</p>
      <p>{t("noConnections")}</p>
      <p>{t("connectFailed")}</p>
      <p>{t("connectionSaved")}</p>
    </section>
  );
}

describe("model hosting copy contract", () => {
  it.each(locales)("%s has every new key and the exact documented ICU arguments", (locale) => {
    const messages = catalogs[locale].models.shared.hosting;
    expect(Object.keys(messages).sort()).toEqual([...keys].sort());
    for (const key of keys) {
      const message = messages[key];
      expect(message.trim()).not.toBe("");
      if (locale !== "en") expect(message, key).not.toBe(en.models.shared.hosting[key]);
      const elements = parse(message);
      const argumentsFound = elements.flatMap((node) => {
        if (node.type === TYPE.literal) return [];
        expect(node.type, `${locale}.${key}`).toBe(TYPE.argument);
        return "value" in node ? [node.value] : [];
      });
      const expected =
        key === "connected" ? ["account"] : key === "connectionPage" ? ["page", "pages"] : [];
      expect(argumentsFound.sort(), `${locale}.${key}`).toEqual(expected.sort());
      const t = createTranslator({
        locale,
        messages,
        onError: (error) => {
          throw error;
        },
      });
      const formatted = t(key, { account: "reader-verified", page: 2, pages: 7 });
      expect(formatted).not.toMatch(/\{(?:account|page|pages)\}/);
      expect(formatted).not.toBe(`models.shared.hosting.${key}`);
    }
    expect(messages.runtimeHelp).toContain("vLLM 0.15.1");
    expect(messages.runtimeHelp).toContain("Python");
    expect(messages.connect).toContain("Hugging Face");
  });

  it.each(locales)(
    "%s renders localized connected and pagination facts without English fallback",
    (locale) => {
      const failures: Error[] = [];
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          onError={(error) => failures.push(error)}
        >
          <ConnectedCopy />
        </NextIntlClientProvider>
      );
      const copy = catalogs[locale].models.shared.hosting;
      expect(screen.getByRole("heading", { name: copy.title })).toBeInTheDocument();
      expect(
        screen.getByText(copy.connected.replace("{account}", "reader-verified"))
      ).toBeInTheDocument();
      expect(
        screen.getByText(copy.connectionPage.replace("{page}", "2").replace("{pages}", "7"))
      ).toBeInTheDocument();
      expect(screen.getByText(copy.connectionsLoading)).toBeInTheDocument();
      expect(screen.getByText(copy.noConnections)).toBeInTheDocument();
      expect(screen.getByText(copy.connectFailed)).toBeInTheDocument();
      expect(screen.getByText(copy.connectionSaved)).toBeInTheDocument();
      expect(failures).toEqual([]);
    }
  );
});

const localImportKeys = [
  "selectSource",
  "huggingFace",
  "localSource",
  "title",
  "description",
  "name",
  "files",
  "limits",
  "import",
  "hashing",
  "uploading",
  "verifying",
  "cancel",
  "canceled",
  "browserUnsupported",
  "invalidFiles",
  "invalidSizes",
  "failed",
  "saved",
  "verified",
  "savedRefreshFailed",
  "select",
  "statusUploading",
  "statusVerified",
  "empty",
  "emptyDescription",
  "search",
  "all",
  "sourceName",
  "state",
  "summary",
  "manifest",
  "retry",
  "stopped",
  "changeSource",
  "localLicense",
  "servedIdentifier",
  "manifestRevision",
  "localLicenseReview",
] as const;

function importArguments(locale: string) {
  return {
    name: "weights-00001.safetensors",
    percent: new Intl.NumberFormat(locale).format(62.5),
    index: 2,
    count: 3,
    bytes: new Intl.NumberFormat(locale).format(137625600),
  };
}

function LocalImportCopy({ locale }: { locale: string }) {
  const t = useTranslations("models.shared.localImport");
  const args = importArguments(locale);
  return (
    <section>
      <h2>{t("title")}</h2>
      <p>{t("hashing", args)}</p>
      <p>{t("uploading", args)}</p>
      <p>{t("summary", args)}</p>
      <p>{t("failed")}</p>
      <p>{t("saved")}</p>
      <p>{t("verified")}</p>
      <p>{t("canceled")}</p>
      <p>{t("localLicense")}</p>
      <label>
        <input type="checkbox" />
        {t("localLicenseReview")}
      </label>
      <button>{t("cancel")}</button>
    </section>
  );
}

describe("local model import copy contract", () => {
  it.each(locales)("%s has the exact keys and formats every documented ICU argument", (locale) => {
    const messages = catalogs[locale].models.shared.localImport;
    expect(Object.keys(messages).sort()).toEqual([...localImportKeys].sort());
    const t = createTranslator({
      locale,
      messages,
      onError: (error) => {
        throw error;
      },
    });
    for (const key of localImportKeys) {
      const message = messages[key];
      expect(message.trim(), `${locale}.${key}`).not.toBe("");
      // "Source" is also the genuine French word; Hugging Face is a proper name.
      if (locale !== "en" && key !== "huggingFace" && !(locale === "fr" && key === "sourceName")) {
        expect(message, `${locale}.${key}`).not.toBe(en.models.shared.localImport[key]);
      }
      const argumentsFound = parse(message).flatMap((node) => {
        if (node.type === TYPE.literal) return [];
        expect(node.type, `${locale}.${key}`).toBe(TYPE.argument);
        return "value" in node ? [node.value] : [];
      });
      const expected =
        key === "hashing"
          ? ["name", "percent"]
          : key === "uploading"
            ? ["name", "index", "count"]
            : key === "summary"
              ? ["count", "bytes"]
              : [];
      expect(argumentsFound.sort(), `${locale}.${key}`).toEqual(expected.sort());
      const formatted = t(key, importArguments(locale));
      expect(formatted).not.toMatch(/\{(?:name|percent|index|count|bytes)\}/);
      expect(formatted).not.toBe(key);
    }
    expect(messages.description).toContain("config.json");
    expect(messages.description).toContain("tokenizer.model");
    expect(messages.limits).toContain("256");
    expect(messages.limits).toContain("200 GiB");
    expect(messages.invalidSizes).toContain("64 MiB");
    expect(messages.manifest).toContain("SHA-256");
    expect(
      new Set([messages.failed, messages.saved, messages.verified, messages.canceled]).size
    ).toBe(4);
  });

  it.each(locales)(
    "%s renders progress and distinct import outcomes without fallback",
    (locale) => {
      const failures: Error[] = [];
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          onError={(error) => failures.push(error)}
        >
          <LocalImportCopy locale={locale} />
        </NextIntlClientProvider>
      );
      const copy = catalogs[locale].models.shared.localImport;
      const args = importArguments(locale);
      expect(screen.getByRole("heading", { name: copy.title })).toBeInTheDocument();
      for (const key of ["hashing", "uploading", "summary"] as const) {
        const expected = copy[key].replace(/\{(name|percent|index|count|bytes)\}/g, (_, argument) =>
          String(args[argument as keyof typeof args])
        );
        expect(screen.getByText(expected, { normalizer: (text) => text })).toBeInTheDocument();
      }
      for (const key of ["failed", "saved", "verified", "canceled", "localLicense"] as const) {
        expect(screen.getByText(copy[key])).toBeInTheDocument();
      }
      expect(screen.getByRole("button", { name: copy.cancel })).toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: copy.localLicenseReview })).toBeInTheDocument();
      expect(failures).toEqual([]);
    }
  );
});
