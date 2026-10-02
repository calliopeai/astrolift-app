import fs from "node:fs";

import { buildSchema, validate } from "graphql";
import { expect, it } from "vitest";

import { GET_APP_GOLDEN_SIGNALS } from "./observability.queries";

it("validates the actual scope/source/physical resource document against the public SDL", () => {
  const schema = buildSchema(fs.readFileSync("schema.graphql", "utf8"));
  expect(validate(schema, GET_APP_GOLDEN_SIGNALS)).toEqual([]);
});
