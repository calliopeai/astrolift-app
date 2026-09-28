import * as React from "react";

/**
 * Shared chrome for the /settings subtree.
 *
 * Uses the same horizontal subnav pattern as /resources and
 * /administration — the previous left-rail sidebar caused a double-nav
 * when combined with the main AppSidebar.
 */
export function SettingsShell({
  subnav,
  children,
}: {
  subnav: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 flex-col">
      {subnav}
      <div className="flex-1">{children}</div>
    </div>
  );
}
