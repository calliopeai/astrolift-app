import type * as React from "react";

/**
 * Chromeless frame for the (bare) group: a surface that is the whole window
 * (the popped-out pod terminal, #1246). No sidebar, header or tab bar.
 */
export function BareShell({ children }: { children: React.ReactNode }) {
  return <div className="bg-background h-svh w-svw overflow-hidden">{children}</div>;
}
