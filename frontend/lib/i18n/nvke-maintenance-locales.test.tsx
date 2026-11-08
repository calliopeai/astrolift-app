import { readFileSync } from "node:fs";
import path from "node:path";
import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { NvkeMaintenancePanel } from "@/components/screens/nvke/NvkeMaintenancePanel";
import { NvkeWorkloadAccessPanel } from "@/components/screens/nvke/NvkeWorkloadAccessPanel";
import access, { ACCESS_BINDING } from "@/components/screens/nvke/NvkeWorkloadAccessPanel.stories";
import { MAINTENANCE_OBSERVATION } from "@/components/screens/nvke/fixtures";
import type english from "@/messages/en.json";
type Messages = { [key: string]: string | Messages };
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")) as typeof english,
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
function argumentsOf(message: string) {
  return parse(message)
    .flatMap((element) => {
      if (element.type === TYPE.literal) return [];
      expect(element.type).toBe(TYPE.argument);
      return element.type === TYPE.argument ? [element.value] : [];
    })
    .sort();
}
const values = {
  site: "reviewed-site",
  siteVersion: 7,
  cluster: ACCESS_BINDING.clusterId,
  source: ACCESS_BINDING.sourceOperationId,
  version: 9,
  binding: ACCESS_BINDING.id,
};
it.each(locales)(
  "%s preserves every access/maintenance field and exact review arguments",
  (locale) => {
    const onError = vi.fn();
    for (const namespace of ["workloadAccess", "maintenance"] as const) {
      const original = leaves(catalogs.en.nvke[namespace]),
        translated = leaves(catalogs[locale].nvke[namespace]);
      expect(Object.keys(translated).sort()).toEqual(Object.keys(original).sort());
      for (const [key, text] of Object.entries(original)) {
        const args = argumentsOf(text);
        expect(argumentsOf(translated[key]), key).toEqual(args);
        const t = createTranslator({
          locale,
          messages: { message: translated[key] },
          onError,
        });
        const rendered = t("message", values);
        expect(rendered).not.toBe("message");
        for (const arg of args)
          expect(rendered, `${locale}:${key}:${arg}`).toContain(
            String(values[arg as keyof typeof values])
          );
      }
    }
    expect(onError).not.toHaveBeenCalled();
  }
);
it.each(locales)(
  "%s renders retained failure and enrollment confirmation without fallback",
  (locale) => {
    const messages = catalogs[locale],
      onError = vi.fn();
    render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC" onError={onError}>
        <NvkeMaintenancePanel
          observation={MAINTENANCE_OBSERVATION}
          loading={false}
          error={null}
          onRefresh={() => {}}
        />
        <NvkeWorkloadAccessPanel {...access.args} />
      </NextIntlClientProvider>
    );
    const t = messages.nvke.maintenance;
    const renewal = screen.getByRole("heading", { name: t.renewal }).closest("section")!;
    expect(within(renewal).getByText(t.state).nextElementSibling).toHaveTextContent(
      t.values.failed
    );
    expect(within(renewal).getByText(t.retained).nextElementSibling).toHaveTextContent(t.yes);
    expect(within(renewal).getByText(t.outcome).nextElementSibling).toHaveTextContent(
      t.notReported
    );
    fireEvent.click(screen.getByRole("button", { name: messages.nvke.workloadAccess.enroll }));
    const review = screen.getByRole("alertdialog");
    expect(review).toHaveTextContent(ACCESS_BINDING.clusterId);
    expect(review).toHaveTextContent(ACCESS_BINDING.sourceOperationId);
    expect(review).toHaveTextContent(access.args.site.name);
    expect(onError).not.toHaveBeenCalled();
  }
);
