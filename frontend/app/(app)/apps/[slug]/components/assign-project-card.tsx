"use client";

import { AssignProjectCardView } from "@/components/screens/apps/overview/AssignProjectCard";
import {
  type UseAssignProjectArgs,
  useAssignProject,
} from "@/components/screens/apps/overview/use-assign-project";

/** Project assignment card (#391): the hook's data rendered by the view. */
export function AssignProjectCard(props: UseAssignProjectArgs) {
  return <AssignProjectCardView {...useAssignProject(props)} />;
}
