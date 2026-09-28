"use client";

import { FileBoxIcon, PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AstroliftProject, AstroliftTeam } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useProjects } from "./use-projects";

/** The create sheet, rendered by the caller so its mutation stays out of this view. */
export interface CreateProjectDialogSlotProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teams: AstroliftTeam[];
}

/** The edit sheet, rendered by the caller so its queries stay out of this view. */
export interface EditProjectDialogSlotProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: AstroliftProject | null;
}

export type ProjectsScreenProps = ReturnType<typeof useProjects> & {
  renderCreateDialog: (props: CreateProjectDialogSlotProps) => React.ReactNode;
  renderEditDialog: (props: EditProjectDialogSlotProps) => React.ReactNode;
};

export function ProjectsScreen({
  table,
  teams,
  teamsLoading,
  noTeams,
  autoOpenCreate,
  deleting,
  deleteProject,
  renderCreateDialog,
  renderEditDialog,
}: ProjectsScreenProps) {
  const fmt = useFormatters();
  const [open, setOpen] = React.useState(false);
  const [editTarget, setEditTarget] = React.useState<AstroliftProject | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftProject | null>(null);

  // ?new=1 from the NavTree's "Add project" affordance opens the sheet.
  React.useEffect(() => {
    if (autoOpenCreate) setOpen(true);
  }, [autoOpenCreate]);

  // The create sheet refetches LIST_PROJECTS, which is a different root field
  // from the page this table walks, so a newly created project would not
  // appear until a navigation. Refetch the walk when the sheet closes.
  function handleCreateOpenChange(next: boolean) {
    setOpen(next);
    if (!next) table.refetch();
  }

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
          <Button onClick={() => setOpen(true)} disabled={teamsLoading}>
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

      {renderCreateDialog({ open, onOpenChange: handleCreateOpenChange, teams })}

      {renderEditDialog({
        open: editTarget !== null,
        onOpenChange: (next) => {
          if (!next) setEditTarget(null);
        },
        project: editTarget,
      })}

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
          if (deleteTarget) await deleteProject(deleteTarget);
        }}
      />
    </PageShell>
  );
}
