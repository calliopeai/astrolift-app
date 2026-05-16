"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  ArrowRightLeftIcon,
  HomeIcon,
  Loader2Icon,
  PlusIcon,
  Trash2Icon,
  UsersIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";
import {
  GRANT_TEAM_ACCESS_TO_APP,
  MOVE_APP_TO_TEAM,
  REVOKE_TEAM_ACCESS_FROM_APP,
} from "@/graphql/registry/registry.mutations";
import {
  GET_APP,
  LIST_APP_TEAM_ACCESSES,
} from "@/graphql/registry/registry.queries";
import type {
  AppTeamAccessLevel,
  AstroliftAppTeamAccess,
} from "@/graphql/registry/registry.types";

interface TeamAccessesResp {
  astroliftAppTeamAccesses: AstroliftAppTeamAccess[];
}
interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

interface Props {
  appSlug: string;
  appId: string;
  /** Current home-team slug, surfaced as breadcrumb context. */
  homeTeamSlug: string;
}

const LEVEL_DESCRIPTIONS: Record<AppTeamAccessLevel, string> = {
  viewer: "Read-only — can view this app, no deploy or mutate rights.",
  deployer: "Read + deploy — can trigger rollouts, rotate tokens, edit manifest.",
  owner: "Full control — including granting access to other teams.",
};

const LEVEL_BADGE: Record<AppTeamAccessLevel, string> = {
  viewer: "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300",
  deployer:
    "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  owner: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
};

export function TeamsCard({ appSlug, appId, homeTeamSlug }: Props) {
  const accesses = useQuery<TeamAccessesResp>(LIST_APP_TEAM_ACCESSES, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const teams = useQuery<TeamsResp>(LIST_TEAMS, { fetchPolicy: "cache-first" });

  const refetch = [
    { query: LIST_APP_TEAM_ACCESSES, variables: { appSlug } },
    { query: GET_APP, variables: { slug: appSlug } },
  ];

  const [grant, { loading: granting }] = useMutation<{
    grantTeamAccessToApp: MutationResult<AstroliftAppTeamAccess>;
  }>(GRANT_TEAM_ACCESS_TO_APP, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const [revoke, { loading: revoking }] = useMutation<{
    revokeTeamAccessFromApp: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_TEAM_ACCESS_FROM_APP, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const [move, { loading: moving }] = useMutation<{
    moveAppToTeam: MutationResult<{ id: string; slug: string; teamSlug: string }>;
  }>(MOVE_APP_TO_TEAM, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftAppTeamAccess | null>(null);
  const [addOpen, setAddOpen] = React.useState(false);
  const [moveOpen, setMoveOpen] = React.useState(false);

  const list = accesses.data?.astroliftAppTeamAccesses ?? [];
  const teamList = teams.data?.astroliftTeams ?? [];

  // Teams in this org that don't yet hold an active access grant —
  // candidates for the Add-Team picker. We don't pre-filter by org
  // here because LIST_TEAMS already returns the active-tenant's teams.
  const grantedTeamIds = new Set(list.map((a) => a.teamId));
  const candidateTeams = teamList.filter((t) => !grantedTeamIds.has(t.id));

  async function handleLevelChange(access: AstroliftAppTeamAccess, level: AppTeamAccessLevel) {
    if (access.accessLevel === level) return;
    const { data } = await grant({
      variables: {
        input: { appId, teamId: access.teamId, accessLevel: level },
      },
    });
    const result = data?.grantTeamAccessToApp;
    if (result?.ok) {
      toast.success(`Updated ${access.teamSlug} to ${level}.`);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Update failed.");
    }
  }

  async function handleRevoke(access: AstroliftAppTeamAccess) {
    const { data } = await revoke({
      variables: { input: { appId, teamId: access.teamId } },
    });
    const result = data?.revokeTeamAccessFromApp;
    if (result?.ok) {
      toast.success(`Revoked ${access.teamSlug}'s access.`);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Revoke failed.");
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <UsersIcon className="size-4" /> Teams
          </CardTitle>
          <CardDescription>
            Teams that can reach this app. The home team owns the app and inherits OWNER access;
            additional teams can be granted viewer, deployer, or owner roles.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Can permission="app.update">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setMoveOpen(true)}
              disabled={moving}
            >
              <ArrowRightLeftIcon className="size-4" />
              Move home team
            </Button>
          </Can>
          <Can permission="app.update">
            <Button
              size="sm"
              onClick={() => setAddOpen(true)}
              disabled={candidateTeams.length === 0}
              title={
                candidateTeams.length === 0
                  ? "Every team in this org already has access."
                  : undefined
              }
            >
              <PlusIcon className="size-4" />
              Add team
            </Button>
          </Can>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {accesses.loading && list.length === 0 ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : list.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={<UsersIcon className="size-5" />}
              title="No team access grants"
              description="Add a team to give it visibility and operational rights on this app."
            />
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Team</TableHead>
                <TableHead>Access level</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="font-medium">{a.teamName}</div>
                      {a.isHome && (
                        <Badge
                          variant="outline"
                          className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                        >
                          <HomeIcon className="size-3" />
                          Home
                        </Badge>
                      )}
                    </div>
                    <div className="text-muted-foreground font-mono text-xs">{a.teamSlug}</div>
                  </TableCell>
                  <TableCell>
                    <Can
                      permission="app.update"
                      fallback={
                        <Badge variant="outline" className={LEVEL_BADGE[a.accessLevel]}>
                          {a.accessLevel}
                        </Badge>
                      }
                    >
                      <Select
                        value={a.accessLevel}
                        onValueChange={(v) => handleLevelChange(a, v as AppTeamAccessLevel)}
                        disabled={granting || a.isHome}
                      >
                        <SelectTrigger
                          className="h-8 w-[140px]"
                          title={
                            a.isHome
                              ? "Home team access is fixed at OWNER. Move the app to a different team to change this."
                              : undefined
                          }
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="viewer">Viewer</SelectItem>
                          <SelectItem value="deployer">Deployer</SelectItem>
                          <SelectItem value="owner">Owner</SelectItem>
                        </SelectContent>
                      </Select>
                    </Can>
                    <p className="text-muted-foreground mt-1 text-[10px]">
                      {LEVEL_DESCRIPTIONS[a.accessLevel]}
                    </p>
                  </TableCell>
                  <TableCell className="text-right">
                    <Can permission="app.update">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="size-8"
                        onClick={() => setRevokeTarget(a)}
                        disabled={revoking || a.isHome}
                        title={
                          a.isHome
                            ? "Cannot revoke the home team. Move the app to a different team first."
                            : "Revoke access"
                        }
                      >
                        <Trash2Icon className="size-4" />
                        <span className="sr-only">Revoke</span>
                      </Button>
                    </Can>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <AddTeamSheet
        open={addOpen}
        onOpenChange={setAddOpen}
        appId={appId}
        appSlug={appSlug}
        candidateTeams={candidateTeams}
        teamsLoading={teams.loading}
      />

      <MoveTeamSheet
        open={moveOpen}
        onOpenChange={setMoveOpen}
        appId={appId}
        appSlug={appSlug}
        homeTeamSlug={homeTeamSlug}
        teams={teamList}
        teamsLoading={teams.loading}
        moveLoading={moving}
        onMove={async (targetTeamId) => {
          const { data } = await move({
            variables: { input: { appId, targetTeamId } },
          });
          const result = data?.moveAppToTeam;
          if (result?.ok) {
            toast.success("App moved to the new home team.");
            setMoveOpen(false);
          } else {
            toast.error(result?.errors?.[0]?.message ?? "Move failed.");
          }
        }}
      />

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokeTarget(null);
        }}
        title={revokeTarget ? `Revoke ${revokeTarget.teamSlug}'s access?` : "Revoke access?"}
        description="The team loses visibility and operational rights on this app. Soft-delete only — the grant can be re-added at any time."
        confirmLabel="Revoke"
        destructive
        onConfirm={async () => {
          if (revokeTarget) await handleRevoke(revokeTarget);
        }}
      />
    </Card>
  );
}

// ─── Add Team ────────────────────────────────────────────────────────────────

interface AddTeamSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  appId: string;
  appSlug: string;
  candidateTeams: AstroliftTeam[];
  teamsLoading: boolean;
}

function AddTeamSheet({
  open,
  onOpenChange,
  appId,
  appSlug,
  candidateTeams,
  teamsLoading,
}: AddTeamSheetProps) {
  const [teamId, setTeamId] = React.useState("");
  const [level, setLevel] = React.useState<AppTeamAccessLevel>("deployer");

  React.useEffect(() => {
    if (!open) {
      setTeamId("");
      setLevel("deployer");
    }
  }, [open]);

  const [grant, { loading }] = useMutation<{
    grantTeamAccessToApp: MutationResult<AstroliftAppTeamAccess>;
  }>(GRANT_TEAM_ACCESS_TO_APP, {
    refetchQueries: [{ query: LIST_APP_TEAM_ACCESSES, variables: { appSlug } }],
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!teamId) return;
    const { data } = await grant({
      variables: { input: { appId, teamId, accessLevel: level } },
    });
    const result = data?.grantTeamAccessToApp;
    if (result?.ok) {
      toast.success(`Granted access at level ${level}.`);
      onOpenChange(false);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Grant failed.");
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Add team access</SheetTitle>
          <SheetDescription>
            Pick a team in this organization and the access level it should hold on this app.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="team">Team</Label>
            <Select
              value={teamId}
              onValueChange={setTeamId}
              disabled={teamsLoading || candidateTeams.length === 0}
            >
              <SelectTrigger id="team">
                <SelectValue
                  placeholder={
                    teamsLoading
                      ? "Loading teams…"
                      : candidateTeams.length === 0
                        ? "Every team already has access"
                        : "Select a team"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {candidateTeams.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}{" "}
                    <span className="text-muted-foreground font-mono">({t.slug})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="level">Access level</Label>
            <Select value={level} onValueChange={(v) => setLevel(v as AppTeamAccessLevel)}>
              <SelectTrigger id="level">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="viewer">Viewer</SelectItem>
                <SelectItem value="deployer">Deployer</SelectItem>
                <SelectItem value="owner">Owner</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">{LEVEL_DESCRIPTIONS[level]}</p>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={loading || !teamId}>
              {loading ? (
                <>
                  <Loader2Icon className="size-3.5 animate-spin" /> Granting…
                </>
              ) : (
                "Grant access"
              )}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

// ─── Move Home Team ──────────────────────────────────────────────────────────

interface MoveTeamSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  appId: string;
  appSlug: string;
  homeTeamSlug: string;
  teams: AstroliftTeam[];
  teamsLoading: boolean;
  moveLoading: boolean;
  onMove: (targetTeamId: string) => Promise<void>;
}

function MoveTeamSheet({
  open,
  onOpenChange,
  appSlug: _appSlug,
  appId: _appId,
  homeTeamSlug,
  teams,
  teamsLoading,
  moveLoading,
  onMove,
}: MoveTeamSheetProps) {
  const [targetTeamId, setTargetTeamId] = React.useState("");

  React.useEffect(() => {
    if (!open) setTargetTeamId("");
  }, [open]);

  // Exclude the current home team from candidates.
  const candidates = teams.filter((t) => t.slug !== homeTeamSlug);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!targetTeamId) return;
    await onMove(targetTeamId);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Move home team</SheetTitle>
          <SheetDescription>
            Change the app&apos;s primary / home team. The target team gains OWNER access; the
            previous home team is downgraded to DEPLOYER (not revoked). The app&apos;s project
            must already belong to the target team — otherwise re-anchor the project first.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label>Current home team</Label>
            <div className="bg-muted/30 text-muted-foreground rounded-md border px-3 py-2 font-mono text-xs">
              {homeTeamSlug}
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="target-team">Move to team</Label>
            <Select
              value={targetTeamId}
              onValueChange={setTargetTeamId}
              disabled={teamsLoading || candidates.length === 0}
            >
              <SelectTrigger id="target-team">
                <SelectValue
                  placeholder={
                    teamsLoading
                      ? "Loading teams…"
                      : candidates.length === 0
                        ? "No other teams in this org"
                        : "Pick a target team"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {candidates.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}{" "}
                    <span className="text-muted-foreground font-mono">({t.slug})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={moveLoading || !targetTeamId}>
              {moveLoading ? (
                <>
                  <Loader2Icon className="size-3.5 animate-spin" /> Moving…
                </>
              ) : (
                "Move app"
              )}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
