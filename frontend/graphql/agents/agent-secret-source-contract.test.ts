import fs from "node:fs";
import { buildSchema, validate } from "graphql";
import { describe, expect, it } from "vitest";
import {
  AGENT_SECRET_SOURCE_DETAIL,
  AGENT_SECRET_SOURCE_OPTIONS,
} from "./agent-secret-source.queries";

const schema = buildSchema(fs.readFileSync("schema.graphql", "utf8"));

describe("environment-recipe source documents", () => {
  for (const [name, document] of Object.entries({
    AGENT_SECRET_SOURCE_DETAIL,
    AGENT_SECRET_SOURCE_OPTIONS,
  })) {
    it(`${name} uses the existing scoped API without secret packet or inferred fleet fields`, () => {
      expect(validate(schema, document)).toEqual([]);
      const text = document.loc?.source.body ?? "";
      expect(text).toContain("orgId: $orgId");
      expect(text).not.toMatch(/secretRefs|secretValue|agentBoxes|environmentSpecSlug/);
    });
  }
});
