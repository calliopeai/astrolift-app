"use client";

import { HistoryIcon, Loader2Icon, Share2Icon, UsersIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import type { PrincipalSearch } from "@/components/access/GrantAccessFlow";
import type { Principal } from "@/components/access/access-model";
import { PrincipalPicker } from "@/components/access/PrincipalPicker";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import {
  DetailTabSections,
  type DetailTabSectionLink,
} from "@/components/detail/DetailTabSections";
import { Feed } from "@/components/feed/Feed";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Panel } from "@/components/panel/Panel";
import { PermissionNote, Restricted } from "@/components/settings/Restricted";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useRelativeAge } from "@/lib/format";

import { APPROVALS_HREF } from "../gated-write";
import type { GateAsk, HistoryEntry, ShareLevel } from "./brain-detail-model";
import { shareLevel } from "./brain-detail-model";
import type { ShareRow } from "./brain-detail-lists";

export interface BrainSharingTabProps {
  layerName: string;
  /** The team that owns the layer: it always has access and is never shared or unshared here. */
  homeTeam: string;
  /** Where a team's level, owner included, is changed: the app's Teams card. */
  teamsCardHref: string;
  section: "teams" | "history";
  sections: DetailTabSectionLink[];
  /** The teams list (embedded, URL state). */
  list: ListStateController;
  rows: ShareRow[];
  totalCount: number;
  canShare: boolean;
  canShareAsEditor: boolean;
  /** What brain.share asks: confirmation, a fresh sign-in. */
  ask: GateAsk;
  share: {
    team: Principal | null;
    onTeam: (team: Principal | null) => void;
    search: PrincipalSearch;
    level: ShareLevel;
    onLevel: (level: ShareLevel) => void;
    submitting: boolean;
    /** Resolves true once the share applied. */
    onShare: () => Promise<boolean>;
  };
  /** A share whose level changes, or that ends; resolves true once it applied. */
  onChangeLevel: (row: ShareRow, level: ShareLevel) => Promise<boolean>;
  onUnshare: (row: ShareRow) => Promise<boolean>;
  /** The last write's refusal, verbatim; with an approval link when one was asked for. */
  refusal: { message: string; approval: boolean } | null;
  history: {
    allowed: boolean;
    items: HistoryEntry[];
    loading: boolean;
    error: string | null;
    onRetry: () => void;
    hasMore: boolean;
    loadingMore: boolean;
    onLoadMore: () => void;
  };
}

/**
 * Sharing (#2424, #2427): the teams a layer is shared with, as viewer (it
 * reads the layer) or editor (it also edits and deploys it), and its access
 * history. Share and level changes go through brain.share, which asks for
 * confirmation and a fresh sign-in; unsharing only narrows. Owners are
 * changed on the app's Teams card, never here. Pure.
 */
export function BrainSharingTab(props: BrainSharingTabProps) {
  const t = useTranslations("brains.sharing");
  return (
    <DetailTabSections ariaLabel={t("sections")} sections={props.sections} active={props.section}>
      {props.section === "history" ? <History {...props} /> : <Teams {...props} />}
    </DetailTabSections>
  );
}

type Pending =
  | { kind: "share"; team: string; level: ShareLevel }
  | { kind: "level"; row: ShareRow; level: ShareLevel }
  | { kind: "unshare"; row: ShareRow };

function Teams({
  layerName,
  homeTeam,
  teamsCardHref,
  list,
  rows,
  totalCount,
  canShare,
  canShareAsEditor,
  ask,
  share,
  onChangeLevel,
  onUnshare,
  refusal,
}: BrainSharingTabProps) {
  const t = useTranslations("brains.sharing");
  const [pending, setPending] = React.useState<Pending | null>(null);
  const levelText = (access: string) => {
    const level = shareLevel(access);
    return level ? t(`levels.${level}`) : access;
  };
  const columns: Column<ShareRow>[] = [
    {
      id: "team",
      header: t("team"),
      sortKey: "team",
      cell: (row) => <span className="font-mono">{row.team}</span>,
    },
    {
      id: "access",
      header: t("access"),
      sortKey: "access",
      cell: (row) => (
        <Badge variant={row.access === "owner" ? "default" : "secondary"} className="font-mono">
          {levelText(row.access)}
        </Badge>
      ),
    },
  ];
  const editable = (row: ShareRow) => canShare && shareLevel(row.access) !== "owner";
  const askText = [
    ask.verification ? t("askVerification") : null,
    ask.approval ? t("askApproval", { count: ask.minimumApprovals }) : null,
  ]
    .filter(Boolean)
    .join(" ");
  const shareTeam = share.team?.id ?? null;
  const blockedTeam = shareTeam === homeTeam;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <p className="text-muted-foreground text-sm">
        {t.rich("ownedBy", {
          team: homeTeam || t("noTeam"),
          code: (chunks) => <span className="text-foreground font-mono">{chunks}</span>,
        })}{" "}
        <Link href={teamsCardHref} className="text-primary hover:underline">
          {t("ownersLink")}
        </Link>
      </p>

      {refusal && (
        <p
          role="alert"
          className="border-danger-border bg-danger/5 text-danger-fg rounded-md border px-3 py-2 text-xs [overflow-wrap:anywhere]"
        >
          <span className="font-mono">{refusal.message}</span>
          {refusal.approval && (
            <>
              {" "}
              <Link href={APPROVALS_HREF} className="text-primary font-sans hover:underline">
                {t("viewRequests")}
              </Link>
            </>
          )}
        </p>
      )}

      <Restricted allowed={canShare} permission="app.update" verb={t("sharing")}>
        <Panel
          title={t("shareTitle")}
          icon={<Share2Icon className="size-4" />}
          description={askText || undefined}
        >
          <div className="grid min-w-0 gap-4 md:grid-cols-[minmax(0,1fr)_12rem_auto] md:items-end">
            <PrincipalPicker
              label={t("team")}
              value={share.team}
              onChange={share.onTeam}
              search={share.search}
              placeholder={t("findTeam")}
            />
            <div className="min-w-0 space-y-2">
              <Label htmlFor="share-level">{t("access")}</Label>
              <Select value={share.level} onValueChange={(v) => share.onLevel(v as ShareLevel)}>
                <SelectTrigger id="share-level" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="viewer">{t("levels.viewer")}</SelectItem>
                  <SelectItem value="deployer" disabled={!canShareAsEditor}>
                    {t("levels.deployer")}
                  </SelectItem>
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                {canShareAsEditor ? t(`levelHelp.${share.level}`) : t("editorNeedsDeploy")}
              </p>
            </div>
            <Button
              disabled={!shareTeam || blockedTeam || share.submitting}
              onClick={() =>
                shareTeam && setPending({ kind: "share", team: shareTeam, level: share.level })
              }
            >
              {share.submitting && <Loader2Icon className="size-4 animate-spin" />}
              {t("share")}
            </Button>
          </div>
          {blockedTeam && <p className="text-muted-foreground mt-2 text-xs">{t("homeTeamNote")}</p>}
        </Panel>
      </Restricted>

      <ListPage<ShareRow>
        embedded
        list={list}
        label={t("teams")}
        columns={columns}
        rows={rows}
        getRowId={(row) => row.team}
        totalCount={totalCount}
        empty={{
          icon: <UsersIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription", { team: homeTeam || t("noTeam") }),
        }}
        rowActions={
          canShare
            ? (row) =>
                editable(row) ? (
                  <>
                    {shareLevel(row.access) !== "viewer" && (
                      <DropdownMenuItem
                        onSelect={() => setPending({ kind: "level", row, level: "viewer" })}
                      >
                        {t("makeViewer")}
                      </DropdownMenuItem>
                    )}
                    {shareLevel(row.access) !== "deployer" && canShareAsEditor && (
                      <DropdownMenuItem
                        onSelect={() => setPending({ kind: "level", row, level: "deployer" })}
                      >
                        {t("makeEditor")}
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem
                      variant="destructive"
                      onSelect={() => setPending({ kind: "unshare", row })}
                    >
                      {t("unshare")}
                    </DropdownMenuItem>
                  </>
                ) : (
                  <DropdownMenuItem asChild>
                    <Link href={teamsCardHref}>{t("changeOnApp")}</Link>
                  </DropdownMenuItem>
                )
            : undefined
        }
      />

      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(open) => !open && setPending(null)}
        destructive={pending?.kind === "unshare"}
        title={
          pending?.kind === "unshare"
            ? t("unshareTitle", { team: pending.row.team })
            : pending
              ? t("shareConfirmTitle", {
                  name: layerName,
                  team: pending.kind === "share" ? pending.team : pending.row.team,
                  level: t(`levels.${pending.level}`),
                })
              : ""
        }
        description={
          pending?.kind === "unshare"
            ? t("unshareDescription")
            : pending
              ? [t(`levelHelp.${pending.level}`), askText].filter(Boolean).join(" ")
              : undefined
        }
        confirmLabel={pending?.kind === "unshare" ? t("unshare") : t("share")}
        onConfirm={() => {
          // The dialog closes at once: a fresh sign-in, if asked for, opens
          // its own prompt, and a refusal is said on the page.
          if (!pending) return;
          void (pending.kind === "share"
            ? share.onShare()
            : pending.kind === "level"
              ? onChangeLevel(pending.row, pending.level)
              : onUnshare(pending.row));
        }}
      />
    </div>
  );
}

function History({ history }: BrainSharingTabProps) {
  const t = useTranslations("brains.sharing");
  const age = useRelativeAge();
  if (!history.allowed)
    return <PermissionNote permission="audit_log.read" verb={t("readingHistory")} />;
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <p className="text-muted-foreground text-sm">{t("historyDescription")}</p>
      <Feed<HistoryEntry>
        label={t("history")}
        items={history.items}
        keyOf={(e) => e.id}
        loading={history.loading}
        error={history.error}
        onRetry={history.onRetry}
        hasMore={history.hasMore}
        loadingMore={history.loadingMore}
        onLoadMore={history.onLoadMore}
        empty={{ icon: <HistoryIcon className="size-5" />, title: t("historyEmpty") }}
        renderItem={(e) => (
          <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-1">
            <div className="min-w-0">
              <p className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
                <StatusDot
                  status={e.outcome === "ok" ? "ok" : e.outcome === "refused" ? "error" : "warn"}
                />
                <span className="font-medium">{e.actor}</span>{" "}
                <span>
                  {e.kind === "other" ? (
                    <span className="font-mono">{e.action}</span>
                  ) : (
                    t(`historyKinds.${e.kind}`)
                  )}
                </span>
                {e.outcome !== "ok" && (
                  <Badge variant="outline" className="font-mono">
                    {e.outcome === "refused"
                      ? t("refused", { code: e.code ?? "DENY" })
                      : t("failed", { code: e.code ?? "" })}
                  </Badge>
                )}
                {e.surface === "mcp" && (
                  <Badge variant="secondary" className="font-mono">
                    {t("viaAgent")}
                  </Badge>
                )}
              </p>
              {e.outcome === "ok" && e.sharedWith && (
                <p className="text-muted-foreground mt-0.5 text-xs [overflow-wrap:anywhere]">
                  {e.sharedWith.length
                    ? t("nowShared", {
                        teams: e.sharedWith.map((s) => `${s.team} (${s.access})`).join(", "),
                      })
                    : t("nowOwnerOnly")}
                </p>
              )}
            </div>
            <span className="text-muted-foreground shrink-0 font-mono text-xs" title={e.at}>
              {age(e.at)}
            </span>
          </div>
        )}
      />
    </div>
  );
}
