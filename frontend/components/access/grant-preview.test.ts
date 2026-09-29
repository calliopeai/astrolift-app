import { describe, expect, it } from "vitest";

import { effectSentence, expiryToIso } from "./GrantAccessFlow";
import { previewOf, principalOfSearch, principalRef, sourceOfPreview } from "./use-grant-access";

const NOW = Date.UTC(2026, 8, 29, 12, 0, 0);

describe("expiryToIso", () => {
  it("never is null, days count from now, a date ends that day", () => {
    expect(expiryToIso({ kind: "never" }, NOW)).toBeNull();
    expect(expiryToIso({ kind: "days", days: 7 }, NOW)).toBe("2026-10-06T12:00:00.000Z");
    const end = new Date(expiryToIso({ kind: "date", date: "2026-10-31" }, NOW)!);
    expect([end.getFullYear(), end.getMonth(), end.getDate(), end.getHours()]).toEqual([
      2026, 9, 31, 23,
    ]);
    expect(expiryToIso({ kind: "date", date: "" }, NOW)).toBeNull();
  });
});

describe("principalOfSearch", () => {
  it("picks users by the id grantRole takes, groups by external id, teams by id", () => {
    expect(
      principalOfSearch({ kind: "USER", name: "Dana", secondary: "dana@example.com", userId: "61" })
    ).toEqual({ kind: "user", id: "61", name: "Dana", detail: "dana@example.com" });
    expect(
      principalOfSearch({
        kind: "GROUP",
        name: "okta:eng",
        secondary: "",
        groupExternalId: "okta:eng",
        memberCount: 1,
      })
    ).toEqual({ kind: "group", id: "okta:eng", name: "okta:eng", detail: "IdP group · 1 member" });
    expect(
      principalOfSearch({
        kind: "TEAM",
        name: "Payments",
        secondary: "payments",
        teamId: "t-1",
        teamSlug: "payments",
      })
    ).toMatchObject({ kind: "team", id: "t-1" });
  });

  it("an invitation is not something a role can be granted to", () => {
    expect(
      principalOfSearch({ kind: "INVITATION", name: "x@example.com", secondary: "" })
    ).toBeNull();
  });

  it("sends each pick as its AstroliftPrincipalRef", () => {
    expect(principalRef({ kind: "group", id: "okta:eng", name: "okta:eng" })).toEqual({
      kind: "GROUP",
      id: "okta:eng",
    });
  });
});

describe("previewOf", () => {
  const user = (id: string) => ({ id, username: `u${id}`, email: `u${id}@example.com` });
  const server = {
    ok: true,
    errors: [],
    gainingCount: 40,
    unchangedCount: 1,
    gaining: [{ user: user("1"), gained: ["app.deploy"], via: [] }],
    unchanged: [
      {
        user: user("2"),
        gained: [],
        via: [
          {
            source: "GROUP_MAPPING",
            scopeKind: "ORG",
            scopeGuid: "org-1",
            sourceScopeLabel: "organization acme",
            groupExternalId: "okta:eng",
            inherited: true,
          },
        ],
      },
    ],
    groups: [{ groupExternalId: "okta:eng", memberCount: 40 }],
    allowed: true,
    refusal: null,
    notes: ["note"],
  };

  it("says who gains what and who already had it through what, with the exact counts", () => {
    const p = previewOf(server);
    expect(p.gaining).toEqual([
      {
        principal: { kind: "user", id: "1", name: "u1", detail: "u1@example.com" },
        permissions: ["app.deploy"],
      },
    ]);
    expect(p.already[0]?.source).toEqual({
      via: { kind: "group", group: "okta:eng" },
      inheritedFrom: { kind: "ORG", id: "org-1", name: "organization acme" },
    });
    expect(effectSentence(p, null, null)).toBe("40 people gain access; 1 already had it.");
    expect(p.refusal).toBeNull();
  });

  it("a refused grant carries the server's reason", () => {
    expect(previewOf({ ...server, allowed: false, refusal: "not yours to grant" }).refusal).toBe(
      "not yours to grant"
    );
  });

  it("a direct grant held elsewhere has no via", () => {
    expect(sourceOfPreview(undefined)).toEqual({});
  });
});
