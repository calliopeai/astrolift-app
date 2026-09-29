"use client";

import { ProjectsScreen } from "@/components/screens/projects/ProjectsScreen";
import { useProjects } from "@/components/screens/projects/use-projects";

import { CreateProjectDialog } from "./create-project-dialog";
import { EditProjectDialog } from "./edit-project-dialog";

export function ProjectsClient() {
  return (
    <ProjectsScreen
      {...useProjects()}
      renderCreateDialog={(p) => <CreateProjectDialog {...p} />}
      renderEditDialog={(p) => <EditProjectDialog {...p} />}
    />
  );
}
