"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { FileBoxIcon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { Can } from "@/components/Can";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
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
import { SOFT_DELETE_PROJECT } from "@/graphql/identity/identity.mutations";
import {
  LIST_PROJECTS,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftProject,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";

import { CreateProjectDialog } from "./create-project-dialog";

interface Resp {
  astroliftProjects: AstroliftProject[];
}
interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

export function ProjectsClient() {
  const [open, setOpen] = React.useState(false);
  const projects = useQuery<Resp>(LIST_PROJECTS);
  const teams = useQuery<TeamsResp>(LIST_TEAMS);

  const [softDeleteProject, { loading: deleting }] = useMutation<{
    softDeleteProject: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_PROJECT, {
    refetchQueries: [{ query: LIST_PROJECTS }],
    awaitRefetchQueries: true,
  });

  async function handleDelete(p: AstroliftProject) {
    if (!confirm(`Delete project ${p.team.slug}/${p.slug}?`)) return;
    const { data } = await softDeleteProject({
      variables: { input: { id: p.id } },
    });
    const result = data?.softDeleteProject;
    if (result?.ok) {
      toast.success(`Deleted ${p.slug}`);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Delete failed");
    }
  }

  const list = projects.data?.astroliftProjects ?? [];

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
      <Card>
        <CardContent className="p-0">
          {projects.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<FileBoxIcon className="size-5" />}
                title="No projects yet"
                description={
                  teams.data?.astroliftTeams.length === 0
                    ? "Create a team first — projects live under teams."
                    : "Group your apps under a project so cost and quotas roll up cleanly."
                }
                actionHref={teams.data?.astroliftTeams.length === 0 ? "/teams" : undefined}
                actionLabel={
                  teams.data?.astroliftTeams.length === 0 ? "Manage teams" : undefined
                }
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Project</TableHead>
                  <TableHead>Team</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell>
                      <Link
                        href={`/projects/${p.slug}`}
                        className="hover:underline"
                      >
                        <div className="font-medium">{p.name}</div>
                        <div className="text-muted-foreground font-mono text-xs">
                          {p.team.slug}/{p.slug}
                        </div>
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{p.team.slug}</Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {new Date(p.createdAt).toLocaleDateString(undefined, {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </TableCell>
                    <TableCell className="text-right">
                      <Can permission="project.delete">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleDelete(p)}
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

      <CreateProjectDialog
        open={open}
        onOpenChange={setOpen}
        teams={teams.data?.astroliftTeams ?? []}
      />
    </PageShell>
  );
}
