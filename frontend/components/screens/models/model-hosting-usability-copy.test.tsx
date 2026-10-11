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
  nextStep: "Finish setup to review hosting",
  enterName: "Enter a deployment name",
  selectCluster: "Select a cluster",
  selectCompute: "Choose CPU or GPU",
  enterCpu: "Enter a CPU request",
  enterMemory: "Enter a memory request",
  enterGpu: "Use 0 GPUs for CPU, or at least 1 for GPU",
  enterCache: "Enter a valid CPU KV-cache size",
  settingsNewTab: "Cluster settings open in a new tab so this hosting setup stays here.",
  smallModel: "Choose a small test model",
  smallModelHelp:
    "Resolve Qwen/Qwen2.5-0.5B-Instruct from Hugging Face. Access, license, runtime and resource checks still apply.",
  previousStep: "Back",
  continueStep: "Next",
  cpuCacheHelp: "Required for CPU: 1–1024 GiB. Requested memory must exceed this cache.",
} as const;
const keys = Object.keys(english) as (keyof typeof english)[];
const actions = keys.filter(
  (key) => !["nextStep", "settingsNewTab", "smallModelHelp", "cpuCacheHelp"].includes(key)
);

function Copy() {
  const t = useTranslations("models.shared.usability");
  return (
    <>
      <h2>{t("nextStep")}</h2>
      {actions.map((key) => (
        <button key={key}>{t(key)}</button>
      ))}
      <p>{t("settingsNewTab")}</p>
      <p>{t("smallModelHelp")}</p>
      <p>{t("cpuCacheHelp")}</p>
    </>
  );
}
afterEach(cleanup);
describe("model hosting actionable setup copy", () => {
  it("keeps the exact fourteen-key English contract", () => {
    expect(en.models.shared.usability).toEqual(english);
  });
  it.each(locales)("%s has genuine copy, exact keys and valid literal ICU", (locale) => {
    const copy = catalogs[locale].models.shared.usability;
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "models.shared.usability",
    });
    expect(Object.keys(copy).sort()).toEqual([...keys].sort());
    for (const key of keys) {
      expect(copy[key].trim()).not.toBe("");
      expect(parse(copy[key]).every((element) => element.type === TYPE.literal)).toBe(true);
      expect(t(key)).toBe(copy[key]);
      if (locale !== "en") expect(copy[key]).not.toBe(english[key]);
    }
    expect(copy.smallModelHelp).toContain("Qwen/Qwen2.5-0.5B-Instruct");
    expect(copy.smallModelHelp).toContain("Hugging Face");
    expect(copy.selectCompute).toContain("CPU");
    expect(copy.selectCompute).toContain("GPU");
    expect(copy.enterGpu).toContain("0");
    expect(copy.enterGpu).toContain("1");
  });
  it.each(locales)("%s renders setup and navigation labels without fallback", (locale) => {
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
    const copy = catalogs[locale].models.shared.usability;
    expect(screen.getByRole("heading", { name: copy.nextStep })).toBeInTheDocument();
    for (const key of actions)
      expect(screen.getByRole("button", { name: copy[key] })).toBeInTheDocument();
    expect(screen.getByText(copy.settingsNewTab)).toBeInTheDocument();
    expect(screen.getByText(copy.smallModelHelp)).toBeInTheDocument();
    expect(screen.getByText(copy.cpuCacheHelp)).toBeInTheDocument();
    expect(errors).toEqual([]);
  });
});
