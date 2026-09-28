"use client";

import {
  ArrowRightLeftIcon,
  HomeIcon,
  Loader2Icon,
  PlusIcon,
  Trash2Icon,
  UsersIcon,
} from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { type Column, DataTable } from "@/components/data-table";
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
import type { AstroliftTeam } from "@/graphql/identity/identity.types";
import type { AppTeamAccessLevel, AstroliftAppTeamAccess } from "@/graphql/registry/registry.types";

import type { useTeamsCard } from "./use-teams-card";

export type TeamsCardViewProps = ReturnType<typeof useTeamsCard>;

const LEVEL_DESCRIPTIONS: Record<AppTeamAccessLevel, string> = {
  viewer: "Read-only — can view this app, no deploy or mutate rights.",
  deployer: "Read + deploy — can trigger rollouts, rotate tokens, edit manifest.",
  owner: "Full control — including granting access to other teams.",
};

const LEVEL_BADGE: Record<AppTeamAccessLevel, string> = {
  viewer: "border-info-border bg-info/10 text-info-fg",
  deployer: "border-success-border bg-success/10 text-success-fg",
  owner: "border-warning-border bg-warning/10 text-warning-fg",
};

/**
 * Teams that can reach this app: the grants table with per-row access
 * level and revoke, plus the Add-Team and Move-home-team sheets. The data
 * half is useTeamsCard.
 */
export function TeamsCardView({
  homeTeamSlug,
  table,
  teams,
  teamsLoading,
  candidateTeams,
  granting,
  revoking,
  moving,
  adding,
  onLevelChange,
  onRevoke,
  onMove,
  onAddTeam,
}: TeamsCardViewProps) {
  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftAppTeamAccess | null>(null);
  const [addOpen, setAddOpen] = React.useState(false);
  const [moveOpen, setMoveOpen] = React.useState(false);

  const columns: Column<AstroliftAppTeamAccess>[] = [
    {
      id: "team",
      header: "Team",
      cell: (a) => (
        <>
          <div className="flex items-center gap-2">
            <div className="font-medium">{a.teamName}</div>
            {a.isHome && (
              <Badge
                variant="outline"
                className="border-warning-border bg-warning/10 text-warning-fg"
              >
                <HomeIcon className="size-3" />
                Home
              </Badge>
            )}
          </div>
          <div className="text-muted-foreground font-mono text-xs">{a.teamSlug}</div>
        </>
      ),
    },
    {
      id: "level",
      header: "Access level",
      cell: (a) => (
        <>
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
              onValueChange={(v) => onLevelChange(a, v as AppTeamAccessLevel)}
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
          <p className="text-muted-foreground text-2xs mt-1">{LEVEL_DESCRIPTIONS[a.accessLevel]}</p>
        </>
      ),
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      width: "w-16",
      cell: (a) => (
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
      ),
    },
  ];

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
            <Button size="sm" variant="outline" onClick={() => setMoveOpen(true)} disabled={moving}>
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
      <CardContent>
        <DataTable
          label="Team access grants"
          controller={table}
          columns={columns}
          getRowId={(a) => a.id}
          searchPlaceholder="Search teams by name or slug…"
          empty={{
            icon: <UsersIcon className="size-5" />,
            title: "No team access grants",
            description: "Add a team to give it visibility and operational rights on this app.",
          }}
          emptyFiltered={{
            title: "No matching teams",
            description: "No team with a grant on this app matches that search.",
          }}
        />
      </CardContent>

      <AddTeamSheetView
        open={addOpen}
        onOpenChange={setAddOpen}
        candidateTeams={candidateTeams}
        teamsLoading={teamsLoading}
        loading={adding}
        onGrant={onAddTeam}
      />

      <MoveTeamSheetView
        open={moveOpen}
        onOpenChange={setMoveOpen}
        homeTeamSlug={homeTeamSlug}
        teams={teams}
        teamsLoading={teamsLoading}
        moveLoading={moving}
        onMove={async (targetTeamId) => {
          if (await onMove(targetTeamId)) setMoveOpen(false);
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
          if (revokeTarget) await onRevoke(revokeTarget);
        }}
      />
    </Card>
  );
}

// ─── Add Team ────────────────────────────────────────────────────────────────

export interface AddTeamSheetViewProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidateTeams: AstroliftTeam[];
  teamsLoading: boolean;
  loading: boolean;
  /** Resolves true when the grant landed; the sheet then closes. */
  onGrant: (teamId: string, level: AppTeamAccessLevel) => Promise<boolean>;
}

export function AddTeamSheetView({
  open,
  onOpenChange,
  candidateTeams,
  teamsLoading,
  loading,
  onGrant,
}: AddTeamSheetViewProps) {
  const [teamId, setTeamId] = React.useState("");
  const [level, setLevel] = React.useState<AppTeamAccessLevel>("deployer");

  React.useEffect(() => {
    if (!open) {
      setTeamId("");
      setLevel("deployer");
    }
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!teamId) return;
    if (await onGrant(teamId, level)) onOpenChange(false);
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
                    {t.name} <span className="text-muted-foreground font-mono">({t.slug})</span>
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

export interface MoveTeamSheetViewProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  homeTeamSlug: string;
  teams: AstroliftTeam[];
  teamsLoading: boolean;
  moveLoading: boolean;
  onMove: (targetTeamId: string) => Promise<void>;
}

export function MoveTeamSheetView({
  open,
  onOpenChange,
  homeTeamSlug,
  teams,
  teamsLoading,
  moveLoading,
  onMove,
}: MoveTeamSheetViewProps) {
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
            previous home team is downgraded to DEPLOYER (not revoked). The app&apos;s project must
            already belong to the target team — otherwise re-anchor the project first.
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
                    {t.name} <span className="text-muted-foreground font-mono">({t.slug})</span>
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
