"use client";

import { TeamsCardView } from "@/components/screens/apps/overview/TeamsCard";
import {
  type UseTeamsCardArgs,
  useTeamsCard,
} from "@/components/screens/apps/overview/use-teams-card";

/** Team access card: the hook's data rendered by the view. */
export function TeamsCard(props: UseTeamsCardArgs) {
  return <TeamsCardView {...useTeamsCard(props)} />;
}
