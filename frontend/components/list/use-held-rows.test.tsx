import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useHeldRows } from "./use-held-rows";

const id = (r: { id: string }) => r.id;
const rows = (...ids: string[]) => ids.map((i) => ({ id: i }));

describe("useHeldRows", () => {
  it("holds the rows on screen and counts the new ones", () => {
    const { result, rerender } = renderHook(({ r }) => useHeldRows(r, id), {
      initialProps: { r: rows("a", "b") },
    });
    rerender({ r: rows("n1", "n2", "a", "b") });
    expect(result.current.rows.map(id)).toEqual(["a", "b"]);
    expect(result.current.newCount).toBe(2);
    act(() => result.current.reveal());
    expect(result.current.rows.map(id)).toEqual(["n1", "n2", "a", "b"]);
    expect(result.current.newCount).toBe(0);
  });

  it("starts over when the question changes", () => {
    const { result, rerender } = renderHook(({ r, k }) => useHeldRows(r, id, { resetKey: k }), {
      initialProps: { r: rows("a"), k: "all" },
    });
    rerender({ r: rows("x", "y"), k: "failed" });
    rerender({ r: rows("x", "y"), k: "failed" });
    expect(result.current.rows.map(id)).toEqual(["x", "y"]);
    expect(result.current.newCount).toBe(0);
  });

  it("passes rows through when not live", () => {
    const { result } = renderHook(() => useHeldRows(rows("a", "b"), id, { live: false }));
    expect(result.current.rows.map(id)).toEqual(["a", "b"]);
  });
});
