import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { type MessageFormatElement, parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { describe, expect, it } from "vitest";

import { IDENTICAL_EVERYWHERE, IDENTICAL_IN } from "./identical-allowlist";

/**
 * next-intl reads a dot in a key as nesting, so `t("actions.connection.reveal")`
 * never finds a key stored flat as `"connection.reveal"`, and the page shows
 * the raw key path instead of the label. Every key is one segment.
 */
const dir = path.resolve(__dirname, "../../messages");

function dottedKeys(node: unknown, at: string[] = []): string[] {
  if (!node || typeof node !== "object") return [];
  return Object.entries(node as Record<string, unknown>).flatMap(([key, value]) => [
    ...(key.includes(".") ? [[...at, key].join(" › ")] : []),
    ...dottedKeys(value, [...at, key]),
  ]);
}

/** Leaf key paths, so a key missing from one catalogue is caught (#2145). */
function leafKeys(node: unknown, at: string[] = []): string[] {
  if (!node || typeof node !== "object") return [at.join(".")];
  return Object.entries(node as Record<string, unknown>).flatMap(([key, value]) =>
    leafKeys(value, [...at, key])
  );
}

describe("message files", () => {
  it.each(readdirSync(dir).filter((f) => f.endsWith(".json")))(
    "%s has no key containing a dot",
    (file) => {
      expect(dottedKeys(JSON.parse(readFileSync(path.join(dir, file), "utf8")))).toEqual([]);
    }
  );

  // Every string the English catalogue ships must exist in every locale, or
  // that locale shows the raw key path instead of the copy.
  const en = new Set(leafKeys(JSON.parse(readFileSync(path.join(dir, "en.json"), "utf8"))));
  it.each(readdirSync(dir).filter((f) => f.endsWith(".json") && f !== "en.json"))(
    "%s has every key en.json has",
    (file) => {
      const keys = new Set(leafKeys(JSON.parse(readFileSync(path.join(dir, file), "utf8"))));
      expect([...en].filter((key) => !keys.has(key))).toEqual([]);
    }
  );
});

/** Leaf key paths with their values. */
function leafEntries(node: unknown, at: string[] = []): [string, string][] {
  if (typeof node === "string") return [[at.join("."), node]];
  if (!node || typeof node !== "object") return [];
  return Object.entries(node as Record<string, unknown>).flatMap(([key, value]) =>
    leafEntries(value, [...at, key])
  );
}

/** What a message shows in words: its literal text, without arguments or tags. */
function shownText(elements: MessageFormatElement[]): string {
  return elements
    .map((node) => {
      if (node.type === TYPE.literal) return node.value;
      if (node.type === TYPE.tag) return shownText(node.children);
      if (node.type === TYPE.plural || node.type === TYPE.select)
        return Object.values(node.options)
          .map((option) => shownText(option.value))
          .join(" ");
      return " ";
    })
    .join("");
}

/**
 * A value that equals the English is an untranslated copy (#2381) unless it
 * is the same in that language: a message with no words of its own
 * ("{name}: {health}"), or a value on the allowlist (brands, acronyms, units
 * and code everywhere; words a language uses unchanged in that language).
 */
describe("translations", () => {
  const english = leafEntries(JSON.parse(readFileSync(path.join(dir, "en.json"), "utf8")));
  const hasWords = (message: string) => /\p{L}/u.test(shownText(parse(message)));
  const copiesIn = (file: string) => {
    const catalog = new Map(leafEntries(JSON.parse(readFileSync(path.join(dir, file), "utf8"))));
    return english.filter(([key, value]) => catalog.get(key) === value && hasWords(value));
  };
  const files = readdirSync(dir).filter((f) => f.endsWith(".json") && f !== "en.json");

  it.each(files)("%s has no untranslated English copy", (file) => {
    const allowed = new Set([...IDENTICAL_EVERYWHERE, ...(IDENTICAL_IN[file.slice(0, -5)] ?? [])]);
    expect(
      copiesIn(file)
        .filter(([, value]) => !allowed.has(value))
        .map(([key, value]) => `${key}: ${value}`)
    ).toEqual([]);
  });

  it("allows only values some catalog still keeps as they are", () => {
    const kept = new Map(
      files.map((f) => [f.slice(0, -5), new Set(copiesIn(f).map(([, v]) => v))])
    );
    expect(IDENTICAL_EVERYWHERE.filter((v) => ![...kept.values()].some((s) => s.has(v)))).toEqual(
      []
    );
    expect(
      Object.entries(IDENTICAL_IN).flatMap(([locale, values]) =>
        values.filter((v) => !kept.get(locale)?.has(v)).map((v) => `${locale}: ${v}`)
      )
    ).toEqual([]);
  });
});
