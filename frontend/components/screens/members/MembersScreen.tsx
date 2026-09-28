"use client";

import {
  AlertTriangleIcon,
  DownloadIcon,
  InfoIcon,
  MailIcon,
  MoreHorizontalIcon,
  SendIcon,
  Trash2Icon,
  UserMinusIcon,
  UserPlusIcon,
  UsersIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column, RowSelection } from "@/components/data-table";
import { type CsvColumn, exportCsv } from "@/components/list/exportCsv";
import { ListPage, type ListPageProps } from "@/components/list/ListPage";
import { adminCrumb } from "@/components/screens/administration/access/admin-crumbs";
import type { ListPageData } from "@/components/screens/administration/access/use-list-page-query";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type {
  AstroliftInvitation,
  AstroliftMember,
  AstroliftRole,
  AstroliftRoleBinding,
} from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { InvitationExpiryBadge } from "./InvitationExpiryBadge";
import { MEMBERS_SEARCH_PLACEHOLDER, type MembersView } from "./members-list";
import type { useMembers } from "./use-members";

export type MembersScreenProps = ReturnType<typeof useMembers> & {
  /**
   * The grant-role sheet, rendered by the caller (it runs its own
   * queries). The screen owns whether it is open and for which member.
   */
  renderGrantDialog?: (props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    roles: AstroliftRole[];
    initialUserId: string | null;
    initialUserLabel: string | null;
  }) => React.ReactNode;
  /** The invite sheet, rendered by the caller (it runs its own queries). */
  renderInviteDialog?: (props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
  }) => React.ReactNode;
};

const scopeBadge: Record<string, string> = {
  ORG: "bg-info/15 text-info-fg",
  TEAM: "bg-chart-3/15 text-chart-3",
  PROJECT: "bg-success/15 text-success-fg",
  APP: "bg-warning/15 text-warning-fg",
};

type RevokeTarget =
  | { kind: "invitation"; invitation: AstroliftInvitation }
  | { kind: "binding"; binding: AstroliftRoleBinding };

/**
 * One People row per USER (#1229) — a user can hold several Member rows
 * (ORG plus per-APP rows auto-created by app-scope grants) and rendering
 * one row per membership reads as a duplicate account.
 */
type MemberGroup = { rows: AstroliftMember[]; primary: AstroliftMember };

const STALE_THRESHOLD_DAYS = 90;

/**
 * Cells that own their own pointer affordances (hover tooltips) have to sit
 * above ``rowHref``'s stretched row link, which is an absolutely-positioned
 * overlay across the whole row.
 */
const ABOVE_ROW_LINK = "relative z-10";

/**
 * Admin › Members (spec 44 §5.1): one list whose views are the people, the
 * pending invitations, the invitation history and the role bindings. Pure
 * view; data comes from useMembers.
 */
export function MembersScreen({
  canManageMembers,
  list,
  people,
  invitations,
  bindings,
  bindingIndexRows: bindingIndexData,
  teams,
  projects,
  roles,
  rolesLoading,
  revoking,
  bulkRevoking,
  revokingInvite,
  deletingInvite,
  resendingInvite,
  onRevokeInvite,
  onResendInvite,
  onDeleteInvite,
  onRevokeBinding,
  onBulkRevoke,
  onAnonymize,
  renderGrantDialog,
  renderInviteDialog,
}: MembersScreenProps) {
  const t = useTranslations("orgMembers");
  const tBulk = useTranslations("lists.membersBulk");
  const fmt = useFormatters();
  const view = list.state.view as MembersView;

  const [open, setOpen] = React.useState(false);
  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<RevokeTarget | null>(null);
  // Deep-link grant: when set, the GrantRoleDialog opens pre-populated
  // with this member's user PK so the operator skips the user picker.
  // The dialog calls onOpenChange(false) on submit/cancel, which clears
  // this back to null via the wrapper handler below.
  const [grantForMember, setGrantForMember] = React.useState<AstroliftMember | null>(null);
  const [deleteInviteTarget, setDeleteInviteTarget] = React.useState<AstroliftInvitation | null>(
    null
  );
  // Re-sending rotates the invitation token, which kills any link the
  // operator already handed out — confirmed rather than fired on click.
  const [resendTarget, setResendTarget] = React.useState<AstroliftInvitation | null>(null);
  const [bulkTarget, setBulkTarget] = React.useState<RowSelection | null>(null);
  const bulkCount = bulkTarget?.selectedCount ?? 0;

  async function handleBulkRevoke() {
    if (!bulkTarget) return;
    try {
      if (await onBulkRevoke(bulkTarget.selectedIds)) bulkTarget.clear();
    } finally {
      setBulkTarget(null);
    }
  }

  const ANONYMIZE_BACKEND_READY = true;
  const [anonymizeTarget, setAnonymizeTarget] = React.useState<AstroliftMember | null>(null);
  const [anonymizeAcknowledged, setAnonymizeAcknowledged] = React.useState(false);

  function openAnonymizeDialog(m: AstroliftMember) {
    setAnonymizeAcknowledged(false);
    setAnonymizeTarget(m);
  }

  const bindingIndexRows = React.useMemo(() => bindingIndexData ?? [], [bindingIndexData]);
  const teamList = teams ?? [];
  const projectList = projects ?? [];

  // Lookup tables for the Scope column. Pre-existing scopes were
  // rendered as a bare badge ("TEAM" / "PROJECT") that operators
  // read as a role name; surface the actual team / project name
  // alongside.
  const teamById = new Map(teamList.map((team) => [team.id, team]));
  const projectById = new Map(projectList.map((project) => [project.id, project]));
  // APP-scope labels come from the bindings' server-resolved
  // sourceScopeLabel (the client doesn't load the app list here).
  const appScopeLabels = new Map<string, string>();
  for (const b of bindingIndexRows) {
    if (b.scopeKind === "APP" && b.sourceScopeLabel) {
      appScopeLabels.set(String(b.scopeId), b.sourceScopeLabel);
    }
  }
  function scopeLabel(m: AstroliftMember): string {
    if (m.scopeKind === "TEAM") {
      return teamById.get(m.scopeId)?.slug ?? "—";
    }
    if (m.scopeKind === "PROJECT") {
      const p = projectById.get(m.scopeId);
      return p ? `${p.team.slug}/${p.slug}` : "—";
    }
    if (m.scopeKind === "ORG") {
      return "organization";
    }
    if (m.scopeKind === "APP") {
      return appScopeLabels.get(String(m.scopeId)) ?? "app";
    }
    return "—";
  }

  // Group bindings by user for the People-row role pills.
  const bindingsByUser = new Map<string, AstroliftRoleBinding[]>();
  for (const b of bindingIndexRows) {
    if (!b.user) continue;
    const arr = bindingsByUser.get(b.user.id) ?? [];
    arr.push(b);
    bindingsByUser.set(b.user.id, arr);
  }

  // Collapse the page's Member rows to one row per user (#1229). The
  // grouping is per page: the member walk is ordered by creation time,
  // so a user's ORG row and a later APP row can land on either side of
  // a page boundary and show up once on each.
  const memberGroups = React.useMemo<MemberGroup[]>(() => {
    const byUser = new Map<string, AstroliftMember[]>();
    for (const m of people.rows) {
      const arr = byUser.get(m.user.id) ?? [];
      arr.push(m);
      byUser.set(m.user.id, arr);
    }
    return Array.from(byUser.values()).map((rows) => ({
      rows,
      primary: rows.find((r) => r.scopeKind === "ORG") ?? rows[0],
    }));
  }, [people.rows]);

  const peopleColumns: Column<MemberGroup>[] = [
    {
      id: "user",
      header: "User",
      cellClassName: "max-w-72",
      cell: ({ primary }) => (
        // The id is the anchor InviterCell deep-links to (`#u-<userId>`).
        <div id={`u-${primary.user.id}`} className="min-w-0">
          <div className="truncate font-medium" title={primary.user.username}>
            {primary.user.username}
          </div>
          <div
            className="text-muted-foreground truncate font-mono text-xs"
            title={primary.user.email}
          >
            {primary.user.email}
          </div>
        </div>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cellClassName: "max-w-64",
      cell: ({ rows }) => (
        <div className="flex min-w-0 flex-col items-start gap-1">
          {rows.map((r) => (
            <div key={r.id} className="flex max-w-full min-w-0 items-center gap-1.5">
              <Badge className={`${scopeBadge[r.scopeKind] ?? ""} font-mono`} variant="secondary">
                {r.scopeKind}
              </Badge>
              <span className="text-muted-foreground min-w-0 truncate font-mono text-xs">
                {scopeLabel(r)}
              </span>
            </div>
          ))}
        </div>
      ),
    },
    {
      id: "roles",
      header: "Roles",
      cellClassName: `${ABOVE_ROW_LINK} max-w-80`,
      cell: ({ primary }) => {
        const userBindings = bindingsByUser.get(primary.user.id) ?? [];
        if (userBindings.length === 0) {
          return <span className="text-muted-foreground text-xs">—</span>;
        }
        return (
          <div className="flex min-w-0 flex-wrap gap-1">
            {userBindings.map((b) => (
              <RoleSourcePill key={b.id} binding={b} />
            ))}
          </div>
        );
      },
    },
    {
      id: "lastActive",
      header: t("lastActiveColumn"),
      cellClassName: ABOVE_ROW_LINK,
      cell: ({ primary }) => <LastActiveCell value={primary.lastActiveAt} />,
    },
    {
      id: "joined",
      header: "Joined",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: ({ primary }) =>
        primary.joinedAt ? fmt.formatDate(primary.joinedAt) : fmt.formatDate(primary.createdAt),
    },
  ];

  const bindingColumns: Column<AstroliftRoleBinding>[] = [
    {
      id: "subject",
      header: "Subject",
      cellClassName: "max-w-72",
      cell: (b) =>
        b.user ? (
          <div className="min-w-0">
            <div className="truncate font-medium" title={b.user.username}>
              {b.user.username}
            </div>
            <div className="text-muted-foreground truncate font-mono text-xs" title={b.user.email}>
              {b.user.email}
            </div>
          </div>
        ) : (
          <div className="truncate font-mono text-xs" title={`group:${b.groupExternalId}`}>
            group:{b.groupExternalId}
          </div>
        ),
    },
    {
      id: "role",
      header: "Role",
      cellClassName: "max-w-72",
      cell: (b) => (
        <div className="min-w-0">
          <div className="truncate font-medium" title={b.role.name}>
            {b.role.name}
          </div>
          <div className="text-muted-foreground truncate font-mono text-xs" title={b.role.slug}>
            {b.role.slug}
          </div>
        </div>
      ),
    },
    {
      id: "source",
      header: t("sourceColumn"),
      cell: (b) => (
        <div className="flex items-center gap-1.5">
          <Badge className={`${scopeBadge[b.scopeKind] ?? ""} font-mono`} variant="secondary">
            {b.scopeKind}
          </Badge>
          {b.sourceScopeLabel && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground inline-flex"
                  aria-label={t("sourceTooltipAria", { scope: b.sourceScopeLabel })}
                >
                  <InfoIcon className="size-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent>
                {t("rolePillTooltipPrefix", { scope: b.sourceScopeLabel })}
              </TooltipContent>
            </Tooltip>
          )}
        </div>
      ),
    },
    {
      id: "granted",
      header: "Granted",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (b) => fmt.formatDate(b.grantedAt),
    },
  ];

  const invitationColumns: Column<AstroliftInvitation>[] = [
    {
      id: "email",
      header: "Email",
      cellClassName: "max-w-80",
      cell: (inv) => (
        <span className="block min-w-0 truncate font-mono text-sm" title={inv.email}>
          {inv.email}
        </span>
      ),
    },
    {
      id: "role",
      header: "Role",
      cellClassName: "max-w-64",
      cell: (inv) =>
        inv.roleSlug ? (
          <Badge variant="outline" className="max-w-full font-mono text-xs" title={inv.roleSlug}>
            <span className="truncate">{inv.roleSlug}</span>
          </Badge>
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        ),
    },
    {
      id: "status",
      header: "Status",
      cell: (inv) => (
        <Badge
          variant={inv.status === "pending" ? "default" : "secondary"}
          className="font-mono capitalize"
        >
          {inv.status}
        </Badge>
      ),
    },
    {
      id: "expires",
      header: "Expires",
      cell: (inv) =>
        inv.status === "pending" ? (
          <InvitationExpiryBadge expiresAt={inv.expiresAt} />
        ) : (
          <span className="text-muted-foreground font-mono text-xs">
            {fmt.formatDate(inv.expiresAt)}
          </span>
        ),
    },
    {
      id: "invitedBy",
      header: "Invited by",
      cellClassName: "max-w-72",
      cell: (inv) => <InviterCell invitation={inv} />,
    },
  ];

  // The page's header, shared by every view: the views are its tabs.
  const header: ListPageProps<unknown>["header"] = {
    crumbs: [adminCrumb("members"), { label: "Members" }],
    title: "Members",
    context: "Users with access to this organization, and the role bindings behind them.",
    primaryAction: (
      <Can permission="org.manage_members">
        <Button size="sm" onClick={() => setInviteOpen(true)}>
          <MailIcon className="size-4" />
          Invite
        </Button>
      </Can>
    ),
    menu: canManageMembers ? (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="size-8" aria-label="More member actions">
            <MoreHorizontalIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem disabled={rolesLoading} onSelect={() => setOpen(true)}>
            <UserPlusIcon className="size-4" />
            Grant role
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    ) : undefined,
  };

  // Each view searches different fields; the box says which.
  const viewList = {
    ...list,
    definition: { ...list.definition, searchPlaceholder: MEMBERS_SEARCH_PLACEHOLDER[view] },
  };

  const shared = { header, list: viewList };

  function listProps<TRow>(page: ListPageData<TRow>) {
    return {
      loading: page.loading,
      stale: page.stale,
      error: page.error,
      onRetry: page.refetch,
      totalCount: page.totalCount,
      nextCursor: page.nextCursor,
    };
  }

  const pageEl =
    view === "bindings" ? (
      <ListPage<AstroliftRoleBinding>
        {...shared}
        {...listProps(bindings)}
        label="Role bindings"
        columns={bindingColumns}
        rows={bindings.rows}
        getRowId={(b) => b.id}
        menu={
          <ExportMenu
            filename="role-bindings"
            rows={bindings.rows}
            columns={[
              { header: "Subject", value: (b) => b.user?.username ?? `group:${b.groupExternalId}` },
              { header: "Email", value: (b) => b.user?.email ?? "" },
              { header: "Role", value: (b) => b.role.slug },
              { header: "Scope", value: (b) => b.scopeKind },
              { header: "Source", value: (b) => b.sourceScopeLabel },
              { header: "Granted", value: (b) => b.grantedAt },
            ]}
          />
        }
        rowActions={
          canManageMembers
            ? (b) => (
                <DropdownMenuItem
                  variant="destructive"
                  disabled={revoking || bulkRevoking}
                  onSelect={() => setRevokeTarget({ kind: "binding", binding: b })}
                >
                  <Trash2Icon className="size-4" />
                  Revoke
                </DropdownMenuItem>
              )
            : undefined
        }
        bulkActions={
          canManageMembers
            ? (selection) => (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setBulkTarget(selection)}
                  disabled={bulkRevoking}
                >
                  <Trash2Icon className="size-4" />
                  {tBulk("revokeButton", { count: selection.selectedCount })}
                </Button>
              )
            : undefined
        }
        empty={{
          icon: <UsersIcon className="size-5" />,
          title: "No role bindings",
          description: "Grant a system role to a user to give them access to the platform.",
        }}
      />
    ) : view === "invited" || view === "invitations" ? (
      <ListPage<AstroliftInvitation>
        {...shared}
        {...listProps(invitations)}
        label="Invitations"
        columns={invitationColumns}
        rows={invitations.rows}
        getRowId={(inv) => inv.id}
        menu={
          <ExportMenu
            filename={view === "invited" ? "invitations-pending" : "invitations"}
            rows={invitations.rows}
            columns={[
              { header: "Email", value: (inv) => inv.email },
              { header: "Role", value: (inv) => inv.roleSlug },
              { header: "Status", value: (inv) => inv.status },
              { header: "Expires", value: (inv) => inv.expiresAt },
              {
                header: "Invited by",
                value: (inv) => inv.invitedByDisplayName ?? inv.invitedByUsername,
              },
            ]}
          />
        }
        rowActions={
          canManageMembers
            ? (inv) =>
                inv.status === "pending" ? (
                  <>
                    <DropdownMenuItem
                      disabled={resendingInvite || revokingInvite}
                      onSelect={() => setResendTarget(inv)}
                    >
                      <SendIcon className="size-4" />
                      Resend invitation
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      variant="destructive"
                      disabled={revokingInvite}
                      onSelect={() => setRevokeTarget({ kind: "invitation", invitation: inv })}
                    >
                      <Trash2Icon className="size-4" />
                      Revoke
                    </DropdownMenuItem>
                  </>
                ) : (
                  <DropdownMenuItem
                    variant="destructive"
                    disabled={deletingInvite}
                    onSelect={() => setDeleteInviteTarget(inv)}
                  >
                    <Trash2Icon className="size-4" />
                    Delete this resolved invitation
                  </DropdownMenuItem>
                )
            : undefined
        }
        empty={{
          icon: <MailIcon className="size-5" />,
          title: "No invitations",
          description:
            "Use Invite to send a one-time accept link. Tokens are hashed at rest; the plaintext is shown once at creation.",
        }}
      />
    ) : (
      <ListPage<MemberGroup>
        {...shared}
        {...listProps(people)}
        label="People"
        columns={peopleColumns}
        rows={memberGroups}
        getRowId={(g) => g.primary.user.id}
        rowHref={(g) => `/administration/members/${g.primary.id}`}
        menu={
          <ExportMenu
            filename="members"
            rows={memberGroups}
            columns={[
              { header: "Username", value: (g) => g.primary.user.username },
              { header: "Email", value: (g) => g.primary.user.email },
              { header: "Scopes", value: (g) => g.rows.map((r) => r.scopeKind).join(" ") },
              {
                header: "Roles",
                value: (g) =>
                  (bindingsByUser.get(g.primary.user.id) ?? []).map((b) => b.role.slug).join(" "),
              },
              { header: "Last active", value: (g) => g.primary.lastActiveAt },
              { header: "Joined", value: (g) => g.primary.joinedAt ?? g.primary.createdAt },
            ]}
          />
        }
        rowActions={
          canManageMembers
            ? ({ rows, primary }) => {
                const alreadyAnonymized = rows.some((r) => r.lifecycle === "anonymized");
                return (
                  <>
                    <DropdownMenuItem onSelect={() => setGrantForMember(primary)}>
                      <UserPlusIcon className="size-4" />
                      {t("grantRoleRowLabel", { name: primary.user.username })}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      variant="destructive"
                      disabled={alreadyAnonymized}
                      onSelect={() => openAnonymizeDialog(primary)}
                    >
                      <UserMinusIcon className="size-4" />
                      {alreadyAnonymized ? "Already anonymized" : "Anonymize user data"}
                    </DropdownMenuItem>
                  </>
                );
              }
            : undefined
        }
        empty={{
          icon: <UsersIcon className="size-5" />,
          title: "No members",
          description: "Members appear here once role bindings are granted to users.",
        }}
      />
    );

  return (
    <TooltipProvider>
      <div className="flex min-w-0 flex-1 flex-col p-6">
        {pageEl}

        <ConfirmDialog
          open={bulkTarget !== null}
          onOpenChange={(next) => {
            if (!next) setBulkTarget(null);
          }}
          title={tBulk("confirm.title", { count: bulkCount })}
          description={tBulk("confirm.description")}
          confirmLabel={tBulk("confirm.confirmLabel", { count: bulkCount })}
          destructive
          onConfirm={handleBulkRevoke}
        />

        {renderGrantDialog?.({
          open: open || grantForMember !== null,
          onOpenChange: (next) => {
            setOpen(next);
            if (!next) setGrantForMember(null);
          },
          roles: roles ?? [],
          initialUserId: grantForMember?.user.id ?? null,
          initialUserLabel: grantForMember?.user.username ?? null,
        })}
        {renderInviteDialog?.({ open: inviteOpen, onOpenChange: setInviteOpen })}

        <ConfirmDialog
          open={resendTarget !== null}
          onOpenChange={(next) => {
            if (!next) setResendTarget(null);
          }}
          title={`Re-send invitation to ${resendTarget?.email ?? ""}?`}
          description="A fresh one-time link is generated and emailed. The link this person already has stops working immediately, so re-send only if the original was lost or never arrived."
          confirmLabel="Re-send invitation"
          onConfirm={async () => {
            if (!resendTarget) return;
            await onResendInvite(resendTarget);
          }}
        />

        <ConfirmDialog
          open={deleteInviteTarget !== null}
          onOpenChange={(next) => {
            if (!next) setDeleteInviteTarget(null);
          }}
          title={`Delete resolved invitation for ${deleteInviteTarget?.email ?? ""}?`}
          description="Removes this resolved invitation from the list. It has no effect on the person's membership or roles."
          confirmLabel="Delete"
          destructive
          onConfirm={async () => {
            if (!deleteInviteTarget) return;
            await onDeleteInvite(deleteInviteTarget);
            setDeleteInviteTarget(null);
          }}
        />

        <ConfirmDialog
          open={revokeTarget !== null}
          onOpenChange={(next) => {
            if (!next) setRevokeTarget(null);
          }}
          title={
            revokeTarget?.kind === "invitation"
              ? `Revoke invitation for ${revokeTarget.invitation.email}?`
              : revokeTarget?.kind === "binding"
                ? `Revoke ${revokeTarget.binding.role.slug} from ${revokeTarget.binding.user?.username ?? revokeTarget.binding.groupExternalId}?`
                : "Revoke?"
          }
          description={
            revokeTarget?.kind === "invitation"
              ? "The pending invitation link stops working immediately. You can re-send a fresh invitation if needed."
              : "The user loses the permissions this role granted. Other role bindings, if any, remain in effect."
          }
          confirmLabel="Revoke"
          destructive
          onConfirm={async () => {
            if (!revokeTarget) return;
            if (revokeTarget.kind === "invitation") {
              await onRevokeInvite(revokeTarget.invitation);
            } else {
              await onRevokeBinding(revokeTarget.binding);
            }
          }}
        />

        <AnonymizeUserDialog
          target={anonymizeTarget}
          onOpenChange={(next) => {
            if (!next) setAnonymizeTarget(null);
          }}
          acknowledged={anonymizeAcknowledged}
          onAcknowledgedChange={setAnonymizeAcknowledged}
          backendReady={ANONYMIZE_BACKEND_READY}
          onConfirm={() =>
            anonymizeTarget ? onAnonymize(anonymizeTarget) : Promise.resolve(false)
          }
        />
      </div>
    </TooltipProvider>
  );
}

/**
 * CSV export from the filter bar's `⋯` (spec 44 §5.1, Admin lists). It
 * writes the rows on screen: the page queries have no export or unpaged
 * read, so the label says "this page" rather than promising the whole list.
 */
function ExportMenu<TRow>({
  filename,
  rows,
  columns,
}: {
  filename: string;
  rows: TRow[];
  columns: CsvColumn<TRow>[];
}) {
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
          onSelect={() => exportCsv(filename, rows, columns)}
        >
          <DownloadIcon className="size-4" />
          Export this page as CSV
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
/* ---- helper components ---------------------------------------------- */

/**
 * Role pill with a hover tooltip when the binding's source scope label
 * is present. Mirrors the role-bindings table's info-icon affordance so
 * the operator can confirm "why does this user have this role" from
 * either surface without a second click.
 */
function RoleSourcePill({ binding }: { binding: AstroliftRoleBinding }) {
  const t = useTranslations("orgMembers");
  if (!binding.sourceScopeLabel) {
    return (
      <Badge variant="outline" className="font-mono text-xs">
        {binding.role.slug}
      </Badge>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" className="cursor-help font-mono text-xs">
          {binding.role.slug}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        {t("rolePillTooltipPrefix", { scope: binding.sourceScopeLabel })}
      </TooltipContent>
    </Tooltip>
  );
}

const RELATIVE_DIVISIONS: ReadonlyArray<{
  amount: number;
  unit: Intl.RelativeTimeFormatUnit;
}> = [
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
 * Last-active cell: relative time + hover-tooltip with absolute, plus
 * a muted "inactive" chip when older than ``STALE_THRESHOLD_DAYS``.
 * Null values render as "—" rather than "never" so a user with no
 * audit records yet doesn't read as suspicious.
 *
 * The "now" reference is snapshotted at mount via useState's lazy
 * initializer so React's purity rules don't trip on a render-time
 * ``Date.now()`` call. Re-snapshot is fine — even if a row sits on
 * screen for an hour, the "3d ago" vs "3d ago" delta is invisible.
 */
function LastActiveCell({ value }: { value: string | null | undefined }) {
  const t = useTranslations("orgMembers");
  const fmt = useFormatters();
  const [now] = React.useState(() => Date.now());
  if (!value) {
    return <span className="text-muted-foreground text-sm">{t("lastActiveNever")}</span>;
  }
  const date = new Date(value);
  const daysAgo = (now - date.getTime()) / (1000 * 60 * 60 * 24);
  const isStale = daysAgo > STALE_THRESHOLD_DAYS;
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

/**
 * Render the "invited by" cell on the invitation row (#418).
 * Falls back to the username (legacy backend) when the richer
 * attribution fields aren't populated yet — keeps the column useful
 * for invitations created before the enrichment landed. When a user
 * id is present, the display name links to the inviter's row in the
 * members list (filtered via the user-id hash anchor so the row
 * scrolls into view).
 */
function InviterCell({ invitation }: { invitation: AstroliftInvitation }) {
  const display = invitation.invitedByDisplayName ?? invitation.invitedByUsername ?? null;
  if (display === null) {
    return <span className="text-muted-foreground text-sm">—</span>;
  }
  const avatarUrl = invitation.invitedByAvatarUrl ?? "";
  const email = invitation.invitedByEmail ?? "";
  const userId = invitation.invitedByUserId ?? null;
  return (
    <div className="flex items-center gap-2">
      <Avatar size="sm">
        {avatarUrl ? <AvatarImage src={avatarUrl} alt={display} /> : null}
        <AvatarFallback>{display.slice(0, 1).toUpperCase()}</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-col">
        {userId ? (
          <a
            href={`/members#u-${userId}`}
            className="text-foreground truncate text-sm font-medium hover:underline"
            title={email || display}
          >
            {display}
          </a>
        ) : (
          <span className="text-foreground truncate text-sm font-medium" title={email || display}>
            {display}
          </span>
        )}
        {email ? <span className="text-muted-foreground truncate text-xs">{email}</span> : null}
      </div>
    </div>
  );
}

interface AnonymizeUserDialogProps {
  target: AstroliftMember | null;
  onOpenChange: (open: boolean) => void;
  acknowledged: boolean;
  onAcknowledgedChange: (next: boolean) => void;
  backendReady: boolean;
  /** Resolves true on success; the hook toasts the reason on failure. */
  onConfirm: () => Promise<boolean>;
}

/**
 * Right-to-delete (GDPR Art. 17) anonymization flow. Double-confirm:
 * the operator must (a) check the "I understand this is irreversible"
 * box before the destructive action button enables, and (b) click that
 * button. The dialog stays open while the mutation is in flight; on
 * error the hook's toast surfaces the message so the operator can retry.
 *
 * The flow is laid out as an explicit list of what gets scrubbed and
 * what stays so the operator can verify the blast radius before
 * acting — `core/anonymization.py` is the source of truth for the
 * field set.
 */
function AnonymizeUserDialog({
  target,
  onOpenChange,
  acknowledged,
  onAcknowledgedChange,
  backendReady,
  onConfirm,
}: AnonymizeUserDialogProps) {
  const [pending, setPending] = React.useState(false);

  async function handleConfirm(e: React.MouseEvent) {
    e.preventDefault();
    if (pending || !acknowledged || !backendReady) return;
    setPending(true);
    try {
      if (await onConfirm()) onOpenChange(false);
    } finally {
      setPending(false);
    }
  }

  const fullName = target?.user.username ?? "";
  const open = target !== null;

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (pending && !next) return;
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Anonymize {fullName}?</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-4">
              {!backendReady && (
                <div className="border-warning-border bg-warning/10 text-warning-fg flex items-start gap-2 rounded-md border p-3 text-xs">
                  <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
                  <div>
                    <p className="font-medium">Backend wiring pending</p>
                    <p className="mt-0.5">
                      The Anonymize button is disabled until the
                      <code className="bg-warning/10 mx-1 rounded px-1 font-mono">
                        anonymizeUser
                      </code>
                      mutation lands. The flow, copy, and double-confirm below are reviewable;
                      tracking under{" "}
                      <a
                        href="https://github.com/calliopeai/astrolift-app/issues/312"
                        target="_blank"
                        rel="noreferrer"
                        className="underline"
                      >
                        #312
                      </a>
                      .
                    </p>
                  </div>
                </div>
              )}

              <div>
                <p className="text-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
                  This will scrub
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  <li>
                    <code className="bg-muted rounded px-1 font-mono">email</code> → SHA-256 hash,
                    not reversible
                  </li>
                  <li>
                    First and last name →{" "}
                    <code className="bg-muted rounded px-1 font-mono">[redacted]</code>
                  </li>
                  <li>Username → deterministic placeholder bound to the user ID</li>
                  <li>Phone, avatar URL → removed</li>
                  <li>Past audit-event payloads → IP, user-agent, email scrubbed in place</li>
                </ul>
              </div>

              <div>
                <p className="text-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
                  This preserves
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  <li>
                    Audit-log structural records (timestamps, action types, affected resources)
                  </li>
                  <li>FK relationships from past actions — referential integrity stays intact</li>
                  <li>
                    Member <code className="bg-muted rounded px-1 font-mono">lifecycle</code> flips
                    to <code className="bg-muted rounded px-1 font-mono">anonymized</code>; role
                    bindings are revoked
                  </li>
                </ul>
              </div>

              <p className="text-destructive font-medium">
                <strong>This action is irreversible.</strong> Re-running it on the same user is a
                no-op; the original PII cannot be restored.
              </p>

              <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4"
                  checked={acknowledged}
                  onChange={(e) => onAcknowledgedChange(e.target.checked)}
                  disabled={pending}
                />
                <span>I understand this is irreversible.</span>
              </label>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={pending || !acknowledged || !backendReady}
            onClick={handleConfirm}
          >
            {pending ? "Anonymizing…" : "Anonymize user data"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
