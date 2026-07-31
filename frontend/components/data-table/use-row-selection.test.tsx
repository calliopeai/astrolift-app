import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useRowSelection } from "./use-row-selection";

describe("useRowSelection", () => {
  it("toggles a row on and off", () => {
    const { result } = renderHook(() => useRowSelection());

    act(() => result.current.toggle("a"));
    expect(result.current.isSelected("a")).toBe(true);
    expect(result.current.selectedCount).toBe(1);

    act(() => result.current.toggle("a"));
    expect(result.current.isSelected("a")).toBe(false);
    expect(result.current.selectedCount).toBe(0);
  });

  it("keeps a selection across pages", () => {
    // The seven hand-rolled copies all reset on page change, so a bulk
    // action silently applied to fewer rows than the count claimed.
    const { result } = renderHook(() => useRowSelection());

    act(() => result.current.togglePage(["a", "b"]));
    act(() => result.current.togglePage(["c", "d"]));

    expect(result.current.selectedCount).toBe(4);
    expect(result.current.selectedIds.sort()).toEqual(["a", "b", "c", "d"]);
  });

  it("reports the header checkbox state for the visible page only", () => {
    const { result } = renderHook(() => useRowSelection());
    const page = ["a", "b", "c"];

    expect(result.current.pageSelectionState(page)).toBe(false);

    act(() => result.current.toggle("a"));
    expect(result.current.pageSelectionState(page)).toBe("indeterminate");

    act(() => result.current.toggle("b"));
    act(() => result.current.toggle("c"));
    expect(result.current.pageSelectionState(page)).toBe(true);

    // A row selected on another page must not make this page look full.
    act(() => result.current.toggle("z"));
    expect(result.current.pageSelectionState(page)).toBe(true);
    expect(result.current.pageSelectionState(["z", "y"])).toBe("indeterminate");
  });

  it("clears only the current page when that page is fully selected", () => {
    const { result } = renderHook(() => useRowSelection());

    act(() => result.current.togglePage(["a", "b"]));
    act(() => result.current.toggle("z"));
    act(() => result.current.togglePage(["a", "b"]));

    expect(result.current.selectedIds).toEqual(["z"]);
  });

  it("selects the whole page when it is only partly selected", () => {
    const { result } = renderHook(() => useRowSelection());

    act(() => result.current.toggle("a"));
    act(() => result.current.togglePage(["a", "b", "c"]));

    expect(result.current.selectedCount).toBe(3);
  });

  it("treats an empty page as unselected and ignores select-all on it", () => {
    const { result } = renderHook(() => useRowSelection());

    expect(result.current.pageSelectionState([])).toBe(false);
    act(() => result.current.togglePage([]));
    expect(result.current.selectedCount).toBe(0);
  });

  it("clears everything", () => {
    const { result } = renderHook(() => useRowSelection());

    act(() => result.current.togglePage(["a", "b", "c"]));
    act(() => result.current.clear());

    expect(result.current.selectedCount).toBe(0);
    expect(result.current.selectedIds).toEqual([]);
  });
});
