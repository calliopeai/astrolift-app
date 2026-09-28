"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { useCursorTable, useRowSelection, type CursorPage } from "@/components/data-table";
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
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

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
 * The data half of MembersScreen: the People, Role bindings and
 * Invitations tables, the lookups their cells resolve against, and every
 * revoke / resend / delete / anonymize mutation.
 */
export function useMembers() {
  // Bulk-revoke (#416) lives in its own i18n namespace so the team-
  // member bulk surface can reuse a sibling key set without
  // overloading orgMembers.
  const tBulk = useTranslations("lists.membersBulk");
  const perms = useMyPermissions();
  const canManageMembers = perms.can("org.manage_members");

  // Resolved (revoked/accepted/expired) invitations are hidden by
  // default and deletable — pending ones keep the resend/revoke pair.
  // `null` means "every status"; the filter is a query variable, so the
  // server does the narrowing and the cursor walk resets when it flips.
  const [inviteStatus, setInviteStatus] = React.useState<InvitationStatus | null>("pending");

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

  /** Throws on failure, so the confirm dialog stays open and shows why. */
  async function onRevokeInvite(inv: AstroliftInvitation): Promise<void> {
    const { data } = await revokeInvite({
      variables: { input: { id: inv.id } },
    });
    if (data?.revokeInvitation.ok) {
      toast.success("Invitation revoked");
    } else {
      throw new Error(data?.revokeInvitation.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  /** Throws on failure, so the confirm dialog stays open and shows why. */
  async function onResendInvite(inv: AstroliftInvitation): Promise<void> {
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

  /** Throws on failure, so the confirm dialog stays open and shows why. */
  async function onDeleteInvite(inv: AstroliftInvitation): Promise<void> {
    const { data } = await deleteInvite({
      variables: { input: { id: inv.id } },
    });
    if (data?.deleteInvitation.ok) {
      toast.success("Invitation deleted");
    } else {
      throw new Error(data?.deleteInvitation.errors?.[0]?.message ?? "Delete failed");
    }
  }

  /** Throws on failure, so the confirm dialog stays open and shows why. */
  async function onRevokeBinding(rb: AstroliftRoleBinding): Promise<void> {
    const { data } = await revokeBinding({ variables: { input: { id: rb.id } } });
    if (data?.revokeRoleBinding.ok) {
      toast.success("Role revoked");
    } else {
      throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  /** Revokes the selected bindings; the view closes its confirm when this settles. */
  async function onBulkRevoke(): Promise<void> {
    const ids = bindingSelection.selectedIds;
    if (ids.length === 0) return;
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
  }

  // Right-to-delete (GDPR) — anonymize a user's PII while preserving
  // audit-log structural records.
  /** Resolves true when the user was anonymized; toasts the reason when not. */
  async function onAnonymize(m: AstroliftMember): Promise<boolean> {
    try {
      const { data } = await anonymizeUser({
        variables: { input: { userGid: m.user.id } },
      });
      if (!data?.astroliftAnonymizeUser.ok) {
        throw new Error(data?.astroliftAnonymizeUser.errors?.[0]?.message ?? "Anonymize failed");
      }
      toast.success("User data anonymized.");
      return true;
    } catch (err) {
      const message = err instanceof Error && err.message ? err.message : "Anonymize failed";
      toast.error(message);
      return false;
    }
  }

  return {
    canManageMembers,
    membersTable,
    bindingsTable,
    bindingSelection,
    invitationsTable,
    inviteStatus,
    setInviteStatus,
    bindingIndexRows: bindingIndex.data?.astroliftRoleBindingsPage.items,
    teams: teams.data?.astroliftTeams,
    projects: projects.data?.astroliftProjects,
    roles: roles.data?.astroliftRoles,
    rolesLoading: roles.loading,
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
  };
}
