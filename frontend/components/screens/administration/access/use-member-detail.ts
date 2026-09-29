"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_MEMBERS } from "@/graphql/identity/identity.queries";
import type { AstroliftMember } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface MembersResp {
  astroliftMembers: AstroliftMember[];
}

/**
 * The person a People row opened (#1106): the Member row `id`, and every
 * other Member row of the same user (their ORG, TEAM and APP rows), which
 * the Teams tab reads. There is no singular member query, so this reads
 * LIST_MEMBERS, shared by every tab of the page from the cache. The tabs'
 * own data comes from their own hooks.
 */
export function useMemberDetail(id: string) {
  const perms = useMyPermissions();
  const { data, loading, error, refetch } = useQuery<MembersResp>(LIST_MEMBERS, {
    variables: {},
    fetchPolicy: "cache-and-network",
  });

  const member = React.useMemo(
    () => (data?.astroliftMembers ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );
  const memberships = React.useMemo(
    () =>
      member ? (data?.astroliftMembers ?? []).filter((row) => row.user.id === member.user.id) : [],
    [data, member]
  );

  return {
    id,
    member,
    memberships,
    canManage: perms.can("org.manage_members"),
    loading: loading && !data,
    error: error && !data ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
  };
}
