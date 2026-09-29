"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import {
  ANONYMIZE_USER,
  DELETE_INVITATION,
  RESEND_INVITATION,
  REVOKE_INVITATION,
} from "@/graphql/identity/identity.mutations";
import {
  LIST_INVITATIONS_PAGE,
  LIST_MEMBERS_PAGE,
  LIST_ROLE_BINDINGS_PAGE,
  LIST_ROLES,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftInvitation,
  AstroliftMember,
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { GET_ME } from "@/graphql/user/user.queries";
import type { MeQueryData } from "@/graphql/user/user.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import {
  buildPeopleRows,
  type InvitationRow,
  PEOPLE_LIST,
  type PeopleRow,
  peopleList,
  selectPeople,
  viewNeeds,
} from "./people-model";
import { useWalk } from "./use-walk";

/**
 * The data half of the People list (access UX design 3.1): the list state
 * (URL), the walks its view needs, and the invitation and anonymize
 * mutations. Only the active view's walks run: All, Mine and Admins read
 * members and bindings; Groups reads bindings; Invited reads invitations.
 * Grants and revokes moved to the Grant access page and the principal
 * pages' Access tab.
 */
export function useMembers() {
  const perms = useMyPermissions();
  const canManageMembers = perms.can("org.manage_members");

  const list = useListState(PEOPLE_LIST);
  const { state } = list;
  const search = state.q.trim() || null;
  const needs = viewNeeds(list.filters);
  const [now] = React.useState(() => Date.now());

  const members = useWalk<AstroliftMember>(
    LIST_MEMBERS_PAGE,
    (d) =>
      (d as { astroliftMembersPage?: CursorPage<AstroliftMember> } | undefined)
        ?.astroliftMembersPage,
    { variables: { search }, skip: !needs.members }
  );
  // Unsearched: a user's roles are all of their bindings, whatever the search.
  const bindings = useWalk<AstroliftRoleBinding>(
    LIST_ROLE_BINDINGS_PAGE,
    (d) =>
      (d as { astroliftRoleBindingsPage?: CursorPage<AstroliftRoleBinding> } | undefined)
        ?.astroliftRoleBindingsPage,
    { skip: !needs.bindings }
  );
  // Every status: the Invited view is `status:pending`, a chip shows the history.
  const invitations = useWalk<AstroliftInvitation>(
    LIST_INVITATIONS_PAGE,
    (d) =>
      (d as { astroliftInvitationsPage?: CursorPage<AstroliftInvitation> } | undefined)
        ?.astroliftInvitationsPage,
    { variables: { search, status: null }, skip: !needs.invitations }
  );
  const roles = useQuery<{ astroliftRoles: AstroliftRole[] }>(LIST_ROLES);
  const me = useQuery<MeQueryData>(GET_ME).data?.me?.profile?.username ?? null;

  const roleList = React.useMemo(() => roles.data?.astroliftRoles ?? [], [roles.data]);
  const people = React.useMemo(
    () => buildPeopleRows({ members: members.rows, bindings: bindings.rows, roles: roleList }),
    [members.rows, bindings.rows, roleList]
  );
  const allRows = React.useMemo<PeopleRow[]>(
    () => [
      ...people.users,
      ...people.groups,
      ...invitations.rows.map(
        (invitation): InvitationRow => ({
          kind: "invitation",
          key: `invitation:${invitation.id}`,
          invitation,
        })
      ),
    ],
    [people, invitations.rows]
  );

  const selected = selectPeople(allRows, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
    me,
    now,
  });

  const teamSlugs = [
    ...new Set(people.users.flatMap((u) => u.teams.map((t) => t.slug)).filter(Boolean)),
  ] as string[];
  const withOptions = { ...list, definition: peopleList(roleList, teamSlugs.sort()) };

  const walks = [
    needs.members && members,
    needs.bindings && bindings,
    needs.invitations && invitations,
  ].filter((w): w is typeof members => Boolean(w));
  const failed = walks.find((w) => w.error);

  const [revokeInvite, { loading: revokingInvite }] = useMutation<{
    revokeInvitation: MutationResult<AstroliftInvitation>;
  }>(REVOKE_INVITATION, { refetchQueries: ["ListInvitationsPage"], awaitRefetchQueries: true });
  const [deleteInvite, { loading: deletingInvite }] = useMutation<{
    deleteInvitation: MutationResult<AstroliftInvitation>;
  }>(DELETE_INVITATION, { refetchQueries: ["ListInvitationsPage"], awaitRefetchQueries: true });
  const [resendInvite, { loading: resendingInvite }] = useMutation<{
    resendInvitation: MutationResult<{
      invitation: AstroliftInvitation;
      plaintextToken: string;
      acceptUrlPath: string;
    }>;
  }>(RESEND_INVITATION, { refetchQueries: ["ListInvitationsPage"], awaitRefetchQueries: true });
  const [anonymizeUser] = useMutation<{
    astroliftAnonymizeUser: MutationResult<{ anonymizedUserId: string }>;
  }>(ANONYMIZE_USER, { refetchQueries: ["ListMembersPage"], awaitRefetchQueries: true });

  /** Throws on failure, so the confirm dialog stays open and shows why. */
  async function onRevokeInvite(inv: AstroliftInvitation): Promise<void> {
    const { data } = await revokeInvite({ variables: { input: { id: inv.id } } });
    if (data?.revokeInvitation.ok) toast.success("Invitation revoked");
    else throw new Error(data?.revokeInvitation.errors?.[0]?.message ?? "Revoke failed");
  }

  /** Throws on failure, so the confirm dialog stays open and shows why. */
  async function onResendInvite(inv: AstroliftInvitation): Promise<void> {
    const { data } = await resendInvite({ variables: { input: { id: inv.id } } });
    const result = data?.resendInvitation;
    if (!result?.ok || !result.data) {
      throw new Error(result?.errors?.[0]?.message ?? "Resend failed");
    }
    // The token was rotated, so any link already handed out is dead. A fresh
    // one was emailed (best effort); Copy link keeps the durable hand-off.
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
    const { data } = await deleteInvite({ variables: { input: { id: inv.id } } });
    if (data?.deleteInvitation.ok) toast.success("Invitation deleted");
    else throw new Error(data?.deleteInvitation.errors?.[0]?.message ?? "Delete failed");
  }

  /** Right to delete (GDPR): resolves true when the user was anonymized; toasts why not. */
  async function onAnonymize(userId: string): Promise<boolean> {
    try {
      const { data } = await anonymizeUser({ variables: { input: { userGid: userId } } });
      if (!data?.astroliftAnonymizeUser.ok) {
        throw new Error(data?.astroliftAnonymizeUser.errors?.[0]?.message ?? "Anonymize failed");
      }
      toast.success("User data anonymized.");
      return true;
    } catch (err) {
      toast.error(err instanceof Error && err.message ? err.message : "Anonymize failed");
      return false;
    }
  }

  return {
    canManageMembers,
    list: withOptions,
    rows: selected.rows,
    totalCount: selected.totalCount,
    filtered: selected.filtered,
    loading: walks.some((w) => w.loading),
    stale: walks.some((w) => w.stale),
    error: failed?.error ?? null,
    truncated: walks.some((w) => w.truncated),
    onRetry: () => walks.forEach((w) => w.refetch()),
    revokingInvite,
    deletingInvite,
    resendingInvite,
    onRevokeInvite,
    onResendInvite,
    onDeleteInvite,
    onAnonymize,
  };
}
