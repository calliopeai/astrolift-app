"use client";

import { BookOpenIcon, DownloadIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { AstroliftNav } from "@/components/AstroliftNav";
import { NavTree } from "@/components/NavTree";
import { NavUser } from "@/components/NavUser";
import { OrgSwitcher } from "@/components/OrgSwitcher";
import { RegisterAppButton } from "@/components/RegisterAppButton";
import { StatusPageLink } from "@/components/StatusPageLink";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import type { CurrentUser } from "@/graphql/user/user.types";

type AppSidebarProps = React.ComponentProps<typeof Sidebar> & {
  ssrUser: CurrentUser | null;
};

export function AppSidebar({ ssrUser, ...props }: AppSidebarProps) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <OrgSwitcher />
      </SidebarHeader>
      <SidebarContent>
        {/* Tenant workspace tree — visible to all authenticated users. */}
        <NavTree />

        {/* Register App CTA — gated on the Apps module's `canCreate`
            (spec 36 §1.3). The component reads `me.modules` itself and
            renders nothing when the viewer can't create apps. */}
        <RegisterAppButton />

        {/* Module switcher (spec 36 §1.2). Each module self-gates on the
            server-authoritative `me.modules.canView`; Dashboard always
            renders, so the nav is shown for every authenticated viewer and
            simply omits the modules they can't view. */}
        <SidebarSeparator />
        <AstroliftNav />
      </SidebarContent>
      <SidebarFooter>
        {/* Utility row — Docs and Downloads at the bottom of every sidebar.
            Removed from the Manage nav section; footer placement keeps them
            accessible without consuming a nav section slot. */}
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="sm" tooltip="Docs">
              <Link href="/documentation">
                <BookOpenIcon />
                <span>Docs</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="sm" tooltip="Downloads">
              <Link href="/downloads">
                <DownloadIcon />
                <span>Downloads</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        <SidebarSeparator />
        <StatusPageLink />
        <SidebarSeparator />
        <NavUser ssrUser={ssrUser} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
