import { describe, expect, it } from "vitest";

import {
  checkAccessHref,
  formatScopeParam,
  parseCheckAccessQuery,
  parseScopeParam,
} from "./check-access-query";

const query = (href: string) => new URLSearchParams(href.split("?")[1] ?? "");

describe("scope params", () => {
  it("round-trips a scope as kind:id with the kind in lower case", () => {
    const raw = formatScopeParam({ kind: "APP", id: "a1b2" });
    expect(raw).toBe("app:a1b2");
    expect(parseScopeParam(raw)).toEqual({ kind: "APP", id: "a1b2" });
  });

  it("keeps colons inside the id", () => {
    expect(parseScopeParam("team:okta:eng")).toEqual({ kind: "TEAM", id: "okta:eng" });
  });

  it("refuses an unknown kind, a missing id and nothing", () => {
    expect(parseScopeParam("cluster:1")).toBeNull();
    expect(parseScopeParam("app:")).toBeNull();
    expect(parseScopeParam(":1")).toBeNull();
    expect(parseScopeParam(null)).toBeNull();
  });
});

describe("check access query", () => {
  it("round-trips who, can, on and compare", () => {
    const href = checkAccessHref({
      who: "u-1",
      can: "app.deploy",
      on: { kind: "APP", id: "g1" },
      compare: "u-2",
    });
    expect(href.startsWith("/administration/permissions/diagnostics?")).toBe(true);
    expect(parseCheckAccessQuery(query(href))).toEqual({
      who: "u-1",
      can: "app.deploy",
      on: { kind: "APP", id: "g1" },
      compare: "u-2",
    });
  });

  it("keeps an empty compare: the compare view with no second person yet", () => {
    expect(parseCheckAccessQuery(query(checkAccessHref({ compare: "" }))).compare).toBe("");
  });

  it("is the bare page, and reads as nothing asked, with nothing set", () => {
    expect(checkAccessHref({})).toBe("/administration/permissions/diagnostics");
    expect(parseCheckAccessQuery(new URLSearchParams())).toEqual({
      who: null,
      can: null,
      on: null,
      compare: null,
    });
  });
});
