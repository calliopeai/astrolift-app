import { readFileSync } from "node:fs";
import path from "node:path";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it } from "vitest";
import en from "@/messages/en.json";
import { NvkeOperatorInstallPlanTreePanel } from "./NvkeOperatorInstallPlanTreePanel";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";

it.each(["en", "de", "es", "fr", "ja", "ko", "pt-BR", "zh-Hans"])(
  "renders complete chart copy in %s",
  (locale) => {
    const messages = JSON.parse(
      readFileSync(path.resolve(__dirname, `../../../messages/${locale}.json`), "utf8")
    ) as typeof en;
    expect(Object.keys(messages.nvke.installTree).sort()).toEqual(
      Object.keys(en.nvke.installTree).sort()
    );
    expect(Object.values(messages.nvke.installTree).every((value) => value.length > 0)).toBe(true);
    render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <NvkeOperatorInstallPlanTreePanel {...INSTALL_TREE_PROPS} />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("button", { name: messages.nvke.installTree.review })).toBeVisible();
    expect(screen.getByText(messages.nvke.installTree.verificationRequired)).toBeVisible();
  }
);
