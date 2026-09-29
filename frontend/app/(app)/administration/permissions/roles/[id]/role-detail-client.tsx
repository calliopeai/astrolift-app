"use client";

import { RoleDetailScreen } from "@/components/screens/administration/permissions/RoleDetailScreen";
import { RoleHoldersTab } from "@/components/screens/administration/permissions/RoleHoldersTab";
import type { RoleTab } from "@/components/screens/administration/permissions/roles-routes";
import { grantRoleHref } from "@/components/screens/administration/permissions/roles-routes";
import { useRoleDetail } from "@/components/screens/administration/permissions/use-role-detail";
import { useRoleHolders } from "@/components/screens/administration/permissions/use-role-holders";
import type { AstroliftRole } from "@/graphql/identity/identity.types";

/** A role's page; the Holders list mounts, and queries, only on its own tab. */
export function RoleDetailClient({ id, tab }: { id: string; tab: RoleTab }) {
  const detail = useRoleDetail(id);
  return (
    <RoleDetailScreen
      {...detail}
      tab={tab}
      holders={tab === "holders" && detail.role ? <RoleHoldersClient role={detail.role} /> : null}
    />
  );
}

function RoleHoldersClient({ role }: { role: AstroliftRole }) {
  return (
    <RoleHoldersTab
      {...useRoleHolders(role)}
      roleName={role.name}
      grantHref={grantRoleHref(role.id)}
    />
  );
}
