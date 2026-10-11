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
  connections: "Connected apps",
  connectionsHelp:
    "Visible app-environment subscriptions and their reconciliation state. A subscription does not prove traffic or runtime health.",
  visibleSubscriptions: "{count} visible subscriptions",
  shownApps: "{count} apps on this page",
  appTraffic: "Per-app traffic",
  appTrafficUnavailable:
    "Per-app traffic is unavailable. Deployment totals cannot identify which app made a request.",
  metrics: "Usage and metrics",
  metricsAvailable: "{count} deployment metrics available",
  metricsUnconfigured: "Serving metrics are not configured for this deployment.",
  metricsNoData: "No serving samples in this window.",
  metricsReadUnavailable: "Usage data could not be read.",
  metricsScope: "Deployment totals across consumers; not per-app traffic.",
  metricsSetup: "Metrics setup",
  costUnavailable: "Cost is unavailable without measured usage and pricing.",
  selectApp: "Select an app",
  appSearch: "Search apps",
  appsEmpty: "No eligible apps",
  appsEmptyHelp: "Apps need a live environment on this deployment’s cluster.",
  sharingHelp:
    "Shared models accept subscriptions from multiple apps. Dedicated models accept only the selected app. Revoke incompatible subscriptions and confirm reconciliation before changing access.",
  immutableSource: "Model source, cluster and compute mode are preserved when editing settings.",
  chooseDedicatedApp: "Choose a current app before reviewing dedicated access.",
} as const;
const keys = Object.keys(english) as (keyof typeof english)[];
const args = { count: 3, cpu: "2", memory: "8Gi", gpu: "0", app: "Test <app> & team" };
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
      <p>{t("visibleSubscriptions", args)}</p>
      <p>{t("shownApps", args)}</p>
      <p>{t("metricsAvailable", args)}</p>
      <p>{t("metricsScope")}</p>
      <p>{t("appTrafficUnavailable")}</p>
      <button>{t("selectApp")}</button>
      <label>{t("appSearch")}</label>
      <p>{t("appsEmpty")}</p>
      <p>{t("appsEmptyHelp")}</p>
      <p>{t("sharingHelp")}</p>
      <p>{t("immutableSource")}</p>
      <p>{t("chooseDedicatedApp")}</p>
    </>
  );
}
afterEach(cleanup);
describe("hosted model inventory copy contract", () => {
  it("keeps the exact forty-one-key English map", () => {
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
            : ["visibleSubscriptions", "shownApps", "metricsAvailable"].includes(key)
              ? ["count"]
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
      expect(result).not.toMatch(/\{(?:cpu|memory|gpu|app|count)\}/);
      for (const placeholder of expected)
        expect(result).toContain(String(args[placeholder as keyof typeof args]));
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
    for (const key of ["visibleSubscriptions", "shownApps", "metricsAvailable"] as const)
      expect(screen.getByText(t(key, args))).toBeInTheDocument();
    expect(screen.getByText(copy.metricsScope)).toBeInTheDocument();
    expect(screen.getByText(copy.appTrafficUnavailable)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.selectApp })).toBeInTheDocument();
    for (const key of [
      "appSearch",
      "appsEmpty",
      "appsEmptyHelp",
      "sharingHelp",
      "immutableSource",
      "chooseDedicatedApp",
    ] as const)
      expect(screen.getByText(copy[key])).toBeInTheDocument();
    expect(document.querySelector("app")).toBeNull();
    expect(errors).toEqual([]);
  });
});
