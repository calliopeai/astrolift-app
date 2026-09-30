"use client";

import { FileBoxIcon, PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { adminCrumbs } from "@/components/screens/administration/insights/header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftProject, AstroliftTeam } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useProjects } from "./use-projects";

/** The create sheet, rendered by the caller so its mutation stays out of this view. */
export interface CreateProjectDialogSlotProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teams: AstroliftTeam[];
  initialTeamSlug?: string | null;
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

/**
 * Admin › Projects (spec 44 §5.1): the org's projects on the shared list,
 * views All · Mine, cursor paged, edit and delete in each row's `⋯`. Pure;
 * the data half is useProjects.
 */
export function ProjectsScreen({
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  error,
  onRetry,
  teams,
  teamsLoading,
  noTeams,
  autoOpenCreate,
  initialTeamSlug,
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
    if (!next) onRetry();
  }

  const columns: Column<AstroliftProject>[] = [
    {
      id: "name",
      header: "Project",
      cellClassName: "max-w-80",
      cell: (p) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={p.name}>
            {p.name}
          </span>
          <span className="text-muted-foreground block truncate font-mono text-xs">
            {p.team.slug}/{p.slug}
          </span>
        </span>
      ),
    },
    {
      id: "team",
      header: "Team",
      cellClassName: "max-w-48",
      cell: (p) => (
        <Badge variant="secondary" className="max-w-full truncate">
          {p.team.slug}
        </Badge>
      ),
    },
    {
      id: "createdAt",
      header: "Created",
      cell: (p) => (
        <span className="text-muted-foreground font-mono text-xs">
          {fmt.formatDate(p.createdAt)}
        </span>
      ),
    },
  ];

  return (
    <>
      <ListPage<AstroliftProject>
        header={{
          crumbs: adminCrumbs("projects", "Projects"),
          title: "Projects",
          context:
            "Group apps that share a deploy cadence, a domain, or a stack. Cost reporting rolls up at this level.",
          primaryAction: (
            <Can permission="project.create">
              <Button onClick={() => setOpen(true)} disabled={teamsLoading}>
                <PlusIcon className="size-4" />
                New project
              </Button>
            </Can>
          ),
        }}
        list={list}
        label="Projects"
        columns={columns}
        rows={rows}
        getRowId={(p) => p.id}
        rowHref={(p) => `/projects/${p.slug}`}
        rowActions={(p) => (
          <>
            <Can permission="project.update">
              <DropdownMenuItem onSelect={() => setEditTarget(p)}>
                <PencilIcon className="size-4" />
                Edit
              </DropdownMenuItem>
            </Can>
            <Can permission="project.delete">
              <DropdownMenuItem
                variant="destructive"
                disabled={deleting}
                onSelect={() => setDeleteTarget(p)}
              >
                <Trash2Icon className="size-4" />
                Delete
              </DropdownMenuItem>
            </Can>
          </>
        )}
        loading={loading}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        nextCursor={nextCursor}
        empty={{
          icon: <FileBoxIcon className="size-5" />,
          title: "No projects yet",
          description: noTeams
            ? "Create a team first — projects live under teams."
            : "Group your apps under a project so cost and quotas roll up cleanly.",
          actionHref: noTeams ? "/administration/teams" : undefined,
          actionLabel: noTeams ? "Manage teams" : undefined,
        }}
      />

      {renderCreateDialog({ open, onOpenChange: handleCreateOpenChange, teams, initialTeamSlug })}

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
    </>
  );
}
