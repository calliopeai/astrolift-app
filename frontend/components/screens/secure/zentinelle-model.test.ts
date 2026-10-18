import { describe, expect, it } from "vitest";

import { zentinelleAccess, zentinelleInNav } from "./zentinelle-model";

const grants =
  (...slugs: string[]) =>
  (check: unknown) =>
    typeof check === "string" && slugs.includes(check);

describe("zentinelleInNav", () => {
  it("links the screen only when the connection is live and the viewer has a grant", () => {
    const admin = zentinelleAccess(grants("zentinelle.status_view", "zentinelle.connect"));
    const developer = zentinelleAccess(grants("zentinelle.usage_view"));
    const none = zentinelleAccess(grants());
    expect(zentinelleInNav(admin, true)).toBe(true);
    expect(zentinelleInNav(developer, true)).toBe(true);
    expect(zentinelleInNav(admin, false)).toBe(false);
    expect(zentinelleInNav(developer, false)).toBe(false);
    expect(zentinelleInNav(none, true)).toBe(false);
  });
});
