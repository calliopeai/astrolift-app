"use client";

import * as React from "react";

/**
 * The "3 new ↑" behaviour (spec 44 §5.1) for live lists: the rows on screen
 * stay put while polled or pushed rows arrive, and the new ones wait behind
 * the pill until the reader asks for them. Rows already shown still update
 * in place (a run's status moving from running to failed); they just do not
 * move.
 *
 *   const held = useHeldRows(rows, (r) => r.id, {
 *     live: list.state.after === null,          // new rows arrive on the first page
 *     resetKey: JSON.stringify(list.filters) + list.state.q,   // a new question starts over
 *   });
 *   <ListPage rows={held.rows} newRows={{ count: held.newCount, onReveal: held.reveal }} … />
 */
export function useHeldRows<TRow>(
  rows: TRow[],
  getRowId: (row: TRow) => string,
  { live = true, resetKey = "" }: { live?: boolean; resetKey?: string } = {}
): { rows: TRow[]; newCount: number; reveal: () => void } {
  const [held, setHeld] = React.useState<{ key: string; ids: ReadonlySet<string> } | null>(null);
  const ids = rows.map(getRowId);
  const current = live && held?.key === resetKey ? held.ids : null;

  // Adopt the first rows of each question as the held set. Set during
  // render (React's "adjust state on a prop change"), not in an effect, so
  // no frame shows the new question's rows as "new".
  if (live && !current && ids.length > 0) setHeld({ key: resetKey, ids: new Set(ids) });

  if (!current) return { rows, newCount: 0, reveal: () => {} };
  const visible = rows.filter((r) => current.has(getRowId(r)));
  return {
    rows: visible,
    newCount: rows.length - visible.length,
    reveal: () => setHeld({ key: resetKey, ids: new Set(ids) }),
  };
}
