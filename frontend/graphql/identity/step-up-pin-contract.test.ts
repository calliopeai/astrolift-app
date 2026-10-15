import fs from "node:fs";
import { buildSchema, validate } from "graphql";
import { describe, expect, it } from "vitest";

import { CLEAR_STEP_UP_PIN, SET_STEP_UP_PIN } from "./identity.mutations";
import { MY_STEP_UP_PIN } from "./identity.queries";

const schema = buildSchema(fs.readFileSync("schema.graphql", "utf8"));

describe("step-up PIN documents (#2399)", () => {
  it("validate against the schema", () => {
    for (const document of [SET_STEP_UP_PIN, CLEAR_STEP_UP_PIN, MY_STEP_UP_PIN]) {
      expect(validate(schema, document)).toEqual([]);
    }
  });

  it("setStepUpPin selects supportedMethods so a PIN change is not offered a PIN step-up", () => {
    // Without it the prompt falls back to the session's methods, which list
    // the PIN first; the set refuses a PIN elevation, so the change loops.
    const text = SET_STEP_UP_PIN.loc?.source.body ?? "";
    expect(text).toMatch(/errors\s*\{[^}]*supportedMethods/);
  });
});
