"use client";

import { EditTeamSheet, type EditTeamSheetProps } from "@/components/screens/teams/EditTeamSheet";
import { useEditTeam } from "@/components/screens/teams/use-edit-team";

type Props = Pick<EditTeamSheetProps, "open" | "onOpenChange" | "team">;

export function EditTeamDialog(props: Props) {
  const edit = useEditTeam({ open: props.open, team: props.team });
  return <EditTeamSheet {...props} {...edit} />;
}
