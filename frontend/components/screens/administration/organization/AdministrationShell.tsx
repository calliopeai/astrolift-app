import type * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";

import { adminCrumbs } from "../insights/header";

export interface AdministrationShellProps {
  /** The Admin function on screen (a `NAV` key), checked in the `Admin ▾` switcher. */
  fnKey: string;
  title: string;
  /** One or two sentences under the header on what the page is for. */
  description?: React.ReactNode;
  primaryAction?: React.ReactNode;
  menu?: React.ReactNode;
  children: React.ReactNode;
}

/**
 * An Admin page's frame (spec 44 §4.4): `Admin ▾ › <function>`, the title
 * row, then the page. The first crumb switches between the Admin functions
 * in the rail's order (Organization, Infrastructure, Usage & governance,
 * Platform signals), which is the job the old Administration tab strip did.
 */
export function AdministrationShell({
  fnKey,
  title,
  description,
  primaryAction,
  menu,
  children,
}: AdministrationShellProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-2">
        <ShellHeader
          crumbs={adminCrumbs(fnKey, title)}
          title={title}
          primaryAction={primaryAction}
          menu={menu}
        />
        {description && (
          <p className="text-muted-foreground max-w-2xl text-sm [overflow-wrap:anywhere]">
            {description}
          </p>
        )}
      </div>
      {children}
    </div>
  );
}
