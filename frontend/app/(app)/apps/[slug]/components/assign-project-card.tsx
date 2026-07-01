"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { FileBoxIcon, FolderInputIcon, Loader2Icon, XCircleIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_NAV_TREE } from "@/graphql/identity/identity.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { ASSIGN_APP_TO_PROJECT } from "@/graphql/registry/registry.mutations";
import { GET_APP, LIST_ASSIGNABLE_PROJECTS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

interface AssignableProject {
  id: string;
  slug: string;
  name: string;
  team: {
    id: string;
    slug: string;
    name: string;
  };
}

interface AssignableProjectsResp {
  assignableAstroliftProjects: AssignableProject[];
}

interface AssignResp {
  assignAstroliftAppToProject: MutationResult<
    Pick<
      AstroliftRegisteredApp,
      "id" | "slug" | "teamSlug" | "teamName" | "teamId" | "projectSlug" | "projectName" | "projectId"
    >
  >;
}

interface Props {
  appSlug: string;
  /** Current project's GUID, or `null` when the app is unassigned. */
  currentProjectId: string | null;
  currentProjectName: string;
  currentTeamName: string;
}

/**
 * Settings landing card for re-parenting an app under a Team/Project (#391).
 *
 * Three states:
 *  - `unassigned` — current project FK is null. Picker is enabled,
 *    Unassign button is hidden.
 *  - `assigned` — current project is set. Picker is enabled (to move),
 *    Unassign button is shown.
 *  - `loading` — assignable-projects query in flight; both controls show
 *    a skeleton.
 *
 * Read-only viewers (`!app.update`) see the current value and the
 * picker as a static badge, with no Save / Unassign affordance.
 */
export function AssignProjectCard({
  appSlug,
  currentProjectId,
  currentProjectName,
  currentTeamName,
}: Props) {
  const projects = useQuery<AssignableProjectsResp>(LIST_ASSIGNABLE_PROJECTS, {
    fetchPolicy: "cache-and-network",
  });

  // After a successful assign the nav tree shape can change (apps move
  // between Team/Project groups), so refetch both the per-app query and
  // the sidebar tree query. The current page already subscribes to
  // `GET_APP`; `LIST_NAV_TREE` is what drives `NavTree`.
  const refetch = [
    { query: GET_APP, variables: { slug: appSlug } },
    { query: LIST_NAV_TREE },
  ];

  const [assign, { loading: assigning }] = useMutation<AssignResp>(ASSIGN_APP_TO_PROJECT, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  // Selected project drives the Save button enablement. Default to the
  // current project so re-opening the card doesn't reset the picker.
  const [selectedProjectId, setSelectedProjectId] = React.useState<string>(
    currentProjectId ?? "",
  );
  React.useEffect(() => {
    setSelectedProjectId(currentProjectId ?? "");
  }, [currentProjectId]);

  const assignable = projects.data?.assignableAstroliftProjects ?? [];

  // Group by team so the picker reads like the nav tree: team header,
  // then projects nested under it. Sorted alphabetically within each
  // bucket for muscle-memory predictability across orgs.
  const byTeam = React.useMemo(() => {
    const map = new Map<string, { teamName: string; teamSlug: string; projects: AssignableProject[] }>();
    for (const p of assignable) {
      const bucket = map.get(p.team.id);
      if (bucket) {
        bucket.projects.push(p);
      } else {
        map.set(p.team.id, {
          teamName: p.team.name,
          teamSlug: p.team.slug,
          projects: [p],
        });
      }
    }
    for (const bucket of map.values()) {
      bucket.projects.sort((a, b) => a.name.localeCompare(b.name));
    }
    return Array.from(map.values()).sort((a, b) => a.teamName.localeCompare(b.teamName));
  }, [assignable]);

  const dirty = selectedProjectId && selectedProjectId !== currentProjectId;

  async function handleSave() {
    if (!dirty) return;
    const { data } = await assign({
      variables: { input: { appSlug, projectGuid: selectedProjectId } },
    });
    const env = data?.assignAstroliftAppToProject;
    if (!env) {
      toast.error("Assignment failed: no response from backend.");
      return;
    }
    if (!env.ok) {
      toast.error(env.errors?.[0]?.message ?? "Assignment failed.");
      return;
    }
    const payload = env.data;
    if (!payload) {
      toast.error("Assignment returned no payload.");
      return;
    }
    toast.success(
      `Moved to ${payload.projectName || payload.projectSlug} under ${payload.teamName || payload.teamSlug}.`,
    );
  }

  async function handleUnassign() {
    const { data } = await assign({
      variables: { input: { appSlug, projectGuid: null } },
    });
    const env = data?.assignAstroliftAppToProject;
    if (!env) {
      toast.error("Unassign failed: no response from backend.");
      return;
    }
    if (!env.ok) {
      toast.error(env.errors?.[0]?.message ?? "Unassign failed.");
      return;
    }
    toast.success("App is now unassigned.");
  }

  const currentLabel = currentProjectId
    ? `${currentProjectName} (${currentTeamName})`
    : "Unassigned";

  return (
    <Card>
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
          <FolderInputIcon className="size-5" />
        </div>
        <div className="flex-1">
          <CardTitle className="text-base">Project assignment</CardTitle>
          <CardDescription className="mt-1">
            Place this app under a project to group it in the sidebar and scope team-level
            permissions. Apps without a project surface in the team&apos;s &quot;Unassigned&quot;
            bucket.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label className="text-xs">Current project</Label>
          <div className="flex items-center gap-2">
            {currentProjectId ? (
              <Badge variant="outline" className="gap-1.5">
                <FileBoxIcon className="size-3" aria-hidden />
                <span className="font-mono text-xs">{currentLabel}</span>
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
              >
                Unassigned
              </Badge>
            )}
          </div>
        </div>

        <Can
          permission="app.update"
          fallback={
            <p className="text-muted-foreground text-xs italic">
              You need the <span className="font-mono">app.update</span> permission to change
              this assignment.
            </p>
          }
        >
          <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
            <div className="space-y-1.5">
              <Label htmlFor="assign-project-picker" className="text-xs">
                Move to project
              </Label>
              {projects.loading && assignable.length === 0 ? (
                <Skeleton className="h-9 w-full" />
              ) : (
                <Select
                  value={selectedProjectId}
                  onValueChange={setSelectedProjectId}
                  disabled={assigning || byTeam.length === 0}
                >
                  <SelectTrigger id="assign-project-picker" className="w-full">
                    <SelectValue
                      placeholder={
                        byTeam.length === 0
                          ? "No projects you can assign to"
                          : "Pick a target project"
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {byTeam.map((group) => (
                      <SelectGroup key={group.teamSlug}>
                        <SelectLabel className="text-2xs uppercase tracking-wide">
                          {group.teamName}
                        </SelectLabel>
                        {group.projects.map((p) => (
                          <SelectItem key={p.id} value={p.id}>
                            <span>{p.name}</span>
                            <span className="text-muted-foreground ml-2 font-mono text-2xs">
                              {p.slug}
                            </span>
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
            <Button onClick={handleSave} disabled={!dirty || assigning} className="sm:self-end">
              {assigning ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <FolderInputIcon className="size-4" />
              )}
              Save
            </Button>
            {currentProjectId ? (
              <Button
                variant="outline"
                onClick={handleUnassign}
                disabled={assigning}
                className="sm:self-end"
              >
                {assigning ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <XCircleIcon className="size-4" />
                )}
                Unassign
              </Button>
            ) : null}
          </div>
        </Can>
      </CardContent>
    </Card>
  );
}
