"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  InfoIcon,
  MailIcon,
  SendIcon,
  ShieldIcon,
  Trash2Icon,
  UserMinusIcon,
  UserPlusIcon,
  UsersIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import {
  DataTable,
  useCursorTable,
  useRowSelection,
  type Column,
  type CursorPage,
  type CursorTableController,
} from "@/components/data-table";
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
import { Section } from "@/components/ui/section";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  ANONYMIZE_USER,
  BULK_REVOKE_ROLE_BINDINGS,
  DELETE_INVITATION,
  RESEND_INVITATION,
  REVOKE_INVITATION,
  REVOKE_ROLE_BINDING,
} from "@/graphql/identity/identity.mutations";
import {
  LIST_INVITATIONS_PAGE,
  LIST_MEMBERS_PAGE,
  LIST_PROJECTS,
  LIST_ROLE_BINDINGS_PAGE,
  LIST_ROLES,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftBulkRevokeRoleBindingsPayload,
  AstroliftInvitation,
  AstroliftMember,
  AstroliftProject,
  AstroliftRole,
  AstroliftRoleBinding,
  AstroliftTeam,
  InvitationStatus,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { GrantRoleDialog } from "./grant-role-dialog";
import { InvitationExpiryBadge } from "./invitation-expiry";
import { InviteDialog } from "./invite-dialog";

interface MembersPageResp {
  astroliftMembersPage: CursorPage<AstroliftMember>;
}
interface RoleBindingsPageResp {
  astroliftRoleBindingsPage: CursorPage<AstroliftRoleBinding>;
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}
interface InvitationsPageResp {
  astroliftInvitationsPage: CursorPage<AstroliftInvitation>;
}

const scopeBadge: Record<string, string> = {
  ORG: "bg-info/15 text-info-fg",
  TEAM: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
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
 * The People row's role pills and its APP-scope labels are a *lookup*,
 * not a table: they answer "which roles does this person hold" for the
 * users on the current People page. There is no per-user binding field
 * on ``AstroliftMember``, so the index is one bounded read of the
 * binding list at the server's ``MAX_PAGE_LIMIT``. Bindings older than
 * the 200 most recent grants fall outside it — the Role bindings table
 * below is the complete, paginated, searchable source of truth.
 */
const BINDING_INDEX_LIMIT = 200;

/**
 * Cells that own their own pointer affordances (hover tooltips, action
 * buttons) have to sit above ``rowHref``'s stretched row link, which is
 * an absolutely-positioned overlay across the whole row.
 */
const ABOVE_ROW_LINK = "relative z-10";

export function MembersClient() {
  const t = useTranslations("orgMembers");
  // Bulk-revoke (#416) lives in its own i18n namespace so the team-
  // member bulk surface can reuse a sibling key set without
  // overloading orgMembers.
  const tBulk = useTranslations("lists.membersBulk");
  const fmt = useFormatters();
  const perms = useMyPermissions();
  const canManageMembers = perms.can("org.manage_members");

  const [open, setOpen] = React.useState(false);
  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<RevokeTarget | null>(null);
  // Deep-link grant: when set, the GrantRoleDialog opens pre-populated
  // with this member's user PK so the operator skips the user picker.
  // The dialog calls onOpenChange(false) on submit/cancel, which clears
  // this back to null via the wrapper handler below.
  const [grantForMember, setGrantForMember] = React.useState<AstroliftMember | null>(null);
  // Resolved (revoked/accepted/expired) invitations are hidden by
  // default and deletable — pending ones keep the resend/revoke pair.
  // `null` means "every status"; the filter is a query variable, so the
  // server does the narrowing and the cursor walk resets when it flips.
  const [inviteStatus, setInviteStatus] = React.useState<InvitationStatus | null>("pending");
  const [deleteInviteTarget, setDeleteInviteTarget] = React.useState<AstroliftInvitation | null>(
    null
  );
  // Re-sending rotates the invitation token, which kills any link the
  // operator already handed out — confirmed rather than fired on click.
  const [resendTarget, setResendTarget] = React.useState<AstroliftInvitation | null>(null);
  const [confirmBulkRevoke, setConfirmBulkRevoke] = React.useState(false);

  const membersTable = useCursorTable<AstroliftMember>({
    query: LIST_MEMBERS_PAGE,
    extract: (d) => (d as MembersPageResp | undefined)?.astroliftMembersPage,
    searchVariable: "search",
    urlKey: "ppl",
  });

  const bindingsTable = useCursorTable<AstroliftRoleBinding>({
    query: LIST_ROLE_BINDINGS_PAGE,
    extract: (d) => (d as RoleBindingsPageResp | undefined)?.astroliftRoleBindingsPage,
    searchVariable: "search",
    urlKey: "rb",
  });
  const bindingSelection = useRowSelection();

  const invitationsTable = useCursorTable<AstroliftInvitation>({
    query: LIST_INVITATIONS_PAGE,
    variables: { status: inviteStatus },
    extract: (d) => (d as InvitationsPageResp | undefined)?.astroliftInvitationsPage,
    searchVariable: "search",
    urlKey: "inv",
  });

  const bindingIndex = useQuery<RoleBindingsPageResp>(LIST_ROLE_BINDINGS_PAGE, {
    variables: { limit: BINDING_INDEX_LIMIT },
    fetchPolicy: "cache-and-network",
  });
  const roles = useQuery<RolesResp>(LIST_ROLES);
  // Loaded so the Scope column can resolve `(scopeKind=TEAM, scopeId=N)`
  // into a human-readable team / project name instead of the bare
  // "TEAM" badge that previously made it ambiguous whether the column
  // showed a role or a scope.
  const teams = useQuery<{ astroliftTeams: AstroliftTeam[] }>(LIST_TEAMS);
  const projects = useQuery<{ astroliftProjects: AstroliftProject[] }>(LIST_PROJECTS);

  // Refetch by operation name: both the Role bindings table and the
  // People-row pill index run `ListRoleBindingsPage` under different
  // variables, and a name refetches every active instance of the query
  // rather than one variable set.
  const [revokeBinding, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    refetchQueries: ["ListRoleBindingsPage"],
    awaitRefetchQueries: true,
  });
  const [bulkRevoke, { loading: bulkRevoking }] = useMutation<{
    bulkRevokeAstroliftRoleBindings: MutationResult<AstroliftBulkRevokeRoleBindingsPayload>;
  }>(BULK_REVOKE_ROLE_BINDINGS, {
    refetchQueries: ["ListRoleBindingsPage", "ListMembersPage"],
    awaitRefetchQueries: true,
  });
  const [revokeInvite, { loading: revokingInvite }] = useMutation<{
    revokeInvitation: MutationResult<AstroliftInvitation>;
  }>(REVOKE_INVITATION, {
    refetchQueries: ["ListInvitationsPage"],
    awaitRefetchQueries: true,
  });
  const [deleteInvite, { loading: deletingInvite }] = useMutation<{
    deleteInvitation: MutationResult<AstroliftInvitation>;
  }>(DELETE_INVITATION, {
    refetchQueries: ["ListInvitationsPage"],
    awaitRefetchQueries: true,
  });
  const [resendInvite, { loading: resendingInvite }] = useMutation<{
    resendInvitation: MutationResult<{
      invitation: AstroliftInvitation;
      plaintextToken: string;
      acceptUrlPath: string;
    }>;
  }>(RESEND_INVITATION, {
    refetchQueries: ["ListInvitationsPage"],
    awaitRefetchQueries: true,
  });
  const [anonymizeUser] = useMutation<{
    astroliftAnonymizeUser: MutationResult<{
      anonymizedUserId: string;
      wasSelf: boolean;
      requiresLogout: boolean;
      lifecycle: string;
      anonymizedAt: string;
    }>;
  }>(ANONYMIZE_USER, {
    refetchQueries: ["ListMembersPage"],
    awaitRefetchQueries: true,
  });

  async function handleRevokeInvite(inv: AstroliftInvitation) {
    const { data } = await revokeInvite({
      variables: { input: { id: inv.id } },
    });
    if (data?.revokeInvitation.ok) {
      toast.success("Invitation revoked");
    } else {
      throw new Error(data?.revokeInvitation.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  async function handleResendInvite(inv: AstroliftInvitation) {
    const { data } = await resendInvite({
      variables: { input: { id: inv.id } },
    });
    const result = data?.resendInvitation;
    if (!result?.ok || !result.data) {
      throw new Error(result?.errors?.[0]?.message ?? "Resend failed");
    }
    // The token was rotated, so any previously-issued link is now
    // dead. A fresh link was emailed (best-effort); surface a
    // Copy-link action so the operator always retains the durable
    // hand-off channel.
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    const url = `${origin}${result.data.acceptUrlPath}`;
    toast.success(`Invitation re-sent to ${inv.email}`, {
      description: "The previous link is now invalid. Copy the fresh link as a backup channel.",
      action: {
        label: "Copy link",
        onClick: () => {
          navigator.clipboard
            .writeText(url)
            .then(() => toast.success("Accept link copied"))
            .catch(() => toast.error("Copy failed"));
        },
      },
    });
  }

  async function handleRevoke(rb: AstroliftRoleBinding) {
    const { data } = await revokeBinding({ variables: { input: { id: rb.id } } });
    if (data?.revokeRoleBinding.ok) {
      toast.success("Role revoked");
    } else {
      throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  async function handleBulkRevoke() {
    const ids = bindingSelection.selectedIds;
    if (ids.length === 0) return;
    try {
      const { data } = await bulkRevoke({
        variables: { input: { bindingIds: ids } },
      });
      const env = data?.bulkRevokeAstroliftRoleBindings;
      if (!env?.ok || !env.data) {
        toast.error(
          tBulk("toasts.allFailed", {
            message: env?.errors?.[0]?.message ?? "unknown error",
          })
        );
        return;
      }
      const { revokedCount, failedCount } = env.data;
      if (failedCount === 0) {
        toast.success(tBulk("toasts.allOk", { count: revokedCount }));
      } else {
        toast.warning(
          tBulk("toasts.partial", {
            revoked: revokedCount,
            failed: failedCount,
          })
        );
      }
      bindingSelection.clear();
    } finally {
      setConfirmBulkRevoke(false);
    }
  }

  // Right-to-delete (GDPR) — anonymize a user's PII while preserving
  // audit-log structural records.
  const ANONYMIZE_BACKEND_READY = true;
  const [anonymizeTarget, setAnonymizeTarget] = React.useState<AstroliftMember | null>(null);
  const [anonymizeAcknowledged, setAnonymizeAcknowledged] = React.useState(false);

  function openAnonymizeDialog(m: AstroliftMember) {
    setAnonymizeAcknowledged(false);
    setAnonymizeTarget(m);
  }

  async function handleAnonymize(m: AstroliftMember) {
    const { data } = await anonymizeUser({
      variables: { input: { userGid: m.user.id } },
    });
    if (!data?.astroliftAnonymizeUser.ok) {
      throw new Error(data?.astroliftAnonymizeUser.errors?.[0]?.message ?? "Anonymize failed");
    }
    toast.success("User data anonymized.");
  }

  const bindingIndexRows = React.useMemo(
    () => bindingIndex.data?.astroliftRoleBindingsPage.items ?? [],
    [bindingIndex.data]
  );
  const teamList = teams.data?.astroliftTeams ?? [];
  const projectList = projects.data?.astroliftProjects ?? [];

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
    for (const m of membersTable.rows) {
      const arr = byUser.get(m.user.id) ?? [];
      arr.push(m);
      byUser.set(m.user.id, arr);
    }
    return Array.from(byUser.values()).map((rows) => ({
      rows,
      primary: rows.find((r) => r.scopeKind === "ORG") ?? rows[0],
    }));
  }, [membersTable.rows]);

  // Same controller, grouped rows: paging, search and state all still
  // come from the server-side walk.
  const peopleController: CursorTableController<MemberGroup> = {
    ...membersTable,
    rows: memberGroups,
  };

  const peopleColumns: Column<MemberGroup>[] = [
    {
      id: "user",
      header: "User",
      cell: ({ primary }) => (
        // The id is the anchor InviterCell deep-links to (`#u-<userId>`).
        <div id={`u-${primary.user.id}`}>
          <div className="font-medium">{primary.user.username}</div>
          <div className="text-muted-foreground text-xs">{primary.user.email}</div>
        </div>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cell: ({ rows }) => (
        <div className="flex flex-col items-start gap-1">
          {rows.map((r) => (
            <div key={r.id} className="flex items-center gap-1.5">
              <Badge className={scopeBadge[r.scopeKind]} variant="secondary">
                {r.scopeKind}
              </Badge>
              <span className="text-muted-foreground text-xs">{scopeLabel(r)}</span>
            </div>
          ))}
        </div>
      ),
    },
    {
      id: "roles",
      header: "Roles",
      cellClassName: ABOVE_ROW_LINK,
      cell: ({ primary }) => {
        const userBindings = bindingsByUser.get(primary.user.id) ?? [];
        if (userBindings.length === 0) {
          return <span className="text-muted-foreground text-xs">—</span>;
        }
        return (
          <div className="flex flex-wrap gap-1">
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
      cellClassName: "text-muted-foreground text-sm",
      cell: ({ primary }) =>
        primary.joinedAt ? fmt.formatDate(primary.joinedAt) : fmt.formatDate(primary.createdAt),
    },
    {
      id: "actions",
      header: t("actionsColumn"),
      align: "right",
      width: "w-24",
      cellClassName: ABOVE_ROW_LINK,
      cell: ({ rows, primary }) => {
        const alreadyAnonymized = rows.some((r) => r.lifecycle === "anonymized");
        return (
          <div className="flex items-center justify-end gap-1">
            <Can permission="org.manage_members">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setGrantForMember(primary)}
                    aria-label={t("grantRoleRowLabel", { name: primary.user.username })}
                  >
                    <UserPlusIcon className="size-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {t("grantRoleRowTooltip", { name: primary.user.username })}
                </TooltipContent>
              </Tooltip>
            </Can>
            <Can permission="org.manage_members">
              <Button
                size="sm"
                variant="ghost"
                disabled={alreadyAnonymized}
                onClick={() => openAnonymizeDialog(primary)}
                aria-label={`Anonymize ${primary.user.username}`}
                title={
                  alreadyAnonymized
                    ? "Already anonymized"
                    : "Anonymize user data (GDPR right-to-delete)"
                }
              >
                <UserMinusIcon className="size-4" />
              </Button>
            </Can>
          </div>
        );
      },
    },
  ];

  const bindingColumns: Column<AstroliftRoleBinding>[] = [
    {
      id: "subject",
      header: "Subject",
      cell: (b) =>
        b.user ? (
          <>
            <div className="font-medium">{b.user.username}</div>
            <div className="text-muted-foreground text-xs">{b.user.email}</div>
          </>
        ) : (
          <div className="font-mono text-xs">group:{b.groupExternalId}</div>
        ),
    },
    {
      id: "role",
      header: "Role",
      cell: (b) => (
        <>
          <div className="font-medium">{b.role.name}</div>
          <div className="text-muted-foreground font-mono text-xs">{b.role.slug}</div>
        </>
      ),
    },
    {
      id: "source",
      header: t("sourceColumn"),
      cell: (b) => (
        <div className="flex items-center gap-1.5">
          <Badge className={scopeBadge[b.scopeKind]} variant="secondary">
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
      cellClassName: "text-muted-foreground text-sm",
      cell: (b) => fmt.formatDate(b.grantedAt),
    },
    {
      id: "actions",
      header: t("actionsColumn"),
      align: "right",
      width: "w-16",
      cell: (b) => (
        <Can permission="org.manage_members">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setRevokeTarget({ kind: "binding", binding: b })}
            disabled={revoking || bulkRevoking}
          >
            <Trash2Icon className="size-4" />
            <span className="sr-only">Revoke</span>
          </Button>
        </Can>
      ),
    },
  ];

  const invitationColumns: Column<AstroliftInvitation>[] = [
    {
      id: "email",
      header: "Email",
      cellClassName: "font-medium",
      cell: (inv) => inv.email,
    },
    {
      id: "role",
      header: "Role",
      cell: (inv) =>
        inv.roleSlug ? (
          <Badge variant="outline" className="font-mono text-xs">
            {inv.roleSlug}
          </Badge>
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        ),
    },
    {
      id: "status",
      header: "Status",
      cell: (inv) => (
        <Badge variant={inv.status === "pending" ? "default" : "secondary"} className="capitalize">
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
          <span className="text-muted-foreground text-sm">{fmt.formatDate(inv.expiresAt)}</span>
        ),
    },
    {
      id: "invitedBy",
      header: "Invited by",
      cell: (inv) => <InviterCell invitation={inv} />,
    },
    {
      id: "actions",
      header: <span className="sr-only">{t("actionsColumn")}</span>,
      align: "right",
      width: "w-24",
      cell: (inv) =>
        inv.status === "pending" ? (
          <Can permission="org.manage_members">
            <div className="flex items-center justify-end gap-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setResendTarget(inv)}
                    disabled={resendingInvite || revokingInvite}
                    aria-label={`Resend invitation to ${inv.email}`}
                  >
                    <SendIcon className="size-4" />
                    <span className="sr-only">Resend</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Resend invitation</TooltipContent>
              </Tooltip>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setRevokeTarget({ kind: "invitation", invitation: inv })}
                disabled={revokingInvite}
              >
                <Trash2Icon className="size-4" />
                <span className="sr-only">Revoke</span>
              </Button>
            </div>
          </Can>
        ) : (
          <Can permission="org.manage_members">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setDeleteInviteTarget(inv)}
                  disabled={deletingInvite}
                  aria-label={`Delete resolved invitation for ${inv.email}`}
                >
                  <Trash2Icon className="size-4" />
                  <span className="sr-only">Delete</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Delete this resolved invitation</TooltipContent>
            </Tooltip>
          </Can>
        ),
    },
  ];

  const showingPendingOnly = inviteStatus === "pending";

  return (
    <TooltipProvider>
      <PageShell
        title="Members"
        description="Users with access to this organization, plus the role bindings that grant their permissions."
        actions={
          <div className="flex items-center gap-2">
            <Can permission="org.manage_members">
              <Button variant="outline" onClick={() => setInviteOpen(true)}>
                <MailIcon className="size-4" />
                Invite
              </Button>
            </Can>
            <Can permission="org.manage_members">
              <Button onClick={() => setOpen(true)} disabled={roles.loading}>
                <UserPlusIcon className="size-4" />
                Grant role
              </Button>
            </Can>
          </div>
        }
      >
        <Section title="People">
          <DataTable
            label="People"
            controller={peopleController}
            columns={peopleColumns}
            getRowId={(g) => g.primary.user.id}
            rowHref={(g) => `/administration/members/${g.primary.id}`}
            searchPlaceholder={t("searchPlaceholder")}
            empty={{
              icon: <UsersIcon className="size-5" />,
              title: "No members",
              description: "Members appear here once role bindings are granted to users.",
            }}
            emptyFiltered={{
              title: t("noMatchTitle"),
              description: t("noMatchDescription", { term: membersTable.search.trim() }),
            }}
          />
        </Section>

        <Section title="Role bindings">
          <DataTable
            label="Role bindings"
            controller={bindingsTable}
            columns={bindingColumns}
            getRowId={(b) => b.id}
            selection={canManageMembers ? bindingSelection : undefined}
            bulkActions={(selection) => (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setConfirmBulkRevoke(true)}
                disabled={bulkRevoking}
              >
                <Trash2Icon className="size-4" />
                {tBulk("revokeButton", { count: selection.selectedCount })}
              </Button>
            )}
            searchPlaceholder="Search by user, group, or role…"
            empty={{
              icon: <ShieldIcon className="size-5" />,
              title: "No role bindings",
              description: "Grant a system role to a user to give them access to the platform.",
            }}
            emptyFiltered={{
              title: "No matching role bindings",
              description:
                "No binding matches this search. Try a username, an SSO group, or a role slug.",
            }}
          />
        </Section>

        <Section title="Invitations">
          <DataTable
            label="Invitations"
            controller={invitationsTable}
            columns={invitationColumns}
            getRowId={(inv) => inv.id}
            toolbar={
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setInviteStatus(showingPendingOnly ? null : "pending")}
              >
                {showingPendingOnly ? "Show resolved" : "Hide resolved"}
              </Button>
            }
            searchPlaceholder="Search by email, role, or inviter…"
            empty={{
              icon: <MailIcon className="size-5" />,
              title: showingPendingOnly ? "No pending invitations" : "No invitations",
              description: showingPendingOnly
                ? "Resolved invitations are hidden — use Show resolved to review or delete them."
                : "Use Invite to send a one-time accept link. Tokens are hashed at rest; the plaintext is shown once at creation.",
            }}
            emptyFiltered={{
              title: "No matching invitations",
              description: "No invitation matches this search under the current status filter.",
            }}
          />
        </Section>

        <ConfirmDialog
          open={confirmBulkRevoke}
          onOpenChange={setConfirmBulkRevoke}
          title={tBulk("confirm.title", { count: bindingSelection.selectedCount })}
          description={tBulk("confirm.description")}
          confirmLabel={tBulk("confirm.confirmLabel", { count: bindingSelection.selectedCount })}
          destructive
          onConfirm={handleBulkRevoke}
        />

        <GrantRoleDialog
          open={open || grantForMember !== null}
          onOpenChange={(next) => {
            setOpen(next);
            if (!next) setGrantForMember(null);
          }}
          roles={roles.data?.astroliftRoles ?? []}
          initialUserId={grantForMember?.user.id ?? null}
          initialUserLabel={grantForMember?.user.username ?? null}
        />
        <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />

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
            await handleResendInvite(resendTarget);
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
            const { data } = await deleteInvite({
              variables: { input: { id: deleteInviteTarget.id } },
            });
            if (data?.deleteInvitation.ok) {
              toast.success("Invitation deleted");
              setDeleteInviteTarget(null);
            } else {
              throw new Error(data?.deleteInvitation.errors?.[0]?.message ?? "Delete failed");
            }
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
              await handleRevokeInvite(revokeTarget.invitation);
            } else {
              await handleRevoke(revokeTarget.binding);
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
          onConfirm={async () => {
            if (anonymizeTarget) await handleAnonymize(anonymizeTarget);
          }}
        />
      </PageShell>
    </TooltipProvider>
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
  onConfirm: () => Promise<void>;
}

/**
 * Right-to-delete (GDPR Art. 17) anonymization flow. Double-confirm:
 * the operator must (a) check the "I understand this is irreversible"
 * box before the destructive action button enables, and (b) click that
 * button. The dialog stays open while the mutation is in flight; on
 * error sonner surfaces the message so the operator can retry.
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
      await onConfirm();
      onOpenChange(false);
    } catch (err) {
      const message = err instanceof Error && err.message ? err.message : "Anonymize failed";
      toast.error(message);
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
