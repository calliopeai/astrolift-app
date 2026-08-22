"use client";

import { ActivityIcon, ShieldCheckIcon, UsersIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { FEATURE_FLAG_ADMIN_PERMISSIONS, useFeatureFlag } from "@/graphql/server/server.hooks";

import { AssignmentsTab } from "./assignments-tab";
import { PermissionsDiagnosticsContent } from "./permissions-diagnostics-client";
import { RolesTab } from "./roles-tab";

type TabKey = "roles" | "assignments" | "diagnostics";

const TABS: ReadonlyArray<{ key: TabKey; label: string; icon: React.ReactNode }> = [
  { key: "roles", label: "Roles", icon: <ShieldCheckIcon className="size-4" /> },
  { key: "assignments", label: "Assignments", icon: <UsersIcon className="size-4" /> },
  { key: "diagnostics", label: "Diagnostics", icon: <ActivityIcon className="size-4" /> },
];

export function PermissionsClient() {
  const enabled = useFeatureFlag(FEATURE_FLAG_ADMIN_PERMISSIONS);
  const [tab, setTab] = React.useState<TabKey>("roles");

  // Screen gate: Permissions ships behind `admin.permissions_enabled`
  // (#1206). The nav entry is filtered out while the flag is off, but the
  // route stays reachable, so this used to `return null` and a direct hit
  // rendered a blank page — indistinguishable from the screen being broken.
  // It says so instead, and points at the flipper that turns it on.
  //
  // The flag reads false until the server-info handshake resolves, and the
  // tab components never mount from here, so no roles or bindings queries
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

      {tab === "roles" && <RolesTab />}
      {tab === "assignments" && <AssignmentsTab />}
      {tab === "diagnostics" && <PermissionsDiagnosticsContent />}
    </PageShell>
  );
}
