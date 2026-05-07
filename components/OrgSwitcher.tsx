"use client";

import { useQuery } from "@apollo/client/react";
import Image from "next/image";
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
 *
 * The Astrolift mark + wordmark is the persistent brand identity;
 * the org name/slug sits underneath as the tenancy context.
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
          <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary/10 ring-1 ring-sidebar-primary/30">
            <Image
              src="/logo.svg"
              alt="Astrolift"
              width={20}
              height={20}
              priority
            />
          </div>
          <div className="grid flex-1 text-left text-sm leading-tight">
            <span className="truncate font-semibold tracking-tight">
              Astrolift
            </span>
            <span className="truncate text-xs text-muted-foreground">
              {loading ? "…" : (org?.name ?? "control plane")}
            </span>
          </div>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
