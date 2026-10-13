import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import { locales } from "@/i18n/config";

import { cookieLocale, GLOBAL_ERROR_MESSAGES } from "./global-error-messages";
import { GlobalErrorScreen } from "./GlobalErrorScreen";

const catalog = (locale: string) =>
  JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")) as {
    errorScreens: Record<string, string>;
  };

describe("the hard-crash screen's own copy (#2381)", () => {
  it.each(locales)("%s mirrors errorScreens in its catalog", (locale) => {
    const { errorScreens } = catalog(locale);
    expect(GLOBAL_ERROR_MESSAGES[locale].errorScreens).toEqual({
      title: errorScreens.title,
      critical: errorScreens.critical,
      errorId: errorScreens.errorId,
      tryAgain: errorScreens.tryAgain,
    });
  });

  it("reads the language the viewer chose from the locale cookie", () => {
    expect(cookieLocale("tz=UTC; locale=ja")).toBe("ja");
    expect(cookieLocale("locale=zh-Hans")).toBe("zh-Hans");
    expect(cookieLocale("locale=xx; tz=UTC")).toBe("en");
    expect(cookieLocale("")).toBe("en");
  });

  it("renders in that language with only its own messages", () => {
    const { errorScreens } = catalog("de");
    render(
      <NextIntlClientProvider locale="de" messages={GLOBAL_ERROR_MESSAGES.de} timeZone="UTC">
        <GlobalErrorScreen message="" digest="abc123" onReset={() => {}} />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("heading", { name: errorScreens.title })).toBeInTheDocument();
    expect(screen.getByText(errorScreens.critical)).toBeInTheDocument();
    expect(
      screen.getByText(errorScreens.errorId.replace("{digest}", "abc123"))
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: errorScreens.tryAgain })).toBeInTheDocument();
  });
});
