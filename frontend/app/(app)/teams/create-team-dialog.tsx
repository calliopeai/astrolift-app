"use client";

import {
  CreateTeamSheet,
  type CreateTeamSheetProps,
} from "@/components/screens/teams/CreateTeamSheet";
import { useCreateTeam } from "@/components/screens/teams/use-create-team";

type Props = Pick<CreateTeamSheetProps, "open" | "onOpenChange">;

export function CreateTeamDialog(props: Props) {
  return <CreateTeamSheet {...props} {...useCreateTeam()} />;
}
