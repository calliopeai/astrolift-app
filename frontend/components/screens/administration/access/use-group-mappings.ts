"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import type { RoleRef, ScopeRef } from "@/components/access/access-model";
import { useScopeTree } from "@/components/access/use-scope-tree";
import type { CursorPage } from "@/components/data-table";
import { useLocalListState } from "@/components/list/use-list-state";
import {
  CREATE_GROUP_ROLE_MAPPING,
  DELETE_GROUP_ROLE_MAPPING,
} from "@/graphql/access/access.mutations";
import { LIST_GROUP_ROLE_MAPPINGS_PAGE } from "@/graphql/access/access.queries";
import { LIST_ROLES_I_CAN_GRANT } from "@/graphql/identity/identity.queries";
import type { AstroliftRole, MutationResult } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { GROUP_MAPPINGS_LIST, groupMappingsVariables } from "./group-mappings";
import type { GroupMapping, GroupMappingsPanelProps } from "./GroupMappingsPanel";

interface MappingsResp {
  astroliftGroupRoleMappingsPage: CursorPage<GroupMapping>;
}

/** The mapping list by name, and the grants and access reads a mapping changes. */
const REFETCH = ["ListGroupRoleMappingsPage", "AccessOn", "PrincipalSearch"];

/**
 * The data half of GroupMappingsPanel: one group's mappings (numbered, on the
 * server), the roles the viewer can grant for the add form, the scope tree,
 * and the create and delete mutations.
 */
export function useGroupMappings(externalId: string): GroupMappingsPanelProps {
  const perms = useMyPermissions();
  const canManage = perms.can("org.manage_members");
  const list = useLocalListState(GROUP_MAPPINGS_LIST);
  const query = useQuery<MappingsResp>(LIST_GROUP_ROLE_MAPPINGS_PAGE, {
    variables: groupMappingsVariables(externalId, list.state),
    fetchPolicy: "cache-and-network",
  });
  const roles = useQuery<{ astroliftRolesICanGrant: AstroliftRole[] }>(LIST_ROLES_I_CAN_GRANT, {
    skip: !canManage,
  });
  const scopeTree = useScopeTree();

  const [create] = useMutation<{ createGroupRoleMapping: MutationResult<GroupMapping> }>(
    CREATE_GROUP_ROLE_MAPPING,
    { refetchQueries: REFETCH, awaitRefetchQueries: true }
  );
  const [remove] = useMutation<{
    deleteGroupRoleMapping: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_GROUP_ROLE_MAPPING, { refetchQueries: REFETCH, awaitRefetchQueries: true });

  const data = query.data ?? query.previousData;
  const page = data?.astroliftGroupRoleMappingsPage;
  const roleRefs: RoleRef[] = (roles.data?.astroliftRolesICanGrant ?? []).map((r) => ({
    ...r,
    scopeLevel: r.scopeLevel as RoleRef["scopeLevel"],
  }));

  return {
    externalId,
    list,
    rows: page?.items ?? [],
    totalCount: page?.totalCount ?? 0,
    loading: query.loading && !data,
    stale: query.loading && !query.data && Boolean(data),
    error: query.error ? { message: query.error.message } : null,
    onRetry: () => void query.refetch(),
    canManage,
    roles: roleRefs,
    scopeTree,
    onCreate: async (roleId: string, scope: ScopeRef) => {
      try {
        const { data: res } = await create({
          variables: {
            input: {
              groupExternalId: externalId,
              roleId,
              scopeKind: scope.kind,
              scopeGuid: scope.id,
            },
          },
        });
        if (res?.createGroupRoleMapping.ok) {
          toast.success(`Mapped ${externalId}`);
          return null;
        }
        return res?.createGroupRoleMapping.errors?.[0]?.message ?? "The mapping failed";
      } catch (err) {
        return err instanceof Error ? err.message : "The mapping failed";
      }
    },
    onDelete: async (mapping: GroupMapping) => {
      const { data: res } = await remove({ variables: { input: { id: mapping.id } } });
      if (res?.deleteGroupRoleMapping.ok) toast.success(`Removed ${mapping.role.slug}`);
      else throw new Error(res?.deleteGroupRoleMapping.errors?.[0]?.message ?? "Remove failed");
    },
  };
}
