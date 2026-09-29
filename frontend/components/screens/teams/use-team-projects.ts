"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject } from "@/graphql/identity/identity.types";

/**
 * The projects under a team: what a grant at the team reaches (design 3.2),
 * for the Access tab's summary. LIST_PROJECTS is the org's project list, so
 * this is usually a cache read of the one the nav already made.
 */
export function useTeamProjects(slug: string) {
  const { data, loading, error, refetch } = useQuery<{ astroliftProjects: AstroliftProject[] }>(
    LIST_PROJECTS
  );
  const projects = (data?.astroliftProjects ?? []).filter((p) => p.team.slug === slug);
  return {
    projects,
    loading: loading && !data,
    error: error && !data ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
  };
}
