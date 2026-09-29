"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { LIST_NAV_TREE } from "@/graphql/identity/identity.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { ASSIGN_APP_TO_PROJECT } from "@/graphql/registry/registry.mutations";
import { GET_APP, LIST_ASSIGNABLE_PROJECTS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

export interface AssignableProject {
  id: string;
  slug: string;
  name: string;
  team: {
    id: string;
    slug: string;
    name: string;
  };
}

export interface AssignableTeamGroup {
  teamName: string;
  teamSlug: string;
  projects: AssignableProject[];
}

interface AssignableProjectsResp {
  assignableAstroliftProjects: AssignableProject[];
}

interface AssignResp {
  assignAstroliftAppToProject: MutationResult<
    Pick<
      AstroliftRegisteredApp,
      | "id"
      | "slug"
      | "teamSlug"
      | "teamName"
      | "teamId"
      | "projectSlug"
      | "projectName"
      | "projectId"
    >
  >;
}

export interface UseAssignProjectArgs {
  appSlug: string;
  /** Current project's GUID, or `null` when the app is unassigned. */
  currentProjectId: string | null;
  currentProjectName: string;
  currentTeamName: string;
}

/**
 * Data half of AssignProjectCardView (#391): the projects the viewer may
 * assign this app to, grouped by team, and the assign / unassign mutation.
 */
export function useAssignProject({
  appSlug,
  currentProjectId,
  currentProjectName,
  currentTeamName,
}: UseAssignProjectArgs) {
  const projects = useQuery<AssignableProjectsResp>(LIST_ASSIGNABLE_PROJECTS, {
    fetchPolicy: "cache-and-network",
  });

  // After a successful assign the nav tree shape can change (apps move
  // between Team/Project groups), so refetch both the per-app query and
  // the sidebar tree query. The current page already subscribes to
  // `GET_APP`; `LIST_NAV_TREE` is what drives `NavTree`.
  const refetch = [{ query: GET_APP, variables: { slug: appSlug } }, { query: LIST_NAV_TREE }];

  const [assign, { loading: assigning }] = useMutation<AssignResp>(ASSIGN_APP_TO_PROJECT, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const assignable = React.useMemo(
    () => projects.data?.assignableAstroliftProjects ?? [],
    [projects.data]
  );

  // Group by team so the picker reads like the nav tree: team header,
  // then projects nested under it. Sorted alphabetically within each
  // bucket for muscle-memory predictability across orgs.
  const byTeam = React.useMemo<AssignableTeamGroup[]>(() => {
    const map = new Map<string, AssignableTeamGroup>();
    for (const p of assignable) {
      const bucket = map.get(p.team.id);
      if (bucket) {
        bucket.projects.push(p);
      } else {
        map.set(p.team.id, {
          teamName: p.team.name,
          teamSlug: p.team.slug,
          projects: [p],
        });
      }
    }
    for (const bucket of map.values()) {
      bucket.projects.sort((a, b) => a.name.localeCompare(b.name));
    }
    return Array.from(map.values()).sort((a, b) => a.teamName.localeCompare(b.teamName));
  }, [assignable]);

  async function onAssign(projectGuid: string) {
    const { data } = await assign({
      variables: { input: { appSlug, projectGuid } },
    });
    const env = data?.assignAstroliftAppToProject;
    if (!env) {
      toast.error("Assignment failed: no response from backend.");
      return;
    }
    if (!env.ok) {
      toast.error(env.errors?.[0]?.message ?? "Assignment failed.");
      return;
    }
    const payload = env.data;
    if (!payload) {
      toast.error("Assignment returned no payload.");
      return;
    }
    toast.success(
      `Moved to ${payload.projectName || payload.projectSlug} under ${payload.teamName || payload.teamSlug}.`
    );
  }

  async function onUnassign() {
    const { data } = await assign({
      variables: { input: { appSlug, projectGuid: null } },
    });
    const env = data?.assignAstroliftAppToProject;
    if (!env) {
      toast.error("Unassign failed: no response from backend.");
      return;
    }
    if (!env.ok) {
      toast.error(env.errors?.[0]?.message ?? "Unassign failed.");
      return;
    }
    toast.success("App is now unassigned.");
  }

  return {
    currentProjectId,
    currentProjectName,
    currentTeamName,
    byTeam,
    /** First load of the assignable projects, nothing cached yet. */
    projectsLoading: projects.loading && assignable.length === 0,
    assigning,
    onAssign,
    onUnassign,
  };
}
