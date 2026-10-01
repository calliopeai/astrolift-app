import { readFileSync } from "node:fs";
import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";

const locales = ["en", "es", "fr", "de", "ja", "ko", "zh-Hans", "pt-BR"];
const keys = ["deleted", "failed", "navigationWarning", "refreshWarning"];

describe.each(locales)("App-frame delete ICU contract in %s", (locale) => {
  it("preserves the narrow key shape and literal slug arguments", () => {
    const messages = JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
    const feedback = messages.apps.frame.deleteFeedback;
    expect(Object.keys(feedback).sort()).toEqual(keys);
    const t = createTranslator({ locale, messages, namespace: "apps.frame.deleteFeedback" });
    for (const key of keys) {
      const ast = parse(feedback[key]);
      expect(ast.filter((node) => node.type !== TYPE.literal)).toEqual(
        key === "failed" ? [] : [{ type: TYPE.argument, value: "slug" }]
      );
      expect(t(key, { slug: "provider/slug_%literal" })).toBeTruthy();
      if (key !== "failed")
        expect(t(key, { slug: "provider/slug_%literal" })).toContain("provider/slug_%literal");
    }
  });
});
