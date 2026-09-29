"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_TEAMS, ORG_MEMBERS_FOR_APPROVAL_PICKER } from "@/graphql/identity/identity.queries";
import type { AstroliftApproverUser, AstroliftTeam } from "@/graphql/identity/identity.types";

interface ApproverPickerResponse {
  astroliftOrgMembersForApprovalPicker: AstroliftApproverUser[];
}

interface TeamsResponse {
  astroliftTeams: AstroliftTeam[];
}

export type ApproverTeamOption = Pick<AstroliftTeam, "id" | "slug" | "name">;

export interface ApproverSelectorData {
  /** Active org members eligible to approve. */
  allUsers: AstroliftApproverUser[];
  /** Teams in the same org as the approver-user picker. */
  orgTeams: ApproverTeamOption[];
  usersLoading: boolean;
  usersError: string | null;
}

/**
 * Data for the approval-gate picker (#410): the org's approver-eligible
 * members and the org's teams.
 */
export function useApproverSelector(orgSlug: string): ApproverSelectorData {
  const usersQuery = useQuery<ApproverPickerResponse>(ORG_MEMBERS_FOR_APPROVAL_PICKER, {
    variables: { orgSlug },
    skip: !orgSlug,
    fetchPolicy: "cache-and-network",
  });
  const teamsQuery = useQuery<TeamsResponse>(LIST_TEAMS, {
    fetchPolicy: "cache-and-network",
  });

  const allUsers = React.useMemo(
    () => usersQuery.data?.astroliftOrgMembersForApprovalPicker ?? [],
    [usersQuery.data]
  );
  // The teams query is install-wide; narrow to the same org as the
  // approver-user picker to keep the policy coherent.
  const orgTeams = React.useMemo(
    () => (teamsQuery.data?.astroliftTeams ?? []).filter((t) => t.organization?.slug === orgSlug),
    [teamsQuery.data, orgSlug]
  );

  return {
    allUsers,
    orgTeams,
    usersLoading: usersQuery.loading && allUsers.length === 0,
    usersError: usersQuery.error?.message ?? null,
  };
}
