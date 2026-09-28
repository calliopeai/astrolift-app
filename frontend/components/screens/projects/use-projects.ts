"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import { SOFT_DELETE_PROJECT } from "@/graphql/identity/identity.mutations";
import { LIST_PROJECTS, LIST_PROJECTS_PAGE, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftProject,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface ProjectsPageResp {
  astroliftProjectsPage: CursorPage<AstroliftProject>;
}
interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

/**
 * The data half of ProjectsScreen: the projects table walk, the team list
 * for the create sheet and the empty state, and the soft-delete mutation.
 */
export function useProjects() {
  // `astroliftProjectsPage` takes `search`, `limit` and `after` only — no sort
  // argument, so no column declares a `sortKey`.
  const table = useCursorTable<AstroliftProject>({
    query: LIST_PROJECTS_PAGE,
    extract: (d) => (d as ProjectsPageResp | undefined)?.astroliftProjectsPage,
    searchVariable: "search",
    urlKey: "proj",
  });

  // Still the flat list: it feeds the create sheet's team picker and the
  // "you have no teams yet" branch of the empty state, neither of which is a
  // table.
  const teams = useQuery<TeamsResp>(LIST_TEAMS);

  // #717 — When the NavTree's "Add project" affordance navigates here
  // with ?new=1, auto-open the dialog. The optional ?team=<slug> is
  // honored by the dialog itself if it's wired to read the URL; we
  // only open the modal here to keep this hook small.
  const searchParams = useSearchParams();
  const autoOpenCreate = searchParams.get("new") === "1";

  const [softDeleteProject, { loading: deleting }] = useMutation<{
    softDeleteProject: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_PROJECT, {
    // LIST_PROJECTS still backs the pickers on other surfaces;
    // "ListProjectsPage" is this table's own walk, which is a different root
    // field and would otherwise keep showing the deleted row.
    refetchQueries: [{ query: LIST_PROJECTS }, "ListProjectsPage"],
    awaitRefetchQueries: true,
  });

  /** Throws on failure so the confirm dialog stays open and shows the error. */
  async function deleteProject(p: AstroliftProject) {
    const { data } = await softDeleteProject({
      variables: { input: { id: p.id } },
    });
    const result = data?.softDeleteProject;
    if (result?.ok) {
      toast.success(`Deleted ${p.slug}`);
    } else {
      throw new Error(result?.errors?.[0]?.message ?? "Delete failed");
    }
  }

  return {
    table,
    teams: teams.data?.astroliftTeams ?? [],
    teamsLoading: teams.loading,
    noTeams: teams.data?.astroliftTeams.length === 0,
    autoOpenCreate,
    deleting,
    deleteProject,
  };
}
