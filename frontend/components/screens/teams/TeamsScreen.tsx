"use client";

import { useTranslations } from "next-intl";

import { PencilIcon, PlusIcon, Trash2Icon, UsersIcon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { accessCrumbs, TEAMS_HREF } from "@/components/screens/administration/access/access-nav";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftTeam } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useTeams } from "./use-teams";

export type TeamsScreenProps = ReturnType<typeof useTeams> & {
  /** The create-team sheet; it runs its own mutation, so the route supplies it. */
  renderCreateDialog: (props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
  }) => React.ReactNode;
  /** The edit-team sheet, for the row whose Edit was picked. */
  renderEditDialog: (props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    team: AstroliftTeam | null;
  }) => React.ReactNode;
};

/**
 * Admin › Access › Teams (access UX design 3.1): the org's teams, numbered.
 * A team scopes projects, apps and their grants; its page says what being on
 * it gives (Access) and who is (Members). Pure view; data from useTeams.
 */
export function TeamsScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  canUpdate,
  canDelete,
  deleting,
  onDelete,
  renderCreateDialog,
  renderEditDialog,
}: TeamsScreenProps) {
  const copy = useTranslations("teams");
  const fmt = useFormatters();
  const [open, setOpen] = React.useState(false);
  const [editTarget, setEditTarget] = React.useState<AstroliftTeam | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftTeam | null>(null);

  // The create sheet refetches LIST_TEAMS, a different root field from this
  // page, so a new team would not appear until a navigation.
  function handleCreateOpenChange(next: boolean) {
    setOpen(next);
    if (!next) onRetry();
  }

  const columns: Column<AstroliftTeam>[] = [
    {
      id: "name",
      header: copy("columnTeam"),
      sortKey: "name",
      cellClassName: "max-w-96",
      cell: (team) => (
        <div className="min-w-0">
          <div className="truncate font-medium" title={team.name}>
            {team.name}
          </div>
          <div className="text-muted-foreground truncate font-mono text-xs" title={team.slug}>
            {team.slug}
          </div>
        </div>
      ),
    },
    {
      id: "created",
      header: copy("columnCreated"),
      sortKey: "created",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (team) => fmt.formatDate(team.createdAt),
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col p-6">
      <ListPage<AstroliftTeam>
        header={{
          crumbs: accessCrumbs("teams"),
          title: copy("title"),
          context: copy("context"),
          primaryAction: (
            <Can permission="team.create">
              <Button size="sm" onClick={() => setOpen(true)}>
                <PlusIcon className="size-4" />
                {copy("newTeam")}
              </Button>
            </Can>
          ),
        }}
        list={list}
        label={copy("title")}
        columns={columns}
        rows={rows}
        getRowId={(team) => team.id}
        rowHref={(team) => `${TEAMS_HREF}/${encodeURIComponent(team.slug)}`}
        rowActions={
          canUpdate || canDelete
            ? (team) => (
                <>
                  {canUpdate && (
                    <DropdownMenuItem onSelect={() => setEditTarget(team)}>
                      <PencilIcon className="size-4" />
                      {copy("edit")}
                    </DropdownMenuItem>
                  )}
                  {canDelete && (
                    <DropdownMenuItem
                      variant="destructive"
                      disabled={deleting}
                      onSelect={() => setDeleteTarget(team)}
                    >
                      <Trash2Icon className="size-4" />
                      {copy("delete")}
                    </DropdownMenuItem>
                  )}
                </>
              )
            : undefined
        }
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        empty={{
          icon: <UsersIcon className="size-5" />,
          title: copy("emptyTitle"),
          description: copy("emptyDescription"),
        }}
      />

      {renderCreateDialog({ open, onOpenChange: handleCreateOpenChange })}

      {renderEditDialog({
        open: editTarget !== null,
        onOpenChange: (next) => {
          if (!next) setEditTarget(null);
        },
        team: editTarget,
      })}

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget
            ? copy("deleteTitle", { slug: deleteTarget.slug })
            : copy("deleteGenericTitle")
        }
        description={copy("deleteDescription")}
        confirmLabel={copy("confirmDelete")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await onDelete(deleteTarget);
        }}
      />
    </div>
  );
}
