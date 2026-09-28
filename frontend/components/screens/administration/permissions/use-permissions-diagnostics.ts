"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_ROLE_BINDINGS } from "@/graphql/identity/identity.queries";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import { GET_MY_PERMISSIONS } from "@/graphql/permissions/astrolift.queries";
import { GET_ME } from "@/graphql/user/user.queries";
import type { CurrentUser } from "@/graphql/user/user.types";

interface MyPermsResp {
  astroliftMyPermissions: string[];
}
interface BindingsResp {
  astroliftRoleBindings: AstroliftRoleBinding[];
}
interface MeResp {
  me: CurrentUser | null;
}

/**
 * The data half of PermissionsDiagnosticsView. Effective permissions come
 * from `astroliftMyPermissions`; the role bindings behind them come from
 * `astroliftRoleBindings`, narrowed to the viewer.
 */
export function usePermissionsDiagnostics() {
  const me = useQuery<MeResp>(GET_ME);
  const perms = useQuery<MyPermsResp>(GET_MY_PERMISSIONS);
  const bindings = useQuery<BindingsResp>(LIST_ROLE_BINDINGS);

  const myBindings =
    bindings.data?.astroliftRoleBindings.filter((b) => b.user?.id === me.data?.me?.id) ?? [];

  return {
    me: me.data?.me ?? null,
    permissions: perms.data?.astroliftMyPermissions ?? [],
    permissionsLoading: perms.loading,
    myBindings,
    bindingsLoading: bindings.loading,
  };
}
