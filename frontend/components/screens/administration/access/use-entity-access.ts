"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import type { ScopeKind } from "@/components/access/access-model";
import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { DELETE_GROUP_ROLE_MAPPING } from "@/graphql/access/access.mutations";
import { ACCESS_ON } from "@/graphql/access/access.queries";
import { REVOKE_ROLE_BINDING } from "@/graphql/identity/identity.mutations";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import {
  type AccessEntry,
  accessOnVariables,
  ENTITY_ACCESS_LIST,
  removalOf,
} from "./entity-access";
import type { EntityAccessPanelProps } from "./EntityAccessPanel";

interface AccessOnResp {
  astroliftAccessOn: CursorPage<AccessEntry>;
}

/** This tab by name, and the lists a removed grant or mapping also leaves. */
const REFETCH = ["AccessOn", "ListRoleBindingsPage", "ListGroupRoleMappingsPage"];

/**
 * The data half of EntityAccessPanel: who has access on one object
 * (`astroliftAccessOn`, numbered and searched on the server), and removing
 * a grant or mapping at its source. `target` null (the page is still
 * finding the object) runs nothing.
 */
export function useEntityAccess(
  target: { kind: ScopeKind | "AGENT"; id: string } | null,
  subject: string
): Omit<EntityAccessPanelProps, "grantHref"> {
  const perms = useMyPermissions();
  const list = useListState(ENTITY_ACCESS_LIST);
  const query = useQuery<AccessOnResp>(ACCESS_ON, {
    variables: target ? accessOnVariables(target, list.state) : undefined,
    skip: target === null,
    fetchPolicy: "cache-and-network",
  });
  const [revoke] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, { refetchQueries: REFETCH, awaitRefetchQueries: true });
  const [unmap] = useMutation<{
    deleteGroupRoleMapping: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_GROUP_ROLE_MAPPING, { refetchQueries: REFETCH, awaitRefetchQueries: true });

  const data = query.data ?? query.previousData;
  const page = data?.astroliftAccessOn;

  async function onRemove(entry: AccessEntry): Promise<void> {
    const input = { variables: { input: { id: entry.bindingId } } };
    const result =
      removalOf(entry) === "mapping"
        ? (await unmap(input)).data?.deleteGroupRoleMapping
        : (await revoke(input)).data?.revokeRoleBinding;
    if (result?.ok) toast.success(`Removed ${entry.role?.slug ?? "access"}`);
    else throw new Error(result?.errors?.[0]?.message ?? "Remove failed");
  }

  return {
    list,
    subject,
    rows: page?.items ?? [],
    totalCount: page?.totalCount ?? 0,
    loading: target === null || (query.loading && !data),
    stale: query.loading && !query.data && Boolean(data),
    error: query.error ? { message: query.error.message } : null,
    onRetry: () => void query.refetch(),
    canManage: perms.can("org.manage_members"),
    onRemove,
  };
}
