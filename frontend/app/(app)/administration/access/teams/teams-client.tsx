"use client";

import { ACCESS_FUNCTIONS } from "@/components/screens/administration/access/access-nav";
import { AccessLandingPanel } from "@/components/screens/administration/access/team-memberships/AccessLandingClient";
import { useTeamAccessNavigation } from "@/components/screens/administration/access/team-memberships/use-team-access-navigation";
import { useTeamMembershipScope } from "@/components/screens/administration/access/team-memberships/use-team-membership-scope";
import { TeamsScreen } from "@/components/screens/teams/TeamsScreen";
import { useTeams } from "@/components/screens/teams/use-teams";

import { CreateTeamDialog } from "./create-team-dialog";
import { EditTeamDialog } from "./edit-team-dialog";

export function TeamsClient() {
  const scope = useTeamMembershipScope();
  return <TeamsContext key={scope.key} ready={scope.ready} />;
}

function TeamsContext({ ready }: { ready: boolean }) {
  const nav = useTeamAccessNavigation(ready);
  const navigation = nav.navigation;
  const teams = useTeams({ freshOnly: true, skip: !ready || !navigation?.canViewTeams });
  const gates = {
    people: "canViewPeople",
    teams: "canViewTeams",
    roles: "canViewRoles",
    policies: "canViewPolicies",
    check: "canCheckAccess",
  } as const;
  if (!ready || nav.loading || !navigation?.canViewTeams)
    return (
      <AccessLandingPanel
        loading={!ready || nav.loading}
        error={nav.error?.message ?? null}
        onRetry={() => {
          void nav.refetch().catch(() => {});
        }}
      />
    );
  return (
    <TeamsScreen
      {...teams}
      accessFunctions={ACCESS_FUNCTIONS.filter((item) => navigation?.[gates[item.key]]).map(
        (item) => item.key
      )}
      renderCreateDialog={(props) => <CreateTeamDialog {...props} />}
      renderEditDialog={(props) => <EditTeamDialog {...props} />}
    />
  );
}
