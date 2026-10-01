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
import { useNow, useTranslations } from "next-intl";
import * as React from "react";

import { PrincipalChip } from "@/components/access/PrincipalChip";
import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { DetailStatusBadge, type Dot } from "@/components/detail/EntityDetailShell";
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
import {
  localizedPeopleList,
  type PeopleRow,
  principalHref,
  principalOf,
  type UserRow,
} from "./people-model";
import type { useMembers } from "./use-members";

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
type PrivacyTarget = { row: UserRow; question: string };

/**
 * Admin › Access › People (access UX design 3.1): one numbered list of
 * principals. Users are the list, IdP groups its Groups view and
 * invitations its Invited view. Invite and Grant access are the primary
 * actions; a row opens the person's or group's page, where their access is
 * managed at its source. Pure view; data comes from useMembers.
 */
export function MembersScreen({
  canManageMembers,
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  onExportCsv,
  exportingCsv,
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
  const shared = useTranslations("shared.confirmation");
  const grant = useTranslations("shared.access.grant");
  const fmt = useFormatters();
  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [inviteTarget, setInviteTarget] = React.useState<InviteTarget | null>(null);
  const [anonymizeTarget, setAnonymizeTarget] = React.useState<PrivacyTarget | null>(null);
  const privacyQuestion = JSON.stringify([
    list.state.q,
    list.filters,
    list.state.sort,
    list.state.page,
    list.state.pageSize,
    list.state.after,
  ]);
  const privacyRow =
    canManageMembers && anonymizeTarget?.question === privacyQuestion
      ? rows.find(
          (row): row is UserRow =>
            row.kind === "user" &&
            row.user.id === anonymizeTarget.row.user.id &&
            row.user.username === anonymizeTarget.row.user.username
        )
      : undefined;
  if (anonymizeTarget && !privacyRow) setAnonymizeTarget(null);
  const currentPrivacyTarget = privacyRow ? anonymizeTarget : null;
  const currentPrivacyRef = React.useRef(currentPrivacyTarget);
  React.useLayoutEffect(() => {
    currentPrivacyRef.current = currentPrivacyTarget;
  }, [currentPrivacyTarget]);

  const columns: Column<PeopleRow>[] = [
    {
      id: "principal",
      header: t("people.columns.who"),
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (row) => (
        <PrincipalChip
          principal={{
            ...principalOf(row),
            ...(row.kind === "group"
              ? { detail: grant("groupDetail", { count: row.memberCount }) }
              : {}),
          }}
          variant="block"
          showId={row.kind === "group"}
        />
      ),
    },
    {
      id: "roles",
      header: t("people.columns.roles"),
      sortKey: "roles",
      cellClassName: "max-w-80",
      cell: (row) => <RolesCell row={row} />,
    },
    {
      id: "teams",
      header: t("people.columns.teams"),
      cellClassName: "max-w-56",
      cell: (row) => <TeamsCell row={row} />,
    },
    {
      id: "status",
      header: t("people.columns.status"),
      cell: (row) => <StatusCell row={row} />,
    },
    {
      id: "lastActive",
      header: t("lastActiveColumn"),
      sortKey: "lastActive",
      // Above the row link, so the tooltip opens.
      cellClassName: "relative z-10",
      cell: (row) =>
        row.kind === "user" || (row.kind === "invitation" && row.invitation.lastActiveAt) ? (
          <LastActiveCell
            value={row.kind === "user" ? row.lastActiveAt : (row.invitation.lastActiveAt ?? null)}
          />
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        ),
    },
    {
      id: "joined",
      header: t("people.columns.joined"),
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
            {t("people.actions.viewAccess")}
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
            {t("people.actions.resend")}
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            disabled={revokingInvite}
            onSelect={() => setInviteTarget({ kind: "revoke", invitation: inv })}
          >
            <Trash2Icon className="size-4" />
            {t("people.actions.revoke")}
          </DropdownMenuItem>
        </>
      ) : (
        <DropdownMenuItem
          variant="destructive"
          disabled={deletingInvite}
          onSelect={() => setInviteTarget({ kind: "delete", invitation: inv })}
        >
          <Trash2Icon className="size-4" />
          {t("people.actions.deleteResolved")}
        </DropdownMenuItem>
      );
    }
    const anonymized = row.user.isAnonymized === true;
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
            {t("people.actions.grantTo", { name: row.user.username })}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem
          variant="destructive"
          onSelect={() => setAnonymizeTarget({ row, question: privacyQuestion })}
        >
          <UserMinusIcon className="size-4" />
          {t(anonymized ? "people.actions.reviewPrivacyCleanup" : "people.actions.anonymize")}
        </DropdownMenuItem>
      </>
    );
  }

  const confirm = inviteTarget
    ? {
        title: t(`people.confirm.${inviteTarget.kind}.title`, {
          email: inviteTarget.invitation.email,
        }),
        description: t(`people.confirm.${inviteTarget.kind}.description`),
        confirmLabel: t(`people.confirm.${inviteTarget.kind}.label`),
      }
    : null;

  return (
    <TooltipProvider>
      <div className="flex min-w-0 flex-1 flex-col p-6">
        <ListPage<PeopleRow>
          header={{
            crumbs: accessCrumbs("people"),
            title: t("people.title"),
            context: t("people.description"),
            primaryAction: (
              <Can permission="org.manage_members">
                <div className="flex flex-wrap items-center gap-2">
                  <Button size="sm" variant="outline" onClick={() => setInviteOpen(true)}>
                    <MailIcon className="size-4" />
                    {t("people.actions.invite")}
                  </Button>
                  <Button size="sm" asChild>
                    <Link href={grantHref({ returnTo: PEOPLE_HREF })}>
                      <ShieldPlusIcon className="size-4" />
                      {t("people.actions.grant")}
                    </Link>
                  </Button>
                </div>
              </Can>
            ),
          }}
          list={{ ...list, definition: localizedPeopleList(list.definition, t) }}
          label={t("people.title")}
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
          menu={<ExportMenu count={totalCount} onExport={onExportCsv} exporting={exportingCsv} />}
          empty={{
            icon: <UsersIcon className="size-5" />,
            title: t("people.emptyTitle"),
            description: t("people.emptyDescription"),
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
          confirmLabel={confirm?.confirmLabel ?? shared("confirm")}
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
          name={privacyRow?.user.username ?? null}
          targetId={privacyRow?.user.id ?? null}
          isAnonymized={privacyRow?.user.isAnonymized}
          onOpenChange={(next) => {
            if (!next)
              setAnonymizeTarget((current) => (current === currentPrivacyTarget ? null : current));
          }}
          onConfirm={() =>
            privacyRow && currentPrivacyTarget && currentPrivacyRef.current === currentPrivacyTarget
              ? onAnonymize(privacyRow.user.id)
              : Promise.resolve(false)
          }
        />
      </div>
    </TooltipProvider>
  );
}

/**
 * CSV export from the filter bar's `⋯` (spec 44 §5.1, Admin lists): every
 * row the view and filters match, walked from the server, not only the page
 * on screen.
 */
function ExportMenu({
  count,
  onExport,
  exporting,
}: {
  count: number;
  onExport: () => void;
  exporting: boolean;
}) {
  const t = useTranslations("orgMembers.people.export");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-8" aria-label={t("actions")}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem disabled={count === 0 || exporting} onSelect={onExport}>
          <DownloadIcon className="size-4" />
          {exporting ? t("working") : t("label", { count })}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function RolesCell({ row }: { row: PeopleRow }) {
  const t = useTranslations("orgMembers.people");
  if (row.kind === "group") {
    return (
      <span className="text-muted-foreground text-xs">
        {t("grants", { count: row.bindingsCount })}
        {row.mappingsCount > 0 && t("mappings", { count: row.mappingsCount })}
      </span>
    );
  }
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
          key={team.id}
          className="text-muted-foreground min-w-0 truncate font-mono text-xs"
          title={team.name}
        >
          {team.slug}
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
  const t = useTranslations("orgMembers.people");
  if (row.kind === "group") {
    return (
      <Badge variant="secondary" className="text-2xs">
        {t("idpGroup")}
      </Badge>
    );
  }
  if (row.kind === "invitation") {
    const inv = row.invitation;
    return inv.status === "pending" ? (
      <InvitationExpiryBadge expiresAt={inv.expiresAt} />
    ) : (
      <Badge variant="secondary" className="font-mono capitalize">
        {t.has(`invitationStatus.${inv.status}`) ? t(`invitationStatus.${inv.status}`) : inv.status}
      </Badge>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      <DetailStatusBadge
        status={row.lifecycle}
        label={
          t.has(`lifecycle.${row.lifecycle}`) ? t(`lifecycle.${row.lifecycle}`) : row.lifecycle
        }
        tone={LIFECYCLE_TONE[row.lifecycle] ?? "muted"}
      />
      {row.admin && <AdminBadge />}
    </div>
  );
}

function AdminBadge() {
  const t = useTranslations("orgMembers.people");
  return (
    <Badge variant="outline" className="text-2xs">
      {t("admin")}
    </Badge>
  );
}

/**
 * Relative time with the absolute one on hover, and a muted "inactive" chip
 * past `STALE_THRESHOLD_DAYS`. No value reads as "never", not as suspicious.
 * The provider's request clock keeps server and initial client render consistent.
 */
function LastActiveCell({ value }: { value: string | null | undefined }) {
  const t = useTranslations("orgMembers");
  const fmt = useFormatters();
  const now = useNow();
  if (!value) {
    return <span className="text-muted-foreground text-sm">{t("lastActiveNever")}</span>;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime()))
    return <span className="text-muted-foreground text-sm">{value}</span>;
  const isStale = (now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24) > STALE_THRESHOLD_DAYS;
  return (
    <div className="flex flex-col items-start gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="text-foreground cursor-help text-sm">
            {fmt.formatRelativeTime(value, now)}
          </span>
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
