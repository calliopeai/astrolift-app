"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { PlusIcon, Trash2Icon, UsersIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { Can } from "@/components/Can";
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
import type {
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";

import { CreateTeamDialog } from "./create-team-dialog";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

export function TeamsClient() {
  const [open, setOpen] = React.useState(false);
  const teams = useQuery<TeamsResp>(LIST_TEAMS);

  const [softDeleteTeam, { loading: deleting }] = useMutation<{
    softDeleteTeam: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_TEAM, {
    refetchQueries: [{ query: LIST_TEAMS }],
    awaitRefetchQueries: true,
  });

  async function handleDelete(team: AstroliftTeam) {
    if (
      !confirm(
        `Delete team ${team.slug}? Soft delete only — slug becomes reclaimable.`,
      )
    ) {
      return;
    }
    const { data } = await softDeleteTeam({
      variables: { input: { id: team.id } },
    });
    const result = data?.softDeleteTeam;
    if (result?.ok) {
      toast.success(`Deleted ${team.slug}`);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Delete failed");
    }
  }

  const list = teams.data?.astroliftTeams ?? [];

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
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Team</TableHead>
                  <TableHead>Slug</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((team) => (
                  <TableRow key={team.id}>
                    <TableCell className="font-medium">{team.name}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {team.slug}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {new Date(team.createdAt).toLocaleDateString(undefined, {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </TableCell>
                    <TableCell className="text-right">
                      <Can permission="team.delete">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleDelete(team)}
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
          )}
        </CardContent>
      </Card>

      <CreateTeamDialog open={open} onOpenChange={setOpen} />
    </PageShell>
  );
}
