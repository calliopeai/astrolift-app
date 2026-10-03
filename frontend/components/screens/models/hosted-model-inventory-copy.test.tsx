import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { cleanup, render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { afterEach, describe, expect, it } from "vitest";
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
  title: "Hosted models",
  description:
    "Models deployed to your clusters. Open a model to review resources, readiness, access and app subscriptions.",
  addModel: "Add model",
  source: "Source",
  local: "Local files",
  huggingface: "Hugging Face",
  unknownSource: "Unknown source",
  manifest: "Manifest SHA-256",
  revision: "Model revision",
  resources: "Requested resources",
  resourceSummary: "{cpu} CPU · {memory} memory · {gpu} GPUs",
  access: "App access",
  shared: "Shared",
  dedicated: "Dedicated",
  dedicatedApp: "Dedicated to {app}",
  unknownAccess: "Access mode unknown",
  settings: "Edit settings",
  settingsDescription:
    "Review changes to the model name, resources and app access. Accepted changes still need reconciliation.",
  overview: "Deployment",
  readiness: "Last readiness observation",
} as const;
const keys = Object.keys(english) as (keyof typeof english)[];
const args = { cpu: "2", memory: "8Gi", gpu: "0", app: "Test <app> & team" };
function Copy() {
  const t = useTranslations("models.shared.inventory");
  return (
    <>
      <h1>{t("title")}</h1>
      <button>{t("addModel")}</button>
      <button>{t("settings")}</button>
      <p>{t("resourceSummary", args)}</p>
      <p>{t("dedicatedApp", args)}</p>
      <p>{t("settingsDescription")}</p>
    </>
  );
}
afterEach(cleanup);
describe("hosted model inventory copy contract", () => {
  it("keeps the exact twenty-key English map", () => {
    expect(en.models.shared.inventory).toEqual(english);
  });
  it.each(locales)("%s preserves keys and ICU arguments with genuine translations", (locale) => {
    const copy = catalogs[locale].models.shared.inventory;
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "models.shared.inventory",
    });
    expect(Object.keys(copy).sort()).toEqual([...keys].sort());
    for (const key of keys) {
      expect(copy[key].trim()).not.toBe("");
      const ast = parse(copy[key]);
      const expected =
        key === "resourceSummary"
          ? ["cpu", "gpu", "memory"]
          : key === "dedicatedApp"
            ? ["app"]
            : [];
      expect(
        ast
          .filter((part) => part.type === TYPE.argument)
          .map((part) => part.value)
          .sort()
      ).toEqual(expected);
      expect(ast.every((part) => part.type === TYPE.literal || part.type === TYPE.argument)).toBe(
        true
      );
      const result = t(key, args);
      expect(result).not.toMatch(/\{(?:cpu|memory|gpu|app)\}/);
      for (const placeholder of expected)
        expect(result).toContain(args[placeholder as keyof typeof args]);
      if (locale !== "en" && key !== "huggingface") expect(copy[key]).not.toBe(english[key]);
    }
    expect(copy.huggingface).toBe("Hugging Face");
    expect(copy.manifest).toContain("SHA-256");
  });
  it.each(locales)("%s renders actions and literal app names without fallback", (locale) => {
    const errors: Error[] = [];
    render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        onError={(error) => errors.push(error)}
      >
        <Copy />
      </NextIntlClientProvider>
    );
    const copy = catalogs[locale].models.shared.inventory;
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "models.shared.inventory",
    });
    expect(screen.getByRole("heading", { name: copy.title })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.addModel })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.settings })).toBeInTheDocument();
    expect(screen.getByText(t("resourceSummary", args))).toBeInTheDocument();
    expect(screen.getByText(t("dedicatedApp", args))).toBeInTheDocument();
    expect(screen.getByText(copy.settingsDescription)).toBeInTheDocument();
    expect(document.querySelector("app")).toBeNull();
    expect(errors).toEqual([]);
  });
});
