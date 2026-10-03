import { describe, expect, it } from "vitest";

import type { AstroliftPolicy } from "@/graphql/__generated__/schema";

import { AFTER_HOURS, INVALID_POLICY, SECRETS_OFFICE } from "./fixtures";
import {
  conditionError,
  describeDays,
  parsePolicy,
  policySentence,
  serializePolicy,
} from "./policy-model";

describe("policy model", () => {
  it("reads a policy as a sentence", () => {
    expect(policySentence(AFTER_HOURS)).toBe(
      "Deny app.deploy on anything in production for everyone unless it is Mon to Fri 09:00 to 18:00 America/Los_Angeles."
    );
    expect(policySentence(SECRETS_OFFICE)).toBe(
      "Deny secret.* on anything for members of okta:contractors unless the request comes from 10.0.0.0/8 or 192.168.0.0/16 and the person signed in within 15 min."
    );
  });

  it("round-trips the stored JSON, keeping unknown conditions verbatim", () => {
    const row = {
      effect: "DENY",
      actionPattern: "app.deploy",
      resourcePattern: { env: "production", app_slug: ["checkout", "cart"] },
      conditions: [
        { kind: "approval_required", min_approvers: 2 },
        { kind: "geo_fence", countries: ["US"] },
      ],
      actorPattern: { user_in_groups: ["okta:eng"] },
    } satisfies Pick<
      AstroliftPolicy,
      "effect" | "actionPattern" | "resourcePattern" | "conditions" | "actorPattern"
    >;
    const out = serializePolicy(parsePolicy(row));
    expect(out.resourcePattern).toEqual(row.resourcePattern);
    expect(out.conditions).toEqual(row.conditions);
    expect(out.actorPattern).toEqual(row.actorPattern);
  });

  it("says what is wrong beside each condition", () => {
    expect(INVALID_POLICY.conditions.map(conditionError)).toEqual([
      "Pick at least one day.",
      "Each entry is a CIDR, like 10.0.0.0/8.",
    ]);
    expect(AFTER_HOURS.conditions.map(conditionError)).toEqual([null]);
  });

  it("collapses consecutive days", () => {
    expect(describeDays(["mon", "tue", "wed"])).toBe("Mon to Wed");
    expect(describeDays(["sat", "mon"])).toBe("Mon, Sat");
    expect(describeDays(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])).toBe("every day");
  });
});
