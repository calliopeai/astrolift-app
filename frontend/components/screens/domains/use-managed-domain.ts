"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import type {
  ManagedDomainQuery,
  ManagedDomainQueryVariables,
  ManagedDomainDiagnosticsQuery,
  ManagedDomainDiagnosticsQueryVariables,
  ManagedDomainProbeQuery,
  ManagedDomainProbeQueryVariables,
  SoftDeleteManagedDomainMutation,
  SoftDeleteManagedDomainMutationVariables,
  RevalidateManagedDomainMutation,
  RevalidateManagedDomainMutationVariables,
  DnsConnectionSupportQuery,
  DnsDomainBindingQuery,
  DnsDomainBindingQueryVariables,
  VerifyDnsDomainMutation,
  VerifyDnsDomainMutationVariables,
} from "@/graphql/__generated__/operations";
import {
  GET_MANAGED_DOMAIN,
  MANAGED_DOMAIN_DIAGNOSTICS,
  MANAGED_DOMAIN_PROBE,
  DNS_CONNECTION_SUPPORT,
  DNS_DOMAIN_BINDING,
  VERIFY_DNS_DOMAIN,
} from "@/graphql/domains/domains.queries";
import {
  REVALIDATE_MANAGED_DOMAIN,
  SOFT_DELETE_MANAGED_DOMAIN,
} from "@/graphql/clusters/clusters.queries";

export type DomainProbeInput = Pick<
  ManagedDomainProbeQueryVariables,
  "hostname" | "tool" | "recordType"
>;
export function useManagedDomain(id: string) {
  const t = useTranslations("managedDomains");
  const connectionText = useTranslations("domainConnections");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const client = useApolloClient();
  const skipped = !org?.id || orgLoading || Boolean(orgError);
  const query = useQuery<ManagedDomainQuery, ManagedDomainQueryVariables>(GET_MANAGED_DOMAIN, {
    variables: { domainId: id },
    skip: skipped,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const raw = skipped || query.error ? null : (query.data?.astroliftManagedDomain ?? null);
  const mismatch = Boolean(
    raw &&
    (raw.id !== id ||
      !Number.isSafeInteger(raw.version) ||
      raw.version < 1 ||
      (org?.slug && raw.organizationSlug && raw.organizationSlug !== org.slug))
  );
  const domain = mismatch ? null : raw;
  const diagnosticsQuery = useQuery<
    ManagedDomainDiagnosticsQuery,
    ManagedDomainDiagnosticsQueryVariables
  >(MANAGED_DOMAIN_DIAGNOSTICS, {
    variables: { domainId: id, expectedVersion: domain?.version ?? 0 },
    skip: !domain || query.loading,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const observed =
    !domain || diagnosticsQuery.error
      ? null
      : (diagnosticsQuery.data?.astroliftManagedDomainDiagnostics ?? null);
  const observationMismatch = Boolean(
    observed &&
    (observed.id !== id || observed.version !== domain?.version || observed.zone !== domain?.zone)
  );
  const diagnostics = observationMismatch ? null : observed;
  const ownDomainReadable = Boolean(
    domain && !query.loading && domain.organizationSlug && !skipped
  );
  const supportQuery = useQuery<DnsConnectionSupportQuery>(DNS_CONNECTION_SUPPORT, {
    skip: !ownDomainReadable,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const bindingAllowed =
    ownDomainReadable &&
    !supportQuery.loading &&
    !supportQuery.error &&
    supportQuery.data?.dnsProviderConnectionSupport.allowed === true &&
    supportQuery.data.dnsProviderConnectionSupport.dnsWritesSupported === false;
  const bindingQuery = useQuery<DnsDomainBindingQuery, DnsDomainBindingQueryVariables>(
    DNS_DOMAIN_BINDING,
    {
      variables: { domainId: id },
      skip: !bindingAllowed,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const binding =
    !bindingAllowed || bindingQuery.error || bindingQuery.loading
      ? null
      : bindingQuery.data?.dnsProviderDomainBinding;
  const [probe, setProbe] = React.useState<
    ManagedDomainProbeQuery["astroliftManagedDomainProbe"] | null
  >(null);
  const [probeError, setProbeError] = React.useState<string | null>(null);
  const [probeLoading, setProbeLoading] = React.useState(false);
  const [probeVersion, setProbeVersion] = React.useState<number | null>(null);
  const [actionMessage, setActionMessage] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [removed, setRemoved] = React.useState(false);
  const canVerify = Boolean(
    domain &&
    !query.loading &&
    !removed &&
    binding?.domainId === id &&
    binding.domainVersion === domain.version &&
    binding.zoneName === domain.zone &&
    binding.canVerify === true
  );
  const generation = React.useRef(0);
  const mounted = React.useRef(true);
  React.useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const versionRef = React.useRef(domain?.version);
  React.useLayoutEffect(() => {
    versionRef.current = domain?.version;
    generation.current++;
  }, [domain?.version]);
  function onResetProbe() {
    generation.current++;
    setProbe(null);
    setProbeError(null);
    setProbeLoading(false);
    setProbeVersion(null);
  }
  async function onProbe(input: DomainProbeInput) {
    if (!domain || removed || query.loading || query.error) return;
    const epoch = ++generation.current;
    setProbe(null);
    setProbeError(null);
    setProbeLoading(true);
    setProbeVersion(domain.version);
    try {
      const result = await client.query<ManagedDomainProbeQuery, ManagedDomainProbeQueryVariables>({
        query: MANAGED_DOMAIN_PROBE,
        variables: {
          domainId: id,
          expectedVersion: domain.version,
          ...input,
          hostname: input.hostname.trim().toLowerCase().replace(/\.$/, ""),
        },
        fetchPolicy: "no-cache",
        context: { queryDeduplication: false },
      });
      if (!mounted.current || epoch !== generation.current) return;
      const value = result.data?.astroliftManagedDomainProbe;
      if (
        !value ||
        value.hostname !== input.hostname.trim().toLowerCase().replace(/\.$/, "") ||
        value.tool !== input.tool ||
        value.recordType !== input.recordType
      ) {
        setProbeError(t("versionChanged"));
        return;
      }
      setProbe(value);
    } catch (error) {
      if (mounted.current && epoch === generation.current)
        setProbeError(error instanceof Error ? error.message : t("readFailed"));
    } finally {
      if (mounted.current && epoch === generation.current) setProbeLoading(false);
    }
  }
  const [revalidate, { loading: revalidating }] = useMutation<
    RevalidateManagedDomainMutation,
    RevalidateManagedDomainMutationVariables
  >(REVALIDATE_MANAGED_DOMAIN);
  const [verify, { loading: verifying }] = useMutation<
    VerifyDnsDomainMutation,
    VerifyDnsDomainMutationVariables
  >(VERIFY_DNS_DOMAIN);
  async function onVerify() {
    if (!domain || !canVerify || verifying) return;
    const expectedVersion = domain.version;
    setActionError(null);
    setActionMessage(null);
    try {
      const result = await verify({ variables: { input: { zone: domain.zone } } });
      if (!mounted.current || versionRef.current !== expectedVersion) return;
      const payload = result.data?.verifyManagedDomain;
      if (payload?.ok === false) throw new Error(payload.errors?.[0]?.message ?? t("actionFailed"));
      if (payload?.ok !== true) throw new Error(t("actionUncertain"));
      setActionMessage(
        payload.data?.zone !== domain.zone
          ? t("acceptedUnverified")
          : payload.data.verified === true
            ? connectionText("ownershipVerified")
            : connectionText("verifyPending")
      );
      await Promise.all([query.refetch(), bindingQuery.refetch()]).catch(() =>
        setActionError(t("refreshFailed"))
      );
    } catch (error) {
      if (mounted.current && versionRef.current === expectedVersion)
        setActionError(error instanceof Error ? error.message : t("actionUncertain"));
    }
  }
  async function onRevalidate() {
    if (
      !domain ||
      !diagnostics?.actions.canRevalidate ||
      !diagnostics.provisionClusterId ||
      query.loading ||
      diagnosticsQuery.loading
    )
      return;
    onResetProbe();
    setActionError(null);
    setActionMessage(null);
    try {
      const result = await revalidate({
        variables: { clusterId: diagnostics.provisionClusterId, zone: domain.zone },
      });
      const payload = result.data?.revalidateManagedDomain;
      if (payload?.ok === false) throw new Error(payload.errors?.[0]?.message ?? t("actionFailed"));
      if (payload?.ok !== true) throw new Error(t("actionUncertain"));
      setActionMessage(
        payload.data?.zone === domain.zone ? t("revalidationAccepted") : t("acceptedUnverified")
      );
      await query.refetch().catch(() => setActionError(t("refreshFailed")));
    } catch (error) {
      setActionError(error instanceof Error ? error.message : t("actionUncertain"));
    }
  }
  const [softDelete, { loading: deleting }] = useMutation<
    SoftDeleteManagedDomainMutation,
    SoftDeleteManagedDomainMutationVariables
  >(SOFT_DELETE_MANAGED_DOMAIN);
  async function onDelete() {
    if (
      !domain ||
      removed ||
      !diagnostics?.actions.canDelete ||
      query.loading ||
      diagnosticsQuery.loading
    )
      return false;
    setActionError(null);
    setActionMessage(null);
    try {
      const result = await softDelete({ variables: { input: { id: domain.id } } });
      const payload = result.data?.softDeleteManagedDomain;
      if (payload?.ok === false) throw new Error(payload.errors?.[0]?.message ?? t("actionFailed"));
      if (payload?.ok !== true) throw new Error(t("actionUncertain"));
      onResetProbe();
      if (payload.data?.id === domain.id && payload.data.deleted === true) {
        setRemoved(true);
        setActionMessage(t("removed"));
      } else {
        setActionMessage(t("acceptedUnverified"));
        await query.refetch().catch(() => setActionError(t("refreshFailed")));
      }
      return true;
    } catch (error) {
      setActionError(error instanceof Error ? error.message : t("actionUncertain"));
      return false;
    }
  }
  function onRetry() {
    onResetProbe();
    if (!skipped) void query.refetch().catch(() => {});
    if (ownDomainReadable) void supportQuery.refetch().catch(() => {});
  }
  function onRefreshDiagnostics() {
    onResetProbe();
    if (domain && !query.loading) void diagnosticsQuery.refetch().catch(() => {});
    if (ownDomainReadable) void supportQuery.refetch().catch(() => {});
  }
  return {
    domain,
    loading: orgLoading || (!skipped && query.loading),
    error:
      orgError?.message ??
      query.error?.message ??
      (mismatch ? t("versionChanged") : !org?.id && !orgLoading ? t("readFailed") : null),
    onRetry,
    diagnostics,
    diagnosticsLoading: Boolean(domain && diagnosticsQuery.loading),
    diagnosticsError:
      diagnosticsQuery.error?.message ?? (observationMismatch ? t("versionChanged") : null),
    onRefreshDiagnostics,
    probe: probeVersion === domain?.version ? probe : null,
    probeLoading: probeVersion === domain?.version && probeLoading,
    probeError: probeVersion === domain?.version ? probeError : null,
    onProbe,
    onResetProbe,
    onRevalidate,
    revalidating,
    onVerify,
    verifying,
    canVerify,
    onDelete,
    deleting,
    removed,
    actionMessage,
    actionError,
  };
}
export type ManagedDomainState = ReturnType<typeof useManagedDomain>;
