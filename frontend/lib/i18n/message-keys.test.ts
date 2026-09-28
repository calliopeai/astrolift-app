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

describe("message files", () => {
  it.each(readdirSync(dir).filter((f) => f.endsWith(".json")))(
    "%s has no key containing a dot",
    (file) => {
      expect(dottedKeys(JSON.parse(readFileSync(path.join(dir, file), "utf8")))).toEqual([]);
    }
  );
});
