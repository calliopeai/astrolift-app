"use client";

import {
  EditProjectSheet,
  type EditProjectSheetProps,
} from "@/components/screens/projects/EditProjectSheet";
import { useEditProject } from "@/components/screens/projects/use-edit-project";

type Props = Pick<EditProjectSheetProps, "open" | "onOpenChange" | "project">;

export function EditProjectDialog(props: Props) {
  const edit = useEditProject({ open: props.open, project: props.project });
  return <EditProjectSheet {...props} {...edit} />;
}
