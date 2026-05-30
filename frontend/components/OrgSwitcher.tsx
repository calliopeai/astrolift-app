"use client";

import Image from "next/image";
import Link from "next/link";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

/**
 * Single-tenant organization header for the sidebar.
 *
 * Astrolift's data model keeps the Organization entity as a strong
 * tenant boundary, but each install runs as exactly one org. The
 * sidebar header just identifies which one — there's nothing to
 * switch to. The Astrolift mark + wordmark is the persistent brand
 * identity; the org name/slug sits underneath as the tenancy context.
 */
export function OrgSwitcher() {
  const { org, loading } = useActiveOrg();

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton size="lg" asChild>
          <Link href="/dashboard" className="flex items-center gap-2">
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
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
