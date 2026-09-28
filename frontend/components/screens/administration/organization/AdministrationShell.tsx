import type * as React from "react";

/** The administration frame: the sticky subnav above the active section. */
export function AdministrationShell({
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
