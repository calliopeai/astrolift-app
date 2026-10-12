import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

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
