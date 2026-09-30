"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject } from "@/graphql/identity/identity.types";

interface ProjectsResponse {
  astroliftProjects: AstroliftProject[];
}

export function scopedWizardProjects(projects: AstroliftProject[], orgId: string) {
  return projects.filter(
    (project) => Boolean(orgId) && project.organization.id === orgId && !project.deletedAt
  );
}

/** The actual destination rows; a nonempty stale ID is not a selected project. */
export function useWizardProjects() {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const query = useQuery<ProjectsResponse>(LIST_PROJECTS, {
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const allProjects = React.useMemo(
    () => scopedWizardProjects(query.data?.astroliftProjects ?? [], orgId),
    [query.data, orgId]
  );
  const confirmDestination = async (projectId: string) => {
    if (!orgId)
      throw new Error("The organization is not available. Select a project before registering.");
    const fresh = await query.refetch();
    const project = scopedWizardProjects(fresh.data?.astroliftProjects ?? [], orgId).find(
      (row) => row.id === projectId
    );
    if (!project)
      throw new Error(
        "The selected project is no longer available in this organization. Select a project before registering."
      );
    return project;
  };
  return { allProjects, orgId, loading: query.loading, error: query.error, confirmDestination };
}
