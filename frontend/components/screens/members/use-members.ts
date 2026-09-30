"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { buildCsv } from "@/components/list/exportCsv";
import { useCsvExport } from "@/components/list/use-csv-export";
import { useListState } from "@/components/list/use-list-state";
import { PRINCIPAL_SEARCH } from "@/graphql/access/access.queries";
import {
  ANONYMIZE_USER,
  DELETE_INVITATION,
  RESEND_INVITATION,
  REVOKE_INVITATION,
} from "@/graphql/identity/identity.mutations";
import {
  EXPORT_MEMBERS_CSV,
  EXPORT_INVITATIONS_CSV,
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
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import {
  type GroupPrincipal,
  groupRow,
  groupsVariables,
  invitationRow,
  invitationsVariables,
  membersVariables,
  PEOPLE_LIST,
  type PeopleRow,
  peopleList,
  sourceOf,
  userRows,
} from "./people-model";
import { PEOPLE_CSV } from "./people-csv";

type Page<T> = CursorPage<T> & { page?: number | null; pageSize?: number | null };

interface MembersResp {
  astroliftMembersPage: Page<AstroliftMember>;
}
interface InvitationsResp {
  astroliftInvitationsPage: Page<AstroliftInvitation>;
}
interface GroupsResp {
  astroliftPrincipalSearch: Page<GroupPrincipal>;
}
interface BindingsResp {
  astroliftRoleBindingsPage: Page<AstroliftRoleBinding>;
}

/** IdP group search has a separate numbered contract. */
const EXPORT_PAGE = 200;

/**
 * The data half of the People list (access UX design 3.1): the list state
 * (URL), the one query its view needs (members, IdP groups or
 * invitations; see people-model.ts), the roles of the people on the page,
 * and the invitation and anonymize mutations. The server filters, sorts,
 * counts and numbers the pages. Grants and revokes are on the Grant access
 * page and the principal pages' Access tab.
 */
export function useMembers() {
  const client = useApolloClient();
  const perms = useMyPermissions();
  const canManageMembers = perms.can("org.manage_members");

  const list = useListState(PEOPLE_LIST);
  const question = {
    q: list.state.q,
    filters: list.filters,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  };
  const source = sourceOf(list.filters);
  const common = { fetchPolicy: "cache-and-network" as const };

  const members = useQuery<MembersResp>(LIST_MEMBERS_PAGE, {
    ...common,
    variables: membersVariables(question),
    skip: source !== "members",
  });
  const groups = useQuery<GroupsResp>(PRINCIPAL_SEARCH, {
    ...common,
    variables: groupsVariables(question),
    skip: source !== "groups",
  });
  const invitations = useQuery<InvitationsResp>(LIST_INVITATIONS_PAGE, {
    ...common,
    variables: invitationsVariables(question),
    skip: source !== "invitations",
  });
  const roles = useQuery<{ astroliftRoles: AstroliftRole[] }>(LIST_ROLES);
  const roleList = React.useMemo(() => roles.data?.astroliftRoles ?? [], [roles.data]);

  const active = { members, groups, invitations }[source];
  const data = active.data ?? active.previousData;
  const memberPage = (members.data ?? members.previousData)?.astroliftMembersPage;
  const memberItems = React.useMemo(
    () => (source === "members" ? (memberPage?.items ?? []) : []),
    [source, memberPage]
  );

  // The roles column: every binding of the people on this page, in one read.
  const usernames = memberItems.map((m) => m.user.username);
  const bindings = useQuery<BindingsResp>(LIST_ROLE_BINDINGS_PAGE, {
    ...common,
    variables: {
      search: null,
      filter: { holder: usernames, kind: ["user"] },
      sort: "name",
      page: 1,
      pageSize: 200,
    },
    skip: usernames.length === 0,
  });
  const bindingRows = React.useMemo(
    () => (bindings.data ?? bindings.previousData)?.astroliftRoleBindingsPage.items ?? [],
    [bindings.data, bindings.previousData]
  );

  const rows = React.useMemo<PeopleRow[]>(() => {
    if (source === "members") return userRows(memberItems, bindingRows, roleList);
    if (source === "groups")
      return ((data as GroupsResp | undefined)?.astroliftPrincipalSearch.items ?? []).map(groupRow);
    return ((data as InvitationsResp | undefined)?.astroliftInvitationsPage.items ?? []).map(
      invitationRow
    );
  }, [source, memberItems, bindingRows, roleList, data]);

  const totalCount =
    source === "members"
      ? memberPage?.totalCount
      : source === "groups"
        ? (data as GroupsResp | undefined)?.astroliftPrincipalSearch.totalCount
        : (data as InvitationsResp | undefined)?.astroliftInvitationsPage.totalCount;

  const { exportingCsv, onExportCsv } = useCsvExport(async () => {
    if (source !== "groups") {
      const query = source === "members" ? EXPORT_MEMBERS_CSV : EXPORT_INVITATIONS_CSV;
      const {
        page: _page,
        pageSize: _pageSize,
        ...variables
      } = source === "members" ? membersVariables(question) : invitationsVariables(question);
      const result = await client.query({ query, variables, fetchPolicy: "network-only" });
      const data = result.data as
        | {
            astroliftMembersCsv?: { filename: string; content: string };
            astroliftInvitationsCsv?: { filename: string; content: string };
          }
        | undefined;
      const csv = source === "members" ? data?.astroliftMembersCsv : data?.astroliftInvitationsCsv;
      if (!csv) throw new Error("The export returned no file");
      return csv;
    }
    const out: PeopleRow[] = [];
    for (let page = 1; ; page++) {
      const result = await client.query<GroupsResp>({
        query: PRINCIPAL_SEARCH,
        variables: { ...groupsVariables(question), page, pageSize: EXPORT_PAGE },
        fetchPolicy: "network-only",
      });
      const batch = result.data?.astroliftPrincipalSearch;
      if (!batch) throw new Error("The export returned no groups");
      out.push(...batch.items.map(groupRow));
      if (
        batch.totalCount != null ? out.length >= batch.totalCount : batch.items.length < EXPORT_PAGE
      )
        break;
      if (!batch.items.length)
        throw new Error("The group list changed during export; retry the export");
    }
    return { filename: "people.csv", content: buildCsv(out, PEOPLE_CSV) };
  });

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
    list: { ...list, definition: peopleList(roleList) },
    rows,
    totalCount: totalCount ?? rows.length,
    loading: active.loading && !data,
    stale: active.loading && !active.data && Boolean(data),
    error: active.error ? { message: active.error.message } : null,
    onRetry: () => {
      void active.refetch();
    },
    exportingCsv,
    onExportCsv,
    revokingInvite,
    deletingInvite,
    resendingInvite,
    onRevokeInvite,
    onResendInvite,
    onDeleteInvite,
    onAnonymize,
  };
}
