"use client";

import { useQuery } from "@apollo/client/react";

import type {
  TemporaryAppCapabilitiesQuery,
  TemporaryAppPolicyQuery,
} from "@/graphql/__generated__/operations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  TEMPORARY_APP_CAPABILITIES,
  TEMPORARY_APP_POLICY,
} from "@/graphql/registry/temporary-apps.queries";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import type { TemporaryAppRules } from "./temporary-app-model";

export function useTemporaryAppRules({ skip = false } = {}) {
  const { org } = useActiveOrg();
  const permissions = useMyPermissions();
  const capabilities = useQuery<TemporaryAppCapabilitiesQuery>(TEMPORARY_APP_CAPABILITIES, {
    skip,
    fetchPolicy: "cache-and-network",
  });
  const advertised = capabilities.data?.astroliftServerInfo?.capabilities;
  const validCapabilities =
    Array.isArray(advertised) && advertised.every((value) => typeof value === "string");
  const supported = validCapabilities && advertised.includes("apps.temporary_expiry");
  const policy = useQuery<TemporaryAppPolicyQuery>(TEMPORARY_APP_POLICY, {
    skip: skip || !org?.id || !supported,
    fetchPolicy: "no-cache",
  });
  const value = policy.data?.astroliftTemporaryAppPolicy;
  const rules: TemporaryAppRules | null =
    supported && value
      ? {
          policy: value,
          permanentAllowed:
            !value.temporaryOnlyForMembers ||
            (!permissions.loading && permissions.can("org.update")),
        }
      : null;
  return {
    rules,
    loading:
      !skip && (capabilities.loading || (supported && (policy.loading || permissions.loading))),
    failed:
      !skip &&
      Boolean(
        capabilities.error ||
        (!capabilities.loading && !validCapabilities) ||
        (supported && (policy.error || (!policy.loading && !value)))
      ),
    onRetry: () => {
      void capabilities.refetch().catch(() => {});
      if (supported) void policy.refetch().catch(() => {});
    },
  };
}
