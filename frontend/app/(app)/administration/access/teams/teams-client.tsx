"use client";

import { TeamsScreen } from "@/components/screens/teams/TeamsScreen";
import { useTeams } from "@/components/screens/teams/use-teams";

import { CreateTeamDialog } from "./create-team-dialog";
import { EditTeamDialog } from "./edit-team-dialog";

export function TeamsClient() {
  return (
    <TeamsScreen
      {...useTeams()}
      renderCreateDialog={(props) => <CreateTeamDialog {...props} />}
      renderEditDialog={(props) => <EditTeamDialog {...props} />}
    />
  );
}
