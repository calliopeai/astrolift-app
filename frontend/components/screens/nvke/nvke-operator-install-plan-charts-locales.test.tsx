import { readFileSync } from "node:fs";
import path from "node:path";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it } from "vitest";
import en from "@/messages/en.json";
import { NvkeOperatorInstallPlanChartsPanel } from "./NvkeOperatorInstallPlanChartsPanel";
import { INSTALL_CHARTS_PROPS } from "./NvkeOperatorInstallPlanChartsPanel.stories";

it.each(["en", "de", "es", "fr", "ja", "ko", "pt-BR", "zh-Hans"])(
  "renders complete chart copy in %s",
  (locale) => {
    const messages = JSON.parse(
      readFileSync(path.resolve(__dirname, `../../../messages/${locale}.json`), "utf8")
    ) as typeof en;
    expect(Object.keys(messages.nvke.installCharts).sort()).toEqual(
      Object.keys(en.nvke.installCharts).sort()
    );
    expect(Object.values(messages.nvke.installCharts).every((value) => value.length > 0)).toBe(
      true
    );
    render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <NvkeOperatorInstallPlanChartsPanel {...INSTALL_CHARTS_PROPS} />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("button", { name: messages.nvke.installCharts.review })).toBeVisible();
    expect(screen.getByText(messages.nvke.installCharts.verificationRequired)).toBeVisible();
  }
);
