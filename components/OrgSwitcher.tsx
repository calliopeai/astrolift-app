"use client";

import { useQuery } from "@apollo/client/react";
import { BuildingIcon } from "lucide-react";
import * as React from "react";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import type { AstroliftOrganization } from "@/graphql/identity/identity.types";
import { setActiveOrgGuid } from "@/lib/identity/active-org";

interface OrgListData {
  astroliftOrganizations: AstroliftOrganization[];
}

/**
 * Single-tenant organization header.
 *
 * Astrolift's data model keeps the Organization entity as a strong
 * tenant boundary, but each install runs as exactly one org. The
 * sidebar header just identifies which one — there's nothing to
 * switch to. We still pin the active org's guid in a cookie so the
 * X-Astrolift-Organization header on every GraphQL request is set
 * even before the single-membership inference kicks in.
 */
export function OrgSwitcher() {
  const { data, loading } = useQuery<OrgListData>(LIST_ORGANIZATIONS);
  const org = data?.astroliftOrganizations[0];

  React.useEffect(() => {
    if (org) setActiveOrgGuid(org.id);
  }, [org]);

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton size="lg" className="cursor-default">
          <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
            <BuildingIcon className="size-4" />
          </div>
          <div className="grid flex-1 text-left text-sm leading-tight">
            <span className="truncate font-semibold">
              {loading ? "…" : org?.name ?? "Astrolift"}
            </span>
            <span className="truncate text-xs text-muted-foreground">
              {org?.slug ?? "control plane"}
            </span>
          </div>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
