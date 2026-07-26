"use client";

import { ActivityIcon, ShieldCheckIcon, UsersIcon } from "lucide-react";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import {
  FEATURE_FLAG_ADMIN_PERMISSIONS,
  useFeatureFlag,
} from "@/graphql/server/server.hooks";

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
  // (#1206), mirroring the cost/quotas surface gates. With the flag off the
  // nav entry is filtered out and a direct hit renders nothing. The flag
  // reads false until the server-info handshake resolves; only the active
  // tab mounts, so no roles/bindings queries fire while gated (the tab
  // components are never mounted when this returns null).
  if (!enabled) {
    return null;
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
