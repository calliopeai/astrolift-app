import { describe, expect, it } from "vitest";
import {
  initialFieldValues,
  readStamp,
  simpleInputs,
  stampKey,
  type InputContract,
} from "./reviewed-start-model";
const contract: InputContract = {
  schema: {},
  digest: "schema",
  supported: true,
  error: "",
  acceptsInputs: true,
  supportsSimpleForm: true,
  fields: [
    {
      name: "count",
      kind: "integer",
      required: true,
      hasDefault: true,
      default: 3,
      sensitive: false,
      simple: true,
      enumValues: null,
      constraints: {},
    },
    {
      name: "optional",
      kind: "string",
      required: false,
      hasDefault: false,
      default: null,
      sensitive: false,
      simple: true,
      enumValues: null,
      constraints: {},
    },
    {
      name: "secret",
      kind: "string",
      required: false,
      hasDefault: false,
      default: "must-not-be-visible",
      sensitive: true,
      simple: true,
      enumValues: null,
      constraints: {},
    },
  ],
};
describe("reviewed start recovery", () => {
  it("separates request metadata by actor, organization and exact GUID", () => {
    expect(stampKey("workflow", "org-a", "actor-a", "guid")).not.toBe(
      stampKey("workflow", "org-b", "actor-a", "guid")
    );
    expect(stampKey("workflow", "org-a", "actor-a", "guid")).not.toBe(
      stampKey("workflow", "org-a", "actor-b", "guid")
    );
  });
  it("restores only reconciliation metadata and strips submitted input values", () => {
    const parsed = readStamp(
      JSON.stringify({
        kind: "workflow",
        targetId: "guid",
        requestId: "stable",
        revision: "rev",
        inputSchemaDigest: "schema",
        inputs: { marker: "private-input" },
      }),
      "workflow",
      "guid"
    );
    expect(parsed?.requestId).toBe("stable");
    expect(JSON.stringify(parsed)).not.toContain("private-input");
    expect(readStamp(JSON.stringify(parsed), "workflow", "other-guid")).toBeNull();
  });
  it("keeps defaults, omits untouched inputs, and allows explicit empty strings", () => {
    const initial = initialFieldValues(contract);
    expect(initial.secret).toBeUndefined();
    expect(simpleInputs(contract, initial)).toEqual({ count: 3 });
    expect(simpleInputs(contract, { ...initial, optional: "" })).toEqual({
      count: 3,
      optional: "",
    });
  });
  it("refuses non-finite and fractional integers", () => {
    expect(() => simpleInputs(contract, { count: "Infinity" })).toThrow();
    expect(() => simpleInputs(contract, { count: "1.5" })).toThrow();
    expect(() => simpleInputs(contract, { count: "" })).toThrow();
    expect(() => simpleInputs(contract, { count: " " })).toThrow();
  });
});
