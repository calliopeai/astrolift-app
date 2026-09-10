"use client";

import { BookOpenIcon, DownloadIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { AstroliftNav } from "@/components/AstroliftNav";
import { NavTree } from "@/components/NavTree";
import { NavUser } from "@/components/NavUser";
import { OrgSwitcher } from "@/components/OrgSwitcher";
import { RegisterAppButton } from "@/components/RegisterAppButton";
import { SidebarDrawer } from "@/components/SidebarDrawer";
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

const WORKSPACE_DRAWER_KEY = "astrolift.sidebar.drawer.workspace.v1";
const PLATFORM_DRAWER_KEY = "astrolift.sidebar.drawer.platform.v1";

type AppSidebarProps = React.ComponentProps<typeof Sidebar> & {
  ssrUser: CurrentUser | null;
};

export function AppSidebar({ ssrUser, ...props }: AppSidebarProps) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <OrgSwitcher />
      </SidebarHeader>
      {/* Two drawers, not one scroll container (#1728). The tree grows
          with the estate and the platform nav is a fixed set of
          destinations; sharing one scroll region meant a real org's tree
          pushed Build / Run / Observe / Secure off the bottom of the
          rail, and the only collapse control took both away at once. */}
      <SidebarContent className="overflow-hidden">
        {/* Tenant workspace tree — visible to all authenticated users.
            Takes the leftover height and scrolls inside it, so however
            deep the estate gets it never displaces the platform nav. */}
        <SidebarDrawer title="Workspace" storageKey={WORKSPACE_DRAWER_KEY} grow>
          <NavTree />

          {/* Register App CTA — gated on the Apps module's `canCreate`
              (spec 36 §1.3). The component reads `me.modules` itself and
              renders nothing when the viewer can't create apps. It
              creates a node in the tree, so it belongs to this drawer. */}
          <RegisterAppButton />
        </SidebarDrawer>

        <SidebarSeparator className="shrink-0" />

        {/* Module switcher (spec 36 §1.2). Each module self-gates on the
            server-authoritative `me.modules.canView`; Dashboard always
            renders, so the nav is shown for every authenticated viewer and
            simply omits the modules they can't view.

            Capped at half the rail: expanding Admin's sub-groups scrolls
            within the drawer instead of squeezing the tree to nothing. */}
        <SidebarDrawer
          title="Platform"
          storageKey={PLATFORM_DRAWER_KEY}
          maxHeightClass="max-h-[50%]"
        >
          <AstroliftNav />
        </SidebarDrawer>
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
