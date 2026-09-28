import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useRailState } from "./use-rail-state";

describe("useRailState", () => {
  beforeEach(() => window.localStorage.clear());

  it("starts from the default until the person chooses, then remembers the choice", () => {
    const first = renderHook(() => useRailState("rail.test", () => true));
    expect(first.result.current[0]).toBe(true);

    act(() => first.result.current[1](false));
    expect(first.result.current[0]).toBe(false);

    const again = renderHook(() => useRailState("rail.test", () => true));
    expect(again.result.current[0]).toBe(false);
  });
});
