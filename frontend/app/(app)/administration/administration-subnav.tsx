"use client";

import {
  BarChart3Icon,
  BuildingIcon,
  FileBoxIcon,
  FlagIcon,
  GaugeIcon,
  KeyIcon,
  ScaleIcon,
  ScrollTextIcon,
  UsersIcon,
  UsersRoundIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import {
  FEATURE_FLAG_ADMIN_QUOTAS,
  useFeatureFlag,
} from "@/graphql/server/server.hooks";
import { cn } from "@/lib/utils";

interface SubnavLink {
  href: string;
  label: string;
  icon: React.ReactNode;
}

// Quotas ships behind the `admin.quotas_enabled` runtime flag (#1204). It
// lives in GROUPS but is filtered out below unless the install has enabled
// the flag, so the href is named here for that gate.
const QUOTAS_HREF = "/administration/quotas";

// Mirrors the sidebar's four-group Admin IA (Organization ·
// Infrastructure · Usage & Governance · Platform Signals) for the
// groups that live under the administration control plane. The
// subnav stays flat — groups render in order with a separator
// between them. /tokens stays top-level canonical (#893).
const GROUPS: SubnavLink[][] = [
  // Organization — the org itself, who's in it, and who can do what.
  [
    { href: "/administration/organization", label: "Organization", icon: <BuildingIcon className="size-4" /> },
    { href: "/administration/members", label: "Members", icon: <UsersIcon className="size-4" /> },
    { href: "/administration/teams", label: "Teams", icon: <UsersRoundIcon className="size-4" /> },
    { href: "/administration/projects", label: "Projects", icon: <FileBoxIcon className="size-4" /> },
    { href: "/administration/policies", label: "Policies", icon: <ScaleIcon className="size-4" /> },
    // Permissions hidden pending a proper redesign (backlog). Route still
    // exists but is unlinked so operators don't land on a non-actionable page.
  ],
  // Usage & Governance — spend, limits, usage, and the audit trail.
  [
    // Cost hidden pending a real implementation (backlog); its route remains
    // but is unlinked. Quotas is implemented but gated behind the
    // `admin.quotas_enabled` runtime flag — filtered out below unless the
    // install turns it on in /administration/features.
    { href: QUOTAS_HREF, label: "Quotas", icon: <GaugeIcon className="size-4" /> },
    { href: "/administration/metrics", label: "Metrics", icon: <BarChart3Icon className="size-4" /> },
    { href: "/tokens", label: "API Keys", icon: <KeyIcon className="size-4" /> },
    { href: "/administration/audit", label: "Audit", icon: <ScrollTextIcon className="size-4" /> },
    { href: "/administration/features", label: "Features", icon: <FlagIcon className="size-4" /> },
  ],
];

export function AdministrationSubnav() {
  const pathname = usePathname();

  // Quotas stays hidden until the install enables `admin.quotas_enabled`
  // (#1204), mirroring the zentinelle.enabled surface gate. Until the
  // server-info handshake resolves the flag reads false, so the entry never
  // flickers in before the answer arrives.
  const quotasEnabled = useFeatureFlag(FEATURE_FLAG_ADMIN_QUOTAS);
  const groups = React.useMemo(
    () =>
      GROUPS.map((group) =>
        group.filter((link) => link.href !== QUOTAS_HREF || quotasEnabled),
      ),
    [quotasEnabled],
  );

  return (
    <nav
      aria-label="Administration sub-navigation"
      className="border-border bg-muted/30 sticky top-0 z-10 flex flex-wrap items-center gap-1 border-b px-4 py-2 backdrop-blur"
    >
      {groups.map((group, groupIdx) => (
        <React.Fragment key={groupIdx}>
          {groupIdx > 0 && (
            <div aria-hidden className="bg-border mx-1 h-4 w-px" />
          )}
          {group.map((link) => {
            const active =
              pathname === link.href || pathname.startsWith(link.href + "/");
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm transition-colors",
                  active
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
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
