"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
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
  localizedEntityAccessList,
  removalOf,
  entryKey,
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
  const t = useTranslations("shared.access.entityPanel");
  const perms = useMyPermissions();
  const list = useListState(localizedEntityAccessList(t));
  const query = useQuery<AccessOnResp>(ACCESS_ON, {
    variables: target ? accessOnVariables(target, list.state) : undefined,
    skip: target === null,
    fetchPolicy: "cache-and-network",
  });
  const [revoke] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    refetchQueries: (result) => (result.data?.revokeRoleBinding.ok ? REFETCH : []),
    onQueryUpdated: (query) => refetchAfterMutation(query, t("refreshWarning")),
    awaitRefetchQueries: true,
  });
  const [unmap] = useMutation<{
    deleteGroupRoleMapping: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_GROUP_ROLE_MAPPING, {
    refetchQueries: (result) => (result.data?.deleteGroupRoleMapping.ok ? REFETCH : []),
    onQueryUpdated: (query) => refetchAfterMutation(query, t("refreshWarning")),
    awaitRefetchQueries: true,
  });

  const data = target ? query.data : undefined;
  const page = data?.astroliftAccessOn;

  async function onRemove(entry: AccessEntry): Promise<void> {
    if (!target || !page?.items.some((item) => entryKey(item) === entryKey(entry)))
      throw new Error(t("noTarget"));
    if (!removalOf(entry)) throw new Error(t("notRemovable"));
    const input = { variables: { input: { id: entry.bindingId } } };
    const result =
      removalOf(entry) === "mapping"
        ? (await unmap(input)).data?.deleteGroupRoleMapping
        : (await revoke(input)).data?.revokeRoleBinding;
    if (result?.ok) toast.success(t("removed", { role: entry.role?.slug ?? t("accessName") }));
    else throw new Error(result?.errors?.[0]?.message ?? t("removeFailed"));
  }

  return {
    list,
    subject,
    rows: page?.items ?? [],
    totalCount: page?.totalCount ?? 0,
    loading: target === null || (query.loading && !data),
    stale: query.loading && Boolean(data),
    error: query.error
      ? { message: query.error.message }
      : target && !query.loading && !page
        ? { message: t("unavailable") }
        : null,
    onRetry: () => {
      void query.refetch().catch(() => {});
    },
    canManage: perms.can("org.manage_members"),
    onRemove,
  };
}
