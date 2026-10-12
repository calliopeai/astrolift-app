"use client";

import {
  ArrowRightLeftIcon,
  HomeIcon,
  Loader2Icon,
  PlusIcon,
  Trash2Icon,
  UsersIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
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

const LEVEL_BADGE: Record<AppTeamAccessLevel, string> = {
  viewer: "border-info-border bg-info/10 text-info-fg",
  deployer: "border-success-border bg-success/10 text-success-fg",
  owner: "border-warning-border bg-warning/10 text-warning-fg",
};

/**
 * Teams that can reach this app: the grants on the embedded list (cursor
 * pages, server search) with per-row access level and revoke, plus the Add-Team and Move-home-team sheets. The data
 * half is useTeamsCard.
 */
export function TeamsCardView({
  homeTeamSlug,
  list,
  rows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
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
  const t = useTranslations("appOverviewCards.teams");

  const columns: Column<AstroliftAppTeamAccess>[] = [
    {
      id: "team",
      header: t("team"),
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
                {t("home")}
              </Badge>
            )}
          </div>
          <div className="text-muted-foreground font-mono text-xs">{a.teamSlug}</div>
        </>
      ),
    },
    {
      id: "level",
      header: t("accessLevel"),
      cell: (a) => (
        <>
          <Can
            permission="app.update"
            fallback={
              <Badge variant="outline" className={LEVEL_BADGE[a.accessLevel]}>
                {t.has(`levels.${a.accessLevel}`) ? t(`levels.${a.accessLevel}`) : a.accessLevel}
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
                title={a.isHome ? t("homeLevelFixed") : undefined}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="viewer">{t("levels.viewer")}</SelectItem>
                <SelectItem value="deployer">{t("levels.deployer")}</SelectItem>
                <SelectItem value="owner">{t("levels.owner")}</SelectItem>
              </SelectContent>
            </Select>
          </Can>
          <p className="text-muted-foreground text-2xs mt-1">
            {t.has(`levelDescriptions.${a.accessLevel}`)
              ? t(`levelDescriptions.${a.accessLevel}`)
              : ""}
          </p>
        </>
      ),
    },
    {
      id: "actions",
      header: t("actions"),
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
            title={a.isHome ? t("cannotRevokeHome") : t("revokeAccess")}
          >
            <Trash2Icon className="size-4" />
            <span className="sr-only">{t("revoke")}</span>
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
            <UsersIcon className="size-4" /> {t("title")}
          </CardTitle>
          <CardDescription>{t("description")}</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Can permission="app.update">
            <Button size="sm" variant="outline" onClick={() => setMoveOpen(true)} disabled={moving}>
              <ArrowRightLeftIcon className="size-4" />
              {t("moveHomeTeam")}
            </Button>
          </Can>
          <Can permission="app.update">
            <Button
              size="sm"
              onClick={() => setAddOpen(true)}
              disabled={candidateTeams.length === 0}
              title={candidateTeams.length === 0 ? t("allTeamsHaveAccess") : undefined}
            >
              <PlusIcon className="size-4" />
              {t("addTeam")}
            </Button>
          </Can>
        </div>
      </CardHeader>
      <CardContent>
        <ListPage<AstroliftAppTeamAccess>
          embedded
          list={{
            ...list,
            definition: {
              ...list.definition,
              searchPlaceholder: t("searchPlaceholder"),
              views: [{ key: "all", label: t("allView"), filters: {} }],
            },
          }}
          label={t("grantsLabel")}
          columns={columns}
          rows={rows}
          getRowId={(a) => a.id}
          loading={loading}
          stale={stale}
          error={error}
          onRetry={onRetry}
          nextCursor={nextCursor}
          totalCount={totalCount}
          empty={{
            icon: <UsersIcon className="size-5" />,
            title: t("emptyTitle"),
            description: t("emptyDescription"),
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
        title={
          revokeTarget ? t("revokeTitle", { team: revokeTarget.teamSlug }) : t("revokeTitleGeneric")
        }
        description={t("revokeDescription")}
        confirmLabel={t("revoke")}
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
  const copy = useTranslations("appOverviewCards.teams");

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
          <SheetTitle>{copy("addTitle")}</SheetTitle>
          <SheetDescription>{copy("addDescription")}</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="team">{copy("team")}</Label>
            <Select
              value={teamId}
              onValueChange={setTeamId}
              disabled={teamsLoading || candidateTeams.length === 0}
            >
              <SelectTrigger id="team">
                <SelectValue
                  placeholder={
                    teamsLoading
                      ? copy("loadingTeams")
                      : candidateTeams.length === 0
                        ? copy("everyTeamHasAccess")
                        : copy("selectTeam")
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
            <Label htmlFor="level">{copy("accessLevel")}</Label>
            <Select value={level} onValueChange={(v) => setLevel(v as AppTeamAccessLevel)}>
              <SelectTrigger id="level">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="viewer">{copy("levels.viewer")}</SelectItem>
                <SelectItem value="deployer">{copy("levels.deployer")}</SelectItem>
                <SelectItem value="owner">{copy("levels.owner")}</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">
              {copy.has(`levelDescriptions.${level}`) ? copy(`levelDescriptions.${level}`) : ""}
            </p>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {copy("cancel")}
            </Button>
            <Button type="submit" disabled={loading || !teamId}>
              {loading ? (
                <>
                  <Loader2Icon className="size-3.5 animate-spin" /> {copy("granting")}
                </>
              ) : (
                copy("grantAccess")
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
  const copy = useTranslations("appOverviewCards.teams");

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
          <SheetTitle>{copy("moveHomeTeam")}</SheetTitle>
          <SheetDescription>{copy("moveDescription")}</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label>{copy("currentHomeTeam")}</Label>
            <div className="bg-muted/30 text-muted-foreground rounded-md border px-3 py-2 font-mono text-xs">
              {homeTeamSlug}
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="target-team">{copy("moveToTeam")}</Label>
            <Select
              value={targetTeamId}
              onValueChange={setTargetTeamId}
              disabled={teamsLoading || candidates.length === 0}
            >
              <SelectTrigger id="target-team">
                <SelectValue
                  placeholder={
                    teamsLoading
                      ? copy("loadingTeams")
                      : candidates.length === 0
                        ? copy("noOtherTeams")
                        : copy("pickTargetTeam")
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
              {copy("cancel")}
            </Button>
            <Button type="submit" disabled={moveLoading || !targetTeamId}>
              {moveLoading ? (
                <>
                  <Loader2Icon className="size-3.5 animate-spin" /> {copy("moving")}
                </>
              ) : (
                copy("moveApp")
              )}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
