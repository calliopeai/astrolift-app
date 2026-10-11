"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { MANAGED_DOMAIN_ACTIONS } from "@/graphql/domains/domains.queries";
import type { ManagedDomainActionsQuery } from "@/graphql/__generated__/operations";

import { selectRows } from "@/components/list/select-rows";
import { useListState } from "@/components/list/use-list-state";

import type {
  RevalidateManagedDomainMutation,
  RevalidateManagedDomainMutationVariables,
} from "@/graphql/__generated__/operations";
import {
  CREATE_MANAGED_DOMAIN,
  LIST_MANAGED_DOMAINS,
  REVALIDATE_MANAGED_DOMAIN,
  SOFT_DELETE_MANAGED_DOMAIN,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

import { MANAGED_DOMAINS_LIST, MANAGED_DOMAINS_SELECT } from "./managed-domains-list";

interface Resp {
  astroliftManagedDomains: AstroliftManagedDomain[];
}

/** What the Add zone sheet submits. */
export interface CreateManagedDomainInput {
  zone: string;
  dnsDriver: string;
  defaultFor: string;
  wildcard: boolean;
}

/**
 * The org's managed DNS zones (URL list state, filtered, sorted and paged
 * in the client: see managed-domains-list.ts) and every mutation the list
 * drives (add, revalidate delegation, soft delete). Polls while any zone is
 * still provisioning. The data half of ManagedDomainsScreen.
 */
export function useManagedDomains() {
  const router = useRouter();
  const t = useTranslations("managedDomains");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const actions = useQuery<ManagedDomainActionsQuery>(MANAGED_DOMAIN_ACTIONS, {
    skip: !org?.id || orgLoading || Boolean(orgError),
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const canCreate =
    !actions.loading &&
    !actions.error &&
    actions.data?.astroliftManagedDomainActions.canCreate === true;
  const definition = React.useMemo(
    () => ({
      ...MANAGED_DOMAINS_LIST,
      searchPlaceholder: t("listSearch"),
      fields: MANAGED_DOMAINS_LIST.fields.map((field) => ({
        ...field,
        label:
          field.key === "state"
            ? t("status")
            : field.key === "driver"
              ? t("driver")
              : t("defaultFor"),
        options:
          field.key === "driver"
            ? [
                ...(field.options ?? []),
                { value: "cloudflare_read_only", label: t("cloudflareReadOnly") },
              ]
            : field.options?.map((option) => ({
                ...option,
                label: t(
                  (
                    {
                      active: "provisioned",
                      provisioning: "provisioning",
                      unprovisioned: "unprovisioned",
                      tenant_apps: "tenantApps",
                      preview_envs: "previewEnvironments",
                      both: "both",
                      none: "none",
                    } as Record<string, string>
                  )[option.value] ?? option.value
                ),
              })),
      })),
      views: MANAGED_DOMAINS_LIST.views.map((view) => ({
        ...view,
        label: t(view.key === "all" ? "all" : view.key === "mine" ? "mine" : "unprovisioned"),
        note: view.key === "mine" ? t("mineNote") : view.note,
      })),
    }),
    [t]
  );
  const list = useListState(definition);
  const { data, loading, error, refetch, startPolling, stopPolling } = useQuery<Resp>(
    LIST_MANAGED_DOMAINS,
    {
      skip: !org?.id || orgLoading || Boolean(orgError),
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );

  // NS records land on the row a few seconds after the provisioning
  // workflow creates the zone; poll until every domain settles so the
  // operator sees them without refreshing.
  const provisioning = (data?.astroliftManagedDomains ?? []).some(
    (d) => d.provisionState !== "mark_active"
  );
  React.useEffect(() => {
    if (provisioning) startPolling(10_000);
    else stopPolling();
    return () => stopPolling();
  }, [provisioning, startPolling, stopPolling]);

  const [createDomain, { loading: creating }] = useMutation<{
    createManagedDomain: MutationResult<AstroliftManagedDomain>;
  }>(CREATE_MANAGED_DOMAIN);

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteManagedDomain: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_MANAGED_DOMAIN, {
    refetchQueries: [{ query: LIST_MANAGED_DOMAINS }],
    awaitRefetchQueries: true,
  });
  const [revalidate, { loading: revalidating }] = useMutation<
    RevalidateManagedDomainMutation,
    RevalidateManagedDomainMutationVariables
  >(REVALIDATE_MANAGED_DOMAIN, {
    refetchQueries: [{ query: LIST_MANAGED_DOMAINS }],
  });

  async function onRevalidate(d: AstroliftManagedDomain) {
    if (!d.provisionClusterId) {
      toast.error("No provisioning cluster is recorded for this domain");
      return;
    }
    const result = await revalidate({
      variables: { clusterId: d.provisionClusterId, zone: d.zone },
    });
    const payload = result.data?.revalidateManagedDomain;
    if (payload?.ok) toast.success(payload.data?.message ?? `Revalidation started for ${d.zone}`);
    else toast.error(payload?.errors?.[0]?.message ?? "Unable to start revalidation");
  }

  /** Resolves true when the zone was created (close the sheet). */
  async function onCreate(input: CreateManagedDomainInput): Promise<boolean> {
    if (!canCreate || creating) return false;
    const { data } = await createDomain({
      variables: {
        input: {
          zone: input.zone.trim(),
          dnsDriver: input.dnsDriver.trim(),
          defaultFor: input.defaultFor,
          isWildcardManaged: input.wildcard,
        },
      },
    });
    if (data?.createManagedDomain.ok) {
      toast.success(t("createAccepted"));
      await refetch().catch(() => toast.error(t("refreshFailed")));
      return true;
    }
    toast.error(data?.createManagedDomain.errors?.[0]?.message ?? "Failed");
    return false;
  }

  /** Throws on failure so the confirm dialog shows the error and stays open. */
  async function onDelete(d: AstroliftManagedDomain) {
    const { data } = await softDelete({ variables: { input: { id: d.id } } });
    if (data?.softDeleteManagedDomain.ok) {
      toast.success(`Deleted ${d.zone}`);
    } else {
      throw new Error(data?.softDeleteManagedDomain.errors?.[0]?.message ?? "Failed");
    }
  }

  async function onCopyNameservers(d: AstroliftManagedDomain) {
    await navigator.clipboard.writeText(d.provisionNameservers.join("\n"));
    toast.success(t("copied"));
  }

  function onOpen(d: AstroliftManagedDomain) {
    router.push(`/domains/${d.id}`);
  }

  const all = error || orgError || !org?.id ? [] : (data?.astroliftManagedDomains ?? []);
  const { state } = list;
  const page = selectRows(
    all,
    {
      filters: list.filters,
      q: state.q,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    },
    MANAGED_DOMAINS_SELECT
  );

  return {
    list,
    rows: page.rows,
    totalCount: page.totalCount,
    loading: orgLoading || loading,
    error:
      orgError || error
        ? { message: orgError?.message ?? error?.message ?? t("readFailed") }
        : null,
    onRetry: () => {
      void refetch();
    },
    creating,
    canCreate,
    deleting,
    revalidating,
    onCreate,
    onDelete,
    onRevalidate,
    onCopyNameservers,
    onOpen,
  };
}

export type ManagedDomainsState = ReturnType<typeof useManagedDomains>;
