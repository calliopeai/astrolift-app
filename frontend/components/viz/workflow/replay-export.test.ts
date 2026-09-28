import { describe, expect, it } from "vitest";

import { frameTimes } from "./replay-export";

describe("frameTimes", () => {
  it("spans the run from start to finish at the requested rate", () => {
    const t = frameTimes(60_000, 2, 5);
    expect(t).toHaveLength(10);
    expect(t[0]).toBe(0);
    expect(t[t.length - 1]).toBe(60_000);
    expect(t.every((v, i) => i === 0 || v > t[i - 1])).toBe(true);
  });

  it("always has a first and a last frame", () => {
    expect(frameTimes(1000, 0, 12)).toEqual([0, 1000]);
  });
});
