"use client";

import { useMutation } from "@apollo/client/react";
import { PencilIcon, PlusIcon, Trash2Icon, UsersIcon } from "lucide-react";
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
import { Button } from "@/components/ui/button";
import { SOFT_DELETE_TEAM } from "@/graphql/identity/identity.mutations";
import { LIST_TEAMS, LIST_TEAMS_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { CreateTeamDialog } from "./create-team-dialog";
import { EditTeamDialog } from "./edit-team-dialog";

interface TeamsPageResp {
  astroliftTeamsPage: CursorPage<AstroliftTeam>;
}

export function TeamsClient() {
  const fmt = useFormatters();
  const [open, setOpen] = React.useState(false);
  const [editTarget, setEditTarget] = React.useState<AstroliftTeam | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftTeam | null>(null);

  // `astroliftTeamsPage` takes `search`, `limit` and `after` only — there is
  // no sort argument, so no column declares a `sortKey` and the header stays
  // a plain label rather than a control that could only reorder one page.
  const table = useCursorTable<AstroliftTeam>({
    query: LIST_TEAMS_PAGE,
    extract: (d) => (d as TeamsPageResp | undefined)?.astroliftTeamsPage,
    searchVariable: "search",
    urlKey: "team",
  });

  const [softDeleteTeam, { loading: deleting }] = useMutation<{
    softDeleteTeam: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_TEAM, {
    // LIST_TEAMS still backs the pickers on other surfaces; "ListTeamsPage"
    // is this table's own walk, which is a different root field and would
    // otherwise keep showing the deleted row.
    refetchQueries: [{ query: LIST_TEAMS }, "ListTeamsPage"],
    awaitRefetchQueries: true,
  });

  async function handleDelete(team: AstroliftTeam) {
    const { data } = await softDeleteTeam({
      variables: { input: { id: team.id } },
    });
    const result = data?.softDeleteTeam;
    if (result?.ok) {
      toast.success(`Deleted ${team.slug}`);
    } else {
      throw new Error(result?.errors?.[0]?.message ?? "Delete failed");
    }
  }

  // The create sheet refetches LIST_TEAMS, which is a different root field
  // from the page this table walks, so a newly created team would not appear
  // until a navigation. Refetch the walk when the sheet closes.
  function handleCreateOpenChange(next: boolean) {
    setOpen(next);
    if (!next) table.refetch();
  }

  const columns: Column<AstroliftTeam>[] = [
    {
      id: "name",
      header: "Team",
      cell: (team) => <span className="font-medium">{team.name}</span>,
    },
    {
      id: "slug",
      header: "Slug",
      cell: (team) => (
        <span className="text-muted-foreground font-mono text-xs">{team.slug}</span>
      ),
    },
    {
      id: "createdAt",
      header: "Created",
      cell: (team) => (
        <span className="text-muted-foreground text-sm">
          {fmt.formatDate(team.createdAt)}
        </span>
      ),
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      cell: (team) => (
        // The row link is a stretched overlay on the first cell, so the row
        // actions need their own stacking context to stay clickable.
        <div className="relative z-10 flex justify-end">
          <Can permission="team.update">
            <Button size="sm" variant="ghost" onClick={() => setEditTarget(team)}>
              <PencilIcon className="size-4" />
              <span className="sr-only">Edit</span>
            </Button>
          </Can>
          <Can permission="team.delete">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDeleteTarget(team)}
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
      title="Teams"
      description="Groups that scope projects, API tokens, and policies. Use teams to delegate ownership of a subset of apps."
      actions={
        <Can permission="team.create">
          <Button onClick={() => setOpen(true)}>
            <PlusIcon className="size-4" />
            New team
          </Button>
        </Can>
      }
    >
      <DataTable
        label="Teams"
        controller={table}
        columns={columns}
        getRowId={(team) => team.id}
        rowHref={(team) => `/teams/${team.slug}`}
        searchPlaceholder="Search teams..."
        empty={{
          icon: <UsersIcon className="size-5" />,
          title: "No teams yet",
          description: "Create a team to start grouping projects and apps.",
        }}
        emptyFiltered={{
          title: "No matching teams",
          description:
            "No team matches that search. The server matches team name, slug and description.",
        }}
      />

      <CreateTeamDialog open={open} onOpenChange={handleCreateOpenChange} />

      <EditTeamDialog
        open={editTarget !== null}
        onOpenChange={(next) => {
          if (!next) setEditTarget(null);
        }}
        team={editTarget}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={deleteTarget ? `Delete team ${deleteTarget.slug}?` : "Delete team?"}
        description="Soft delete only — the slug becomes reclaimable. Projects under this team stay visible until reassigned."
        confirmLabel="Delete team"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />
    </PageShell>
  );
}
