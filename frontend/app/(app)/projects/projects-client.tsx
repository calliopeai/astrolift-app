"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { FileBoxIcon, PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import {
  DataTable,
  useCursorTable,
  type Column,
  type CursorPage,
} from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SOFT_DELETE_PROJECT } from "@/graphql/identity/identity.mutations";
import {
  LIST_PROJECTS,
  LIST_PROJECTS_PAGE,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftProject,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { CreateProjectDialog } from "./create-project-dialog";
import { EditProjectDialog } from "./edit-project-dialog";

interface ProjectsPageResp {
  astroliftProjectsPage: CursorPage<AstroliftProject>;
}
interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

export function ProjectsClient() {
  const fmt = useFormatters();
  const [open, setOpen] = React.useState(false);
  const [editTarget, setEditTarget] = React.useState<AstroliftProject | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftProject | null>(null);

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
  const newParam = searchParams.get("new");
  React.useEffect(() => {
    if (newParam === "1") setOpen(true);
  }, [newParam]);

  const [softDeleteProject, { loading: deleting }] = useMutation<{
    softDeleteProject: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_PROJECT, {
    // LIST_PROJECTS still backs the pickers on other surfaces;
    // "ListProjectsPage" is this table's own walk, which is a different root
    // field and would otherwise keep showing the deleted row.
    refetchQueries: [{ query: LIST_PROJECTS }, "ListProjectsPage"],
    awaitRefetchQueries: true,
  });

  async function handleDelete(p: AstroliftProject) {
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

  // The create sheet refetches LIST_PROJECTS, which is a different root field
  // from the page this table walks, so a newly created project would not
  // appear until a navigation. Refetch the walk when the sheet closes.
  function handleCreateOpenChange(next: boolean) {
    setOpen(next);
    if (!next) table.refetch();
  }

  const noTeams = teams.data?.astroliftTeams.length === 0;

  const columns: Column<AstroliftProject>[] = [
    {
      id: "name",
      header: "Project",
      cell: (p) => (
        <>
          <div className="font-medium">{p.name}</div>
          <div className="text-muted-foreground font-mono text-xs">
            {p.team.slug}/{p.slug}
          </div>
        </>
      ),
    },
    {
      id: "team",
      header: "Team",
      cell: (p) => <Badge variant="secondary">{p.team.slug}</Badge>,
    },
    {
      id: "createdAt",
      header: "Created",
      cell: (p) => (
        <span className="text-muted-foreground text-sm">{fmt.formatDate(p.createdAt)}</span>
      ),
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      cell: (p) => (
        // The row link is a stretched overlay on the first cell, so the row
        // actions need their own stacking context to stay clickable.
        <div className="relative z-10 flex justify-end">
          <Can permission="project.update">
            <Button size="sm" variant="ghost" onClick={() => setEditTarget(p)}>
              <PencilIcon className="size-4" />
              <span className="sr-only">Edit</span>
            </Button>
          </Can>
          <Can permission="project.delete">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDeleteTarget(p)}
              disabled={deleting}
            >
              <Trash2Icon className="size-4" />
              <span className="sr-only">Delete</span>
            </Button>
          </Can>
        </div>
      ),
    },
  ];

  return (
    <PageShell
      title="Projects"
      description="Group apps that share a deploy cadence, a domain, or a stack. Cost reporting rolls up at this level."
      actions={
        <Can permission="project.create">
          <Button onClick={() => setOpen(true)} disabled={teams.loading}>
            <PlusIcon className="size-4" />
            New project
          </Button>
        </Can>
      }
    >
      <DataTable
        label="Projects"
        controller={table}
        columns={columns}
        getRowId={(p) => p.id}
        rowHref={(p) => `/projects/${p.slug}`}
        searchPlaceholder="Search projects..."
        empty={{
          icon: <FileBoxIcon className="size-5" />,
          title: "No projects yet",
          description: noTeams
            ? "Create a team first — projects live under teams."
            : "Group your apps under a project so cost and quotas roll up cleanly.",
          actionHref: noTeams ? "/administration/teams" : undefined,
          actionLabel: noTeams ? "Manage teams" : undefined,
        }}
        emptyFiltered={{
          title: "No matching projects",
          description:
            "No project matches that search. The server matches project name, slug and description, plus the owning team.",
        }}
      />

      <CreateProjectDialog
        open={open}
        onOpenChange={handleCreateOpenChange}
        teams={teams.data?.astroliftTeams ?? []}
      />

      <EditProjectDialog
        open={editTarget !== null}
        onOpenChange={(next) => {
          if (!next) setEditTarget(null);
        }}
        project={editTarget}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget
            ? `Delete project ${deleteTarget.team.slug}/${deleteTarget.slug}?`
            : "Delete project?"
        }
        description="Soft delete — apps remain visible until you reassign them. The slug becomes reclaimable."
        confirmLabel="Delete project"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />
    </PageShell>
  );
}
