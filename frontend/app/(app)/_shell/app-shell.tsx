"use client";

import { useQuery } from "@apollo/client/react";
import { usePathname } from "next/navigation";
import * as React from "react";

import { CommandPalette } from "@/components/CommandPalette";
import { ElevationIndicator } from "@/components/ElevationIndicator";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { NotificationsBell } from "@/components/NotificationsBell";
import { AppShell } from "@/components/shell/AppShell";
import { BrandMark } from "@/components/shell/BrandMark";
import { MainRail } from "@/components/shell/MainRail";
import { OrgMenu } from "@/components/shell/OrgMenu";
import { ProjectsRail } from "@/components/shell/ProjectsRail";
import { StepUpPrompt } from "@/components/StepUpPrompt";
import { UserMenu } from "@/components/shell/UserMenu";
import { useCommandPalette } from "@/components/use-command-palette";
import { useKeyboardShortcuts } from "@/components/use-keyboard-shortcuts";
import { useStepUp } from "@/components/use-step-up";
import { useWayfinding } from "@/components/use-wayfinding";
import { WayfindingBubble } from "@/components/WayfindingBubble";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { GET_MY_PROFILE, LIST_NAV_TREE } from "@/graphql/identity/identity.queries";
import type { AstroliftMyProfile, AstroliftNavTree } from "@/graphql/identity/identity.types";
import { useMe, useModules } from "@/graphql/user/user.hooks";
import { useElevation } from "@/hooks/use-elevation";
import { useMyNotifications } from "@/hooks/use-my-notifications";
import { clearToken } from "@/lib/auth/token-store";
import { setSentryUser } from "@/lib/sentry";
import { activeFor, NAV, visibleNav } from "@/lib/shell/nav-model";
import { projectsFromNavTree } from "@/lib/shell/projects-from-nav-tree";
import { useRailState } from "@/lib/shell/use-rail-state";

import { PlatformIncidentBannerContainer } from "./platform-incident-banner";

/**
 * The app shell's wiring (spec 44 §4.2): every piece of data the pure shell
 * components show, gathered here and nowhere else.
 */
export function AppShellContainer({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const { canView } = useModules();
  const { org, loading: orgLoading } = useActiveOrg();
  const { user } = useMe();
  const { data: profileData } = useQuery<{ astroliftMyProfile: AstroliftMyProfile | null }>(
    GET_MY_PROFILE,
    { fetchPolicy: "cache-first" }
  );
  const { data: treeData, loading: treeLoading } = useQuery<{
    astroliftNavTree: AstroliftNavTree | null;
  }>(LIST_NAV_TREE, { fetchPolicy: "cache-and-network" });
  const notifications = useMyNotifications();
  const elevation = useElevation();
  const shortcuts = useKeyboardShortcuts();
  const wayfinding = useWayfinding();
  const stepUp = useStepUp();
  const palette = useCommandPalette();

  const [mainCollapsed, setMainCollapsed] = useRailState("astrolift.shell.main", () => false);
  const [projectsCollapsed, setProjectsCollapsed] = useRailState(
    "astrolift.shell.projects",
    () => window.innerWidth < 1280
  );

  React.useEffect(() => {
    setSentryUser(user ?? null);
  }, [user]);

  const nav = React.useMemo(() => visibleNav(NAV, canView), [canView]);
  const projects = React.useMemo(
    () => projectsFromNavTree(treeData?.astroliftNavTree, canView),
    [treeData, canView]
  );

  const profile = profileData?.astroliftMyProfile ?? null;
  const fullName =
    profile && (profile.firstName || profile.lastName)
      ? `${profile.firstName ?? ""} ${profile.lastName ?? ""}`.trim()
      : profile?.username || user?.profile?.username || "User";

  function signOut() {
    setSentryUser(null);
    clearToken();
    window.location.href = `${process.env.NEXT_PUBLIC_API_ROOT ?? ""}/app/auth1/logout`;
  }

  return (
    <AppShell
      banner={<PlatformIncidentBannerContainer />}
      onToggleMain={() => setMainCollapsed(!mainCollapsed)}
      onToggleProjects={() => setProjectsCollapsed(!projectsCollapsed)}
      mainRail={
        <MainRail
          nav={nav}
          active={activeFor(nav, pathname)}
          collapsed={mainCollapsed}
          onCollapsedChange={setMainCollapsed}
          header={<BrandMark />}
          headerCollapsed={<BrandMark collapsed />}
          footer={
            <>
              <ElevationIndicator {...elevation} />
              <NotificationsBell {...notifications} />
              <UserMenu fullName={fullName} email={profile?.email ?? ""} onSignOut={signOut} />
            </>
          }
        />
      }
      projectsRail={
        <ProjectsRail
          org={<OrgMenu name={org?.name} loading={orgLoading} />}
          projects={projects}
          loading={treeLoading && projects.length === 0}
          activeHref={pathname}
          allProjectsHref="/projects"
          collapsed={projectsCollapsed}
          onCollapsedChange={setProjectsCollapsed}
        />
      }
    >
      {children}
      <CommandPalette {...palette} />
      <KeyboardShortcuts {...shortcuts} />
      <StepUpPrompt {...stepUp} />
      {/* Read-only help (#1101). Global chrome, not entitlement-gated: the
          people who most need to ask where a thing is are the ones who have
          seen the least of the product. */}
      <WayfindingBubble {...wayfinding} />
    </AppShell>
  );
}
