"use client";

import * as React from "react";

/**
 * The viewer's granted permission slugs, provided once for the tree.
 *
 * `useMyPermissions` (and so `<Can>`) reads this when it is present, which is
 * what lets a component that gates on a permission render in Storybook: the
 * catalog provides a set from its toolbar, the app provides the real one.
 * Without a provider the hook queries, exactly as before.
 */
export interface PermissionsValue {
  granted: ReadonlySet<string>;
  loading: boolean;
}

export const PermissionsContext = React.createContext<PermissionsValue | null>(null);

export function PermissionsProvider({
  value,
  children,
}: {
  value: PermissionsValue;
  children: React.ReactNode;
}) {
  return <PermissionsContext.Provider value={value}>{children}</PermissionsContext.Provider>;
}
