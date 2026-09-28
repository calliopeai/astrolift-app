"use client";

import {
  BarChart3Icon,
  BuildingIcon,
  FileBoxIcon,
  FlagIcon,
  KeyIcon,
  ScaleIcon,
  ScrollTextIcon,
  ShieldCheckIcon,
  UsersIcon,
  UsersRoundIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { cn } from "@/lib/utils";

import type { useAdministrationSubnav } from "./use-administration-subnav";

export type AdministrationSubnavProps = ReturnType<typeof useAdministrationSubnav>;

interface SubnavLink {
  href: string;
  label: string;
  icon: React.ReactNode;
}

// Permissions ships behind the `admin.permissions_enabled` runtime flag
// (#1206) — the actionable Roles / Assignments / Diagnostics screen. Lives
// in the Organization group but filtered out below unless the install
// enables the flag, so the href is named here for that gate.
const PERMISSIONS_HREF = "/administration/permissions";

// Mirrors the sidebar's four-group Admin IA (Organization ·
// Infrastructure · Usage & Governance · Platform Signals) for the
// groups that live under the administration control plane. The
// subnav stays flat — groups render in order with a separator
// between them. /tokens stays top-level canonical (#893).
const GROUPS: SubnavLink[][] = [
  // Organization — the org itself, who's in it, and who can do what.
  [
    {
      href: "/administration/organization",
      label: "Organization",
      icon: <BuildingIcon className="size-4" />,
    },
    { href: "/administration/members", label: "Members", icon: <UsersIcon className="size-4" /> },
    { href: "/administration/teams", label: "Teams", icon: <UsersRoundIcon className="size-4" /> },
    {
      href: "/administration/projects",
      label: "Projects",
      icon: <FileBoxIcon className="size-4" />,
    },
    { href: "/administration/policies", label: "Policies", icon: <ScaleIcon className="size-4" /> },
    // Permissions is the actionable Roles / Assignments / Diagnostics screen
    // (#1206), gated behind `admin.permissions_enabled` — filtered out below
    // unless the install turns it on in /administration/features.
    { href: PERMISSIONS_HREF, label: "Permissions", icon: <ShieldCheckIcon className="size-4" /> },
  ],
  // Usage & Governance — spend, limits, usage, and the audit trail.
  [
    {
      href: "/administration/metrics",
      label: "Metrics",
      icon: <BarChart3Icon className="size-4" />,
    },
    { href: "/tokens", label: "API Keys", icon: <KeyIcon className="size-4" /> },
    { href: "/administration/audit", label: "Audit", icon: <ScrollTextIcon className="size-4" /> },
    { href: "/administration/features", label: "Features", icon: <FlagIcon className="size-4" /> },
  ],
];

export function AdministrationSubnav({ pathname, permissionsEnabled }: AdministrationSubnavProps) {
  const groups = React.useMemo(
    () =>
      GROUPS.map((group) =>
        group.filter((link) => link.href !== PERMISSIONS_HREF || permissionsEnabled)
      ),
    [permissionsEnabled]
  );

  return (
    <nav
      aria-label="Administration sub-navigation"
      className="border-border bg-muted/30 sticky top-0 z-10 flex flex-wrap items-center gap-1 border-b px-4 py-2 backdrop-blur"
    >
      {groups.map((group, groupIdx) => (
        <React.Fragment key={groupIdx}>
          {groupIdx > 0 && <div aria-hidden className="bg-border mx-1 h-4 w-px" />}
          {group.map((link) => {
            const active = pathname === link.href || pathname.startsWith(link.href + "/");
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm transition-colors",
                  active
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
                aria-current={active ? "page" : undefined}
              >
                {link.icon}
                <span>{link.label}</span>
              </Link>
            );
          })}
        </React.Fragment>
      ))}
    </nav>
  );
}
