"use client";

import { FileBoxIcon, PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("projectsList");
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
      header: t("project"),
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
      header: t("team"),
      cellClassName: "max-w-48",
      cell: (p) => (
        <Badge variant="secondary" className="max-w-full truncate">
          {p.team.slug}
        </Badge>
      ),
    },
    {
      id: "createdAt",
      header: t("created"),
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
          crumbs: adminCrumbs("projects", t("title")),
          title: t("title"),
          context: t("context"),
          primaryAction: (
            <Can permission="project.create">
              <Button onClick={() => setOpen(true)} disabled={teamsLoading}>
                <PlusIcon className="size-4" />
                {t("newProject")}
              </Button>
            </Can>
          ),
        }}
        list={list}
        label={t("title")}
        columns={columns}
        rows={rows}
        getRowId={(p) => p.id}
        rowHref={(p) => `/projects/${p.slug}`}
        rowActions={(p) => (
          <>
            <Can permission="project.update">
              <DropdownMenuItem onSelect={() => setEditTarget(p)}>
                <PencilIcon className="size-4" />
                {t("edit")}
              </DropdownMenuItem>
            </Can>
            <Can permission="project.delete">
              <DropdownMenuItem
                variant="destructive"
                disabled={deleting}
                onSelect={() => setDeleteTarget(p)}
              >
                <Trash2Icon className="size-4" />
                {t("delete")}
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
          title: t("empty"),
          description: noTeams ? t("emptyNoTeams") : t("emptyDescription"),
          actionHref: noTeams ? "/administration/teams" : undefined,
          actionLabel: noTeams ? t("manageTeams") : undefined,
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
            ? t("deleteTitle", { team: deleteTarget.team.slug, project: deleteTarget.slug })
            : t("deleteTitleGeneric")
        }
        description={t("deleteDescription")}
        confirmLabel={t("deleteProject")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await deleteProject(deleteTarget);
        }}
      />
    </>
  );
}
