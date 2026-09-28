"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_MEMBERS, LIST_ROLE_BINDINGS } from "@/graphql/identity/identity.queries";
import type { AstroliftMember, AstroliftRoleBinding } from "@/graphql/identity/identity.types";

interface MembersResp {
  astroliftMembers: AstroliftMember[];
}
interface RoleBindingsResp {
  astroliftRoleBindings: AstroliftRoleBinding[];
}

/**
 * The data half of MemberDetail (#1106). Reuses LIST_MEMBERS (no singular
 * query exists) and the already-loaded LIST_ROLE_BINDINGS to surface the
 * member's granted roles.
 */
export function useMemberDetail(id: string) {
  const { data, loading } = useQuery<MembersResp>(LIST_MEMBERS, {
    variables: {},
    fetchPolicy: "cache-and-network",
  });
  const bindingsQuery = useQuery<RoleBindingsResp>(LIST_ROLE_BINDINGS, {
    fetchPolicy: "cache-and-network",
  });

  const member = React.useMemo(
    () => (data?.astroliftMembers ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  const roleBindings = React.useMemo(
    () =>
      (bindingsQuery.data?.astroliftRoleBindings ?? []).filter(
        (b) => b.user?.id && member?.user.id && b.user.id === member.user.id
      ),
    [bindingsQuery.data, member]
  );

  return { id, member, loading, roleBindings, rolesLoading: bindingsQuery.loading };
}
