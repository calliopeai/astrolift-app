"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

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
 * The org's managed DNS zones and every mutation the list drives (add,
 * revalidate delegation, soft delete). Polls while any zone is still
 * provisioning. The data half of ManagedDomainsScreen.
 */
export function useManagedDomains() {
  const router = useRouter();
  const { data, loading, startPolling, stopPolling } = useQuery<Resp>(LIST_MANAGED_DOMAINS);

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
  }>(CREATE_MANAGED_DOMAIN, {
    refetchQueries: [{ query: LIST_MANAGED_DOMAINS }],
    awaitRefetchQueries: true,
  });

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
      toast.success(`Created ${input.zone} — provisioning; nameservers will appear shortly`);
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
    toast.success("Nameservers copied");
  }

  function onOpen(d: AstroliftManagedDomain) {
    router.push(`/domains/${d.id}`);
  }

  return {
    loading,
    domains: data?.astroliftManagedDomains ?? [],
    creating,
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
