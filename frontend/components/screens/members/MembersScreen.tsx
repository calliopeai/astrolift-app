"use client";

import {
  DownloadIcon,
  MailIcon,
  MoreHorizontalIcon,
  SendIcon,
  ShieldCheckIcon,
  ShieldPlusIcon,
  Trash2Icon,
  UserMinusIcon,
  UsersIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { PrincipalChip } from "@/components/access/PrincipalChip";
import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { DetailStatusBadge, type Dot } from "@/components/detail/EntityDetailShell";
import { exportCsv } from "@/components/list/exportCsv";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import {
  accessCrumbs,
  grantHref,
  PEOPLE_HREF,
} from "@/components/screens/administration/access/access-nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { AstroliftInvitation } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { AnonymizeUserDialog } from "./AnonymizeUserDialog";
import { InvitationExpiryBadge } from "./InvitationExpiryBadge";
import { type PeopleRow, principalHref, principalOf, type UserRow } from "./people-model";
import type { useMembers } from "./use-members";
import { WALK_CAP } from "./use-walk";

export type MembersScreenProps = Omit<ReturnType<typeof useMembers>, "list"> & {
  list: ListStateController;
  /** The invite sheet, rendered by the caller (it runs its own queries). */
  renderInviteDialog?: (props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
  }) => React.ReactNode;
};

const LIFECYCLE_TONE: Record<string, Dot> = {
  active: "ok",
  invited: "warn",
  suspended: "warn",
  anonymized: "muted",
};

const STALE_THRESHOLD_DAYS = 90;
const ROLES_SHOWN = 3;
const TEAMS_SHOWN = 2;

type InviteTarget = { kind: "resend" | "revoke" | "delete"; invitation: AstroliftInvitation };

/**
 * Admin › Access › People (access UX design 3.1): one numbered list of
 * principals. Users and IdP groups are rows of it; invitations are the
 * Invited view. Invite and Grant access are the primary actions; a row opens
 * the person's or group's page, where their access is managed at its
 * source. Pure view; data comes from useMembers.
 */
export function MembersScreen({
  canManageMembers,
  list,
  rows,
  totalCount,
  filtered,
  loading,
  stale,
  error,
  truncated,
  onRetry,
  revokingInvite,
  deletingInvite,
  resendingInvite,
  onRevokeInvite,
  onResendInvite,
  onDeleteInvite,
  onAnonymize,
  renderInviteDialog,
}: MembersScreenProps) {
  const t = useTranslations("orgMembers");
  const fmt = useFormatters();
  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [inviteTarget, setInviteTarget] = React.useState<InviteTarget | null>(null);
  const [anonymizeTarget, setAnonymizeTarget] = React.useState<UserRow | null>(null);

  const columns: Column<PeopleRow>[] = [
    {
      id: "principal",
      header: "Who",
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (row) => (
        <PrincipalChip principal={principalOf(row)} variant="block" showId={row.kind === "group"} />
      ),
    },
    {
      id: "roles",
      header: "Roles",
      sortKey: "roles",
      cellClassName: "max-w-80",
      cell: (row) => <RolesCell row={row} />,
    },
    {
      id: "teams",
      header: "Teams",
      cellClassName: "max-w-56",
      cell: (row) => <TeamsCell row={row} />,
    },
    {
      id: "status",
      header: "Status",
      cell: (row) => <StatusCell row={row} />,
    },
    {
      id: "lastActive",
      header: t("lastActiveColumn"),
      sortKey: "lastActive",
      // Above the row link, so the tooltip opens.
      cellClassName: "relative z-10",
      cell: (row) =>
        row.kind === "user" ? (
          <LastActiveCell value={row.lastActiveAt} />
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        ),
    },
    {
      id: "joined",
      header: "Joined",
      sortKey: "joined",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (row) =>
        row.kind === "user"
          ? fmt.formatDate(row.joinedAt)
          : row.kind === "invitation"
            ? fmt.formatDate(row.invitation.createdAt)
            : "—",
    },
  ];

  function rowActions(row: PeopleRow): React.ReactNode {
    if (row.kind === "group") {
      return (
        <DropdownMenuItem asChild>
          <Link href={principalHref(row)}>
            <ShieldCheckIcon className="size-4" />
            View access
          </Link>
        </DropdownMenuItem>
      );
    }
    if (row.kind === "invitation") {
      const inv = row.invitation;
      return inv.status === "pending" ? (
        <>
          <DropdownMenuItem
            disabled={resendingInvite || revokingInvite}
            onSelect={() => setInviteTarget({ kind: "resend", invitation: inv })}
          >
            <SendIcon className="size-4" />
            Resend invitation
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            disabled={revokingInvite}
            onSelect={() => setInviteTarget({ kind: "revoke", invitation: inv })}
          >
            <Trash2Icon className="size-4" />
            Revoke
          </DropdownMenuItem>
        </>
      ) : (
        <DropdownMenuItem
          variant="destructive"
          disabled={deletingInvite}
          onSelect={() => setInviteTarget({ kind: "delete", invitation: inv })}
        >
          <Trash2Icon className="size-4" />
          Delete this resolved invitation
        </DropdownMenuItem>
      );
    }
    const anonymized = row.memberships.some((m) => m.lifecycle === "anonymized");
    return (
      <>
        <DropdownMenuItem asChild>
          <Link
            href={grantHref({
              principal: { kind: "user", id: row.user.id, name: row.user.username },
              returnTo: PEOPLE_HREF,
            })}
          >
            <ShieldPlusIcon className="size-4" />
            Grant access to {row.user.username}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem
          variant="destructive"
          disabled={anonymized}
          onSelect={() => setAnonymizeTarget(row)}
        >
          <UserMinusIcon className="size-4" />
          {anonymized ? "Already anonymized" : "Anonymize user data"}
        </DropdownMenuItem>
      </>
    );
  }

  const confirm = inviteTarget ? INVITE_CONFIRM[inviteTarget.kind](inviteTarget.invitation) : null;

  return (
    <TooltipProvider>
      <div className="flex min-w-0 flex-1 flex-col p-6">
        <ListPage<PeopleRow>
          header={{
            crumbs: accessCrumbs("people"),
            title: "People",
            context: truncated
              ? `Filters and sort cover the first ${WALK_CAP.toLocaleString()} rows until the members query filters on the server.`
              : "Users and IdP groups with access to this organization.",
            primaryAction: (
              <Can permission="org.manage_members">
                <div className="flex flex-wrap items-center gap-2">
                  <Button size="sm" variant="outline" onClick={() => setInviteOpen(true)}>
                    <MailIcon className="size-4" />
                    Invite
                  </Button>
                  <Button size="sm" asChild>
                    <Link href={grantHref({ returnTo: PEOPLE_HREF })}>
                      <ShieldPlusIcon className="size-4" />
                      Grant access
                    </Link>
                  </Button>
                </div>
              </Can>
            ),
          }}
          list={list}
          label="People"
          columns={columns}
          rows={rows}
          getRowId={(r) => r.key}
          rowHref={(r) =>
            r.kind === "invitation"
              ? `${PEOPLE_HREF}?view=invited&q=${encodeURIComponent(r.invitation.email)}`
              : principalHref(r)
          }
          rowActions={canManageMembers ? rowActions : undefined}
          loading={loading}
          stale={stale}
          error={error}
          onRetry={onRetry}
          totalCount={totalCount}
          menu={<ExportMenu rows={filtered} />}
          empty={{
            icon: <UsersIcon className="size-5" />,
            title: "No people yet",
            description: "Invite someone to the organization, then grant them a role.",
          }}
        />

        {renderInviteDialog?.({ open: inviteOpen, onOpenChange: setInviteOpen })}

        <ConfirmDialog
          open={confirm !== null}
          onOpenChange={(next) => {
            if (!next) setInviteTarget(null);
          }}
          title={confirm?.title ?? ""}
          description={confirm?.description ?? ""}
          confirmLabel={confirm?.confirmLabel ?? "Confirm"}
          destructive={inviteTarget?.kind !== "resend"}
          onConfirm={async () => {
            if (!inviteTarget) return;
            const inv = inviteTarget.invitation;
            if (inviteTarget.kind === "resend") await onResendInvite(inv);
            else if (inviteTarget.kind === "revoke") await onRevokeInvite(inv);
            else await onDeleteInvite(inv);
            setInviteTarget(null);
          }}
        />

        <AnonymizeUserDialog
          name={anonymizeTarget?.user.username ?? null}
          onOpenChange={(next) => {
            if (!next) setAnonymizeTarget(null);
          }}
          onConfirm={() =>
            anonymizeTarget ? onAnonymize(anonymizeTarget.user.id) : Promise.resolve(false)
          }
        />
      </div>
    </TooltipProvider>
  );
}

const INVITE_CONFIRM: Record<
  InviteTarget["kind"],
  (inv: AstroliftInvitation) => { title: string; description: string; confirmLabel: string }
> = {
  // Re-sending rotates the token, which kills any link already handed out.
  resend: (inv) => ({
    title: `Re-send invitation to ${inv.email}?`,
    description:
      "A fresh one-time link is generated and emailed. The link this person already has stops working immediately, so re-send only if the original was lost or never arrived.",
    confirmLabel: "Re-send invitation",
  }),
  revoke: (inv) => ({
    title: `Revoke invitation for ${inv.email}?`,
    description:
      "The pending invitation link stops working immediately. You can re-send a fresh invitation if needed.",
    confirmLabel: "Revoke",
  }),
  delete: (inv) => ({
    title: `Delete resolved invitation for ${inv.email}?`,
    description:
      "Removes this resolved invitation from the list. It has no effect on the person's membership or roles.",
    confirmLabel: "Delete",
  }),
};

/**
 * CSV export from the filter bar's `⋯` (spec 44 §5.1, Admin lists): every
 * row the view and filters match, not only the page on screen.
 */
function ExportMenu({ rows }: { rows: PeopleRow[] }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-8" aria-label="More list actions">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          disabled={rows.length === 0}
          onSelect={() =>
            exportCsv("people", rows, [
              { header: "Kind", value: (r) => r.kind },
              { header: "Name", value: (r) => principalOf(r).name },
              {
                header: "Email",
                value: (r) =>
                  r.kind === "user"
                    ? r.user.email
                    : r.kind === "invitation"
                      ? r.invitation.email
                      : "",
              },
              {
                header: "Roles",
                value: (r) =>
                  r.kind === "invitation"
                    ? (r.invitation.roleSlug ?? "")
                    : r.bindings.map((b) => `${b.role.slug}@${b.scopeKind}`).join(" "),
              },
              {
                header: "Teams",
                value: (r) =>
                  r.kind === "user" ? r.teams.map((t) => t.slug ?? `#${t.pk}`).join(" ") : "",
              },
              {
                header: "Status",
                value: (r) =>
                  r.kind === "user"
                    ? r.lifecycle
                    : r.kind === "invitation"
                      ? r.invitation.status
                      : "idp group",
              },
              { header: "Last active", value: (r) => (r.kind === "user" ? r.lastActiveAt : null) },
              {
                header: "Joined",
                value: (r) =>
                  r.kind === "user"
                    ? r.joinedAt
                    : r.kind === "invitation"
                      ? r.invitation.createdAt
                      : null,
              },
            ])
          }
        >
          <DownloadIcon className="size-4" />
          Export {rows.length} as CSV
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function RolesCell({ row }: { row: PeopleRow }) {
  if (row.kind === "invitation") {
    return row.invitation.roleSlug ? (
      <Badge variant="outline" className="max-w-full font-mono text-xs">
        <span className="truncate">{row.invitation.roleSlug}</span>
      </Badge>
    ) : (
      <span className="text-muted-foreground text-xs">—</span>
    );
  }
  if (row.bindings.length === 0) return <span className="text-muted-foreground text-xs">—</span>;
  const slugs = [...new Set(row.bindings.map((b) => b.role.slug))];
  return (
    <div className="flex min-w-0 flex-wrap gap-1">
      {slugs.slice(0, ROLES_SHOWN).map((slug) => (
        <Badge key={slug} variant="outline" className="max-w-full font-mono text-xs" title={slug}>
          <span className="truncate">{slug}</span>
        </Badge>
      ))}
      {slugs.length > ROLES_SHOWN && (
        <span className="text-muted-foreground font-mono text-xs">
          +{slugs.length - ROLES_SHOWN}
        </span>
      )}
    </div>
  );
}

function TeamsCell({ row }: { row: PeopleRow }) {
  if (row.kind !== "user" || row.teams.length === 0) {
    return <span className="text-muted-foreground text-xs">—</span>;
  }
  return (
    <div className="flex min-w-0 flex-wrap gap-x-2 gap-y-0.5">
      {row.teams.slice(0, TEAMS_SHOWN).map((team) => (
        <span
          key={team.pk}
          className="text-muted-foreground min-w-0 truncate font-mono text-xs"
          title={team.slug ?? `team #${team.pk}`}
        >
          {team.slug ?? `#${team.pk}`}
        </span>
      ))}
      {row.teams.length > TEAMS_SHOWN && (
        <span className="text-muted-foreground font-mono text-xs">
          +{row.teams.length - TEAMS_SHOWN}
        </span>
      )}
    </div>
  );
}

function StatusCell({ row }: { row: PeopleRow }) {
  if (row.kind === "group") {
    return (
      <div className="flex flex-wrap items-center gap-1">
        <Badge variant="secondary" className="text-2xs">
          IdP group
        </Badge>
        {row.admin && <AdminBadge />}
      </div>
    );
  }
  if (row.kind === "invitation") {
    const inv = row.invitation;
    return inv.status === "pending" ? (
      <InvitationExpiryBadge expiresAt={inv.expiresAt} />
    ) : (
      <Badge variant="secondary" className="font-mono capitalize">
        {inv.status}
      </Badge>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      <DetailStatusBadge status={row.lifecycle} tone={LIFECYCLE_TONE[row.lifecycle] ?? "muted"} />
      {row.admin && <AdminBadge />}
    </div>
  );
}

function AdminBadge() {
  return (
    <Badge variant="outline" className="text-2xs">
      admin
    </Badge>
  );
}

const RELATIVE_DIVISIONS: ReadonlyArray<{ amount: number; unit: Intl.RelativeTimeFormatUnit }> = [
  { amount: 60, unit: "second" },
  { amount: 60, unit: "minute" },
  { amount: 24, unit: "hour" },
  { amount: 7, unit: "day" },
  { amount: 4.34524, unit: "week" },
  { amount: 12, unit: "month" },
  { amount: Number.POSITIVE_INFINITY, unit: "year" },
];

function formatRelative(iso: string, now: number): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  let duration = (date.getTime() - now) / 1000;
  for (const division of RELATIVE_DIVISIONS) {
    if (Math.abs(duration) < division.amount) {
      return rtf.format(Math.round(duration), division.unit);
    }
    duration /= division.amount;
  }
  return iso;
}

/**
 * Relative time with the absolute one on hover, and a muted "inactive" chip
 * past `STALE_THRESHOLD_DAYS`. No value reads as "never", not as suspicious.
 * "Now" is snapshotted at mount so render stays pure.
 */
function LastActiveCell({ value }: { value: string | null | undefined }) {
  const t = useTranslations("orgMembers");
  const fmt = useFormatters();
  const [now] = React.useState(() => Date.now());
  if (!value) {
    return <span className="text-muted-foreground text-sm">{t("lastActiveNever")}</span>;
  }
  const date = new Date(value);
  const isStale = (now - date.getTime()) / (1000 * 60 * 60 * 24) > STALE_THRESHOLD_DAYS;
  return (
    <div className="flex flex-col items-start gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="text-foreground cursor-help text-sm">{formatRelative(value, now)}</span>
        </TooltipTrigger>
        <TooltipContent>{fmt.formatDateTime(date)}</TooltipContent>
      </Tooltip>
      {isStale && (
        <Badge variant="secondary" className="text-2xs">
          {t("lastActiveInactive")}
        </Badge>
      )}
    </div>
  );
}
