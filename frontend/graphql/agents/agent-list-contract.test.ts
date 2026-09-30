import fs from "node:fs";
import { buildSchema, validate } from "graphql";
import { describe, expect, it } from "vitest";

import {
  AGENT_FLEET_LIST_PAGE,
  LIST_AGENT_FLEET,
  LIST_AGENT_FLEET_PAGE,
  LIST_AGENT_WORKLOADS,
} from "./agents.queries";

const schema = buildSchema(fs.readFileSync("schema.graphql", "utf8"));

describe("agent run-spec list reads", () => {
  for (const [name, document] of Object.entries({
    AGENT_FLEET_LIST_PAGE,
    LIST_AGENT_FLEET,
    LIST_AGENT_FLEET_PAGE,
    LIST_AGENT_WORKLOADS,
  })) {
    it(`${name} reads supported settings on every list surface`, () => {
      expect(validate(schema, document)).toEqual([]);
      const fields = new Set<string>();
      function walk(node: unknown) {
        if (!node || typeof node !== "object") return;
        if ("kind" in node && node.kind === "Field" && "name" in node) {
          fields.add((node.name as { value: string }).value);
        }
        for (const [key, value] of Object.entries(node)) {
          if (key === "loc") continue;
          if (Array.isArray(value)) value.forEach(walk);
          else walk(value);
        }
      }
      walk(document);
      for (const field of [
        "replicas",
        "runMaxParallel",
        "scheduledScaleTo",
        "scaleUpCron",
        "scaleDownCron",
      ])
        expect(fields.has(field)).toBe(true);
    });
  }
});
