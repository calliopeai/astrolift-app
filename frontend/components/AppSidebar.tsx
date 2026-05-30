"use client";

import * as React from "react";

import { AstroliftNav } from "@/components/AstroliftNav";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { NavTree } from "@/components/NavTree";
import { NavUser } from "@/components/NavUser";
import { OrgSwitcher } from "@/components/OrgSwitcher";
import { RegisterAppButton } from "@/components/RegisterAppButton";
import { StatusPageLink } from "@/components/StatusPageLink";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import type { CurrentUser } from "@/graphql/user/user.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

type AppSidebarProps = React.ComponentProps<typeof Sidebar> & {
  ssrUser: CurrentUser | null;
};

export function AppSidebar({ ssrUser, ...props }: AppSidebarProps) {
  // Audience split: platform admins (any permission granted) see the full
  // BROCS nav. End users with no platform permissions see only the workspace
  // tree + Register App CTA — a clean onboarding surface, not a broken
  // admin view. The Register App button is always visible to both audiences.
  const { hasAnyAccess, loading } = useMyPermissions();
  const isAdmin = hasAnyAccess;

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <OrgSwitcher />
      </SidebarHeader>
      <SidebarContent>
        {/* Tenant workspace tree — visible to all authenticated users. */}
        <NavTree />

        {/* Register App CTA — always visible, primary onboarding action. */}
        <RegisterAppButton />

        {/* BROCS platform nav — admin-only. End users with no platform
            permissions see only the workspace tree + CTA above. While
            permissions are loading we render the nav to avoid layout
            shift; items filter themselves via their own permission checks. */}
        {(loading || isAdmin) && (
          <>
            <SidebarSeparator />
            <AstroliftNav />
          </>
        )}
      </SidebarContent>
      <SidebarFooter>
        <LanguageSwitcher />
        <ThemeToggle />
        <StatusPageLink />
        <SidebarSeparator />
        <NavUser ssrUser={ssrUser} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
