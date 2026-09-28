"use client";

import { ActivityIcon, ShieldCheckIcon, UsersIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { PageShell } from "@/components/PageShell";

export type PermissionsTabKey = "roles" | "assignments" | "diagnostics";

const TABS: ReadonlyArray<{ key: PermissionsTabKey; label: string; icon: React.ReactNode }> = [
  { key: "roles", label: "Roles", icon: <ShieldCheckIcon className="size-4" /> },
  { key: "assignments", label: "Assignments", icon: <UsersIcon className="size-4" /> },
  { key: "diagnostics", label: "Diagnostics", icon: <ActivityIcon className="size-4" /> },
];

export interface PermissionsScreenProps {
  /** `admin.permissions_enabled`; while false the screen says so and mounts no tab. */
  enabled: boolean;
  /**
   * Each tab's body. Only the active one is rendered, so a container passed
   * here runs its queries only while its tab is shown.
   */
  roles: React.ReactNode;
  assignments: React.ReactNode;
  diagnostics: React.ReactNode;
  initialTab?: PermissionsTabKey;
}

/** Administration › Permissions: roles, assignments and diagnostics behind one tab strip. */
export function PermissionsScreen({
  enabled,
  roles,
  assignments,
  diagnostics,
  initialTab = "roles",
}: PermissionsScreenProps) {
  const [tab, setTab] = React.useState<PermissionsTabKey>(initialTab);

  // Screen gate: Permissions ships behind `admin.permissions_enabled`
  // (#1206). The nav entry is filtered out while the flag is off, but the
  // route stays reachable, so this used to `return null` and a direct hit
  // rendered a blank page — indistinguishable from the screen being broken.
  // It says so instead, and points at the flipper that turns it on.
  //
  // The flag reads false until the server-info handshake resolves, and the
  // tab bodies never mount from here, so no roles or bindings queries
  // fire while gated.
  if (!enabled) {
    return (
      <PageShell
        title="Permissions"
        description="Define roles and their permission sets, assign roles to users, and inspect effective access."
      >
        <div className="text-muted-foreground max-w-prose rounded-md border border-dashed p-6 text-sm">
          <p className="text-foreground font-medium">
            This console is turned off for this install.
          </p>
          <p className="mt-2">
            Permissions ships behind the{" "}
            <code className="font-mono">admin.permissions_enabled</code> flag. Turn it on under{" "}
            <Link className="underline underline-offset-2" href="/administration/features">
              Administration &rsaquo; Features
            </Link>
            .
          </p>
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell
      title="Permissions"
      description="Define roles and their permission sets, assign roles to users, and inspect effective access."
    >
      <div
        role="tablist"
        aria-label="Permissions tabs"
        className="bg-muted/40 inline-flex flex-wrap rounded-md border p-1"
      >
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(t.key)}
              className={
                "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm font-medium transition " +
                (active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {t.icon}
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === "roles" && roles}
      {tab === "assignments" && assignments}
      {tab === "diagnostics" && diagnostics}
    </PageShell>
  );
}
