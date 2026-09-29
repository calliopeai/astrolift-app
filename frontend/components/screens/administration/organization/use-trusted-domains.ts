"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { selectRows } from "@/components/list/select-rows";
import { useLocalListState } from "@/components/list/use-list-state";

import {
  ADD_ORGANIZATION_ALLOWLIST_DOMAIN,
  REMOVE_ORGANIZATION_ALLOWLIST_DOMAIN,
} from "@/graphql/identity/identity.mutations";
import {
  LIST_ORGANIZATION_ALLOWLIST_DOMAINS,
  LIST_ROLES,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftOrganizationAllowlistedDomain,
  AstroliftRole,
  MutationResult,
} from "@/graphql/identity/identity.types";

import { TRUSTED_DOMAINS_LIST, TRUSTED_DOMAINS_SELECT } from "./trusted-domains-list";

interface ListResp {
  astroliftOrganizationAllowlistDomains: AstroliftOrganizationAllowlistedDomain[];
}

interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

export interface TrustedDomainInput {
  domain: string;
  defaultRoleSlug: string | null;
  requiresReview: boolean;
}

/**
 * The server half of TrustedDomainsCard: the allowlist (filtered, sorted and
 * paged in the client over in-memory list state, since the settings page
 * owns `?section=`) and its mutations.
 */
export function useTrustedDomains() {
  const listState = useLocalListState(TRUSTED_DOMAINS_LIST);
  const list = useQuery<ListResp>(LIST_ORGANIZATION_ALLOWLIST_DOMAINS);
  const rolesQ = useQuery<RolesResp>(LIST_ROLES);

  const [addDomain, { loading: adding }] = useMutation<{
    addOrganizationAllowlistDomain: MutationResult<AstroliftOrganizationAllowlistedDomain>;
  }>(ADD_ORGANIZATION_ALLOWLIST_DOMAIN, {
    refetchQueries: [{ query: LIST_ORGANIZATION_ALLOWLIST_DOMAINS }],
    awaitRefetchQueries: true,
  });

  const [removeDomain, { loading: removing }] = useMutation<{
    removeOrganizationAllowlistDomain: MutationResult<{
      id: string;
      deleted: boolean;
    }>;
  }>(REMOVE_ORGANIZATION_ALLOWLIST_DOMAIN, {
    refetchQueries: [{ query: LIST_ORGANIZATION_ALLOWLIST_DOMAINS }],
    awaitRefetchQueries: true,
  });

  // Roles that make sense at the org scope. The backend resolves by
  // slug; surfacing ORG-scoped roles plus the broadly-applicable
  // viewer roles keeps the dropdown short while still letting an
  // operator pick e.g. team_viewer (the common 'minimal access' grant).
  const roleOptions = (rolesQ.data?.astroliftRoles ?? []).filter(
    (r) => r.scopeLevel === "ORG" || r.slug.endsWith("_viewer")
  );

  /** Resolves true when the domain was added, so the form can reset. */
  async function onAdd(input: TrustedDomainInput): Promise<boolean> {
    const cleaned = input.domain.trim().toLowerCase();
    if (!cleaned) return false;
    const { data } = await addDomain({
      variables: {
        input: {
          domain: cleaned,
          defaultRoleSlug: input.defaultRoleSlug,
          requiresReview: input.requiresReview,
        },
      },
    });
    if (data?.addOrganizationAllowlistDomain.ok) {
      toast.success(`Added ${cleaned}`);
      return true;
    }
    toast.error(data?.addOrganizationAllowlistDomain.errors?.[0]?.message ?? "Add failed");
    return false;
  }

  /** Throws on failure so ConfirmDialog stays open and surfaces the error. */
  async function onRemove(row: AstroliftOrganizationAllowlistedDomain): Promise<void> {
    const { data } = await removeDomain({
      variables: { input: { id: row.id } },
    });
    if (data?.removeOrganizationAllowlistDomain.ok) {
      toast.success(`Removed ${row.domain}`);
    } else {
      throw new Error(
        data?.removeOrganizationAllowlistDomain.errors?.[0]?.message ?? "Remove failed"
      );
    }
  }

  const { state } = listState;
  const all = list.data?.astroliftOrganizationAllowlistDomains ?? [];
  const page = selectRows(
    all,
    {
      filters: listState.filters,
      q: state.q,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    },
    TRUSTED_DOMAINS_SELECT
  );

  return {
    list: listState,
    rows: page.rows,
    totalCount: page.totalCount,
    loading: list.loading && all.length === 0,
    error: list.error && all.length === 0 ? { message: list.error.message } : null,
    onRetry: () => {
      void list.refetch();
    },
    roleOptions,
    adding,
    removing,
    onAdd,
    onRemove,
  };
}
