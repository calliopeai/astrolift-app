"use client";

import { useTranslations } from "next-intl";
import { useQuery } from "@apollo/client/react";

import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject } from "@/graphql/identity/identity.types";

/**
 * The projects under a team: what a grant at the team reaches (design 3.2),
 * for the Access tab's summary. LIST_PROJECTS is the org's project list, so
 * this is usually a cache read of the one the nav already made.
 */
export function useTeamProjects(slug: string) {
  const t = useTranslations("teams.access");
  const { data, loading, error, refetch } = useQuery<{ astroliftProjects: AstroliftProject[] }>(
    LIST_PROJECTS,
    { fetchPolicy: "cache-first" }
  );
  const projects = (data?.astroliftProjects ?? []).filter((p) => p.team.slug === slug);
  return {
    projects,
    loading: loading && !data,
    error: error
      ? { message: error.message }
      : !loading && data?.astroliftProjects == null
        ? { message: t("unavailable") }
        : null,
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}
