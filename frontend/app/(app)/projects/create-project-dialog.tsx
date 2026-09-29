"use client";

import {
  CreateProjectSheet,
  type CreateProjectSheetProps,
} from "@/components/screens/projects/CreateProjectSheet";
import { useCreateProject } from "@/components/screens/projects/use-create-project";

type Props = Pick<CreateProjectSheetProps, "open" | "onOpenChange" | "teams">;

export function CreateProjectDialog(props: Props) {
  return <CreateProjectSheet {...props} {...useCreateProject()} />;
}
