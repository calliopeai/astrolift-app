"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { PencilIcon, PlusIcon, Trash2Icon, UsersIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SOFT_DELETE_TEAM } from "@/graphql/identity/identity.mutations";
import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";
import { useListControls } from "@/hooks/use-list-controls";
import { useFormatters } from "@/lib/i18n/formatters";

import { CreateTeamDialog } from "./create-team-dialog";
import { EditTeamDialog } from "./edit-team-dialog";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

export function TeamsClient() {
  const fmt = useFormatters();
  const [open, setOpen] = React.useState(false);
  const [editTarget, setEditTarget] = React.useState<AstroliftTeam | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftTeam | null>(null);
  const teams = useQuery<TeamsResp>(LIST_TEAMS);

  const [softDeleteTeam, { loading: deleting }] = useMutation<{
    softDeleteTeam: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_TEAM, {
    refetchQueries: [{ query: LIST_TEAMS }],
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

  const list = teams.data?.astroliftTeams ?? [];

  const ctrl = useListControls({
    data: list,
    searchFn: (item) => [item.name, item.slug].join(" "),
    initialPageSize: 25,
    sortFn: (a, b, sort) => {
      if (sort.key === "name") {
        const cmp = a.name.localeCompare(b.name);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      if (sort.key === "createdAt") {
        const cmp = a.createdAt < b.createdAt ? -1 : a.createdAt > b.createdAt ? 1 : 0;
        return sort.dir === "asc" ? cmp : -cmp;
      }
      return 0;
    },
  });

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
      <Card>
        <CardContent className="p-0">
          {teams.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<UsersIcon className="size-5" />}
                title="No teams yet"
                description="Create a team to start grouping projects and apps."
              />
            </div>
          ) : (
            <>
              <div className="px-4 py-3 border-b">
                <ListControls controls={ctrl} searchPlaceholder="Search teams..." />
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>
                      <SortableHeader sortKey="name" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                        Team
                      </SortableHeader>
                    </TableHead>
                    <TableHead>Slug</TableHead>
                    <TableHead>
                      <SortableHeader sortKey="createdAt" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                        Created
                      </SortableHeader>
                    </TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {ctrl.rows.map((team) => (
                    <TableRow key={team.id}>
                      <TableCell className="font-medium">
                        <Link href={`/teams/${team.slug}`} className="hover:underline">
                          {team.name}
                        </Link>
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {team.slug}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {fmt.formatDate(team.createdAt)}
                      </TableCell>
                      <TableCell className="text-right">
                        <Can permission="team.update">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setEditTarget(team)}
                          >
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
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          )}
        </CardContent>
      </Card>

      <CreateTeamDialog open={open} onOpenChange={setOpen} />

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
