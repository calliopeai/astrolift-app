import { readFileSync } from "node:fs";
import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { describe, expect, it } from "vitest";

const locales = ["en", "es", "fr", "de", "ja", "ko", "zh-Hans", "pt-BR"];
function leaves(object: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.assign(
    {},
    ...Object.entries(object).map(([key, value]) =>
      typeof value === "string"
        ? { [prefix + key]: value }
        : leaves(value as Record<string, unknown>, prefix + key + ".")
    )
  );
}
function contract(nodes: MessageFormatElement[]): unknown[] {
  return nodes
    .filter((node) => node.type !== TYPE.literal)
    .map((node) =>
      node.type === TYPE.tag
        ? [node.type, node.value, contract(node.children)]
        : [node.type, "value" in node ? node.value : null]
    );
}
const catalog = (locale: string) => JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
const canonical = leaves(catalog("en").apps.managedServicesAdmin);
describe.each(locales)("Managed service admin ICU contract in %s", (locale) => {
  it("has exactly the 36 owned human keys and preserves literal arguments and rich tags", () => {
    const own = leaves(catalog(locale).apps.managedServicesAdmin);
    expect(Object.keys(own).sort()).toEqual(Object.keys(canonical).sort());
    expect(Object.keys(own)).toHaveLength(36);
    for (const [key, value] of Object.entries(own))
      expect(contract(parse(value))).toEqual(contract(parse(canonical[key])));
  });
});
