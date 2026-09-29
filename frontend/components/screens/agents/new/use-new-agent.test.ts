import { describe, expect, it } from "vitest";

import { partErrors, stepValid } from "./use-new-agent";

const none = { repo: false, discover: false, project: false };
const all = { repo: true, discover: true, project: true };

describe("stepValid", () => {
  it("needs a repo and a scan with a new agent to leave Source", () => {
    expect(stepValid(1, all, true)).toBe(true);
    expect(stepValid(1, { ...all, discover: false }, true)).toBe(false);
    // A cleared repo leaves the old scan's validity behind; it does not count.
    expect(stepValid(1, all, false)).toBe(false);
  });

  it("needs a project to leave Configure; Review always submits", () => {
    expect(stepValid(2, { ...all, project: false }, true)).toBe(false);
    expect(stepValid(2, all, true)).toBe(true);
    expect(stepValid(3, none, false)).toBe(true);
  });
});

describe("partErrors", () => {
  it("says nothing until Continue was pressed on the step", () => {
    expect(partErrors(1, none, false, true)).toEqual({});
  });

  it("names each part still missing on the step on screen, beside that part", () => {
    expect(Object.keys(partErrors(1, none, true, false))).toEqual(["repo"]);
    expect(Object.keys(partErrors(1, { ...none, repo: true }, true, true))).toEqual(["discover"]);
    expect(Object.keys(partErrors(2, all, true, true))).toEqual([]);
    expect(partErrors(2, none, true, true).project).toMatch(/project/);
  });
});
