"use client";

/**
 * useRowSelection — the selection state seven surfaces had each written
 * out by hand (#1231).
 *
 * The copies were near-identical and all shared one bug: "select all"
 * meant "select every row currently rendered", so paging away and back
 * silently changed what a bulk action would apply to. Keeping the id set
 * across pages makes the selection mean what the count says, and
 * `pageSelectionState` is deliberately scoped to the visible page so the
 * header checkbox still answers the question the operator is asking.
 */

import * as React from "react";

export type RowSelection = {
  selectedIds: string[];
  selectedCount: number;
  isSelected: (id: string) => boolean;
  toggle: (id: string) => void;
  /** Select or clear every row on the current page. */
  togglePage: (ids: string[]) => void;
  /** `true` / `false` / `"indeterminate"` for the header checkbox. */
  pageSelectionState: (ids: string[]) => boolean | "indeterminate";
  clear: () => void;
};

export function useRowSelection(): RowSelection {
  const [selected, setSelected] = React.useState<ReadonlySet<string>>(() => new Set());

  const toggle = React.useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, []);

  const togglePage = React.useCallback((ids: string[]) => {
    setSelected((prev) => {
      const next = new Set(prev);
      const allOn = ids.length > 0 && ids.every((id) => next.has(id));
      for (const id of ids) {
        if (allOn) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  }, []);

  const pageSelectionState = React.useCallback(
    (ids: string[]): boolean | "indeterminate" => {
      if (ids.length === 0) return false;
      const on = ids.filter((id) => selected.has(id)).length;
      if (on === 0) return false;
      return on === ids.length ? true : "indeterminate";
    },
    [selected]
  );

  return {
    selectedIds: React.useMemo(() => [...selected], [selected]),
    selectedCount: selected.size,
    isSelected: React.useCallback((id: string) => selected.has(id), [selected]),
    toggle,
    togglePage,
    pageSelectionState,
    clear: React.useCallback(() => setSelected(new Set()), []),
  };
}
