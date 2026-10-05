"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import type {
  DnsConnectionSupportQuery,
  DnsConnectionsPageQuery,
  DnsConnectionsPageQueryVariables,
  CloudflareZonesQuery,
  CloudflareZonesQueryVariables,
  CloudflareRecordsQuery,
  CloudflareRecordsQueryVariables,
  ConnectCloudflareTokenMutation,
  ConnectCloudflareTokenMutationVariables,
  BeginCloudflareOAuthMutation,
  BeginCloudflareOAuthMutationVariables,
  RetestDnsConnectionMutation,
  RetestDnsConnectionMutationVariables,
  DisconnectDnsConnectionMutation,
  DisconnectDnsConnectionMutationVariables,
  RegisterCloudflareZoneMutation,
  RegisterCloudflareZoneMutationVariables,
  AttachCloudflareZoneMutation,
  AttachCloudflareZoneMutationVariables,
  VerifyDnsDomainMutation,
  VerifyDnsDomainMutationVariables,
  ManagedDomainQuery,
  ManagedDomainQueryVariables,
  ListManagedDomainsQuery,
  DnsDomainBindingQuery,
  DnsDomainBindingQueryVariables,
} from "@/graphql/__generated__/operations";
import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/clusters.queries";
import {
  DNS_CONNECTION_SUPPORT,
  DNS_CONNECTIONS_PAGE,
  CLOUDFLARE_ZONES,
  CLOUDFLARE_RECORDS,
  CONNECT_CLOUDFLARE_TOKEN,
  BEGIN_CLOUDFLARE_OAUTH,
  RETEST_DNS_CONNECTION,
  DISCONNECT_DNS_CONNECTION,
  REGISTER_CLOUDFLARE_ZONE,
  ATTACH_CLOUDFLARE_ZONE,
  VERIFY_DNS_DOMAIN,
  GET_MANAGED_DOMAIN,
  DNS_DOMAIN_BINDING,
} from "@/graphql/domains/domains.queries";

export type DnsConnection = DnsConnectionsPageQuery["dnsProviderConnectionsPage"]["items"][number];
export type DnsZone = CloudflareZonesQuery["cloudflareDnsZones"]["items"][number];
const isGuid = (value: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const version = (value: number) => Number.isSafeInteger(value) && value > 0;
const context = { queryDeduplication: false };

/** Request-local fences include actor/org, exact source versions, and selected native zone. */
function useSetupEpoch(binding: unknown) {
  const key = JSON.stringify(binding);
  const [scope, setScope] = React.useState({ key, revision: 0 });
  if (scope.key !== key) setScope({ key, revision: scope.revision + 1 });
  const latest = React.useRef({ key, revision: scope.revision });
  const lifecycle = React.useRef(0);
  const mounted = React.useRef(false);
  React.useLayoutEffect(() => {
    latest.current = { key, revision: scope.revision };
  });
  React.useLayoutEffect(() => {
    mounted.current = true;
    const current = lifecycle.current + 1;
    lifecycle.current = current;
    return () => {
      mounted.current = false;
      lifecycle.current = current + 1;
    };
  }, []);
  return () => {
    const current = lifecycle.current;
    return () =>
      mounted.current &&
      lifecycle.current === current &&
      latest.current.key === key &&
      latest.current.revision === scope.revision;
  };
}

export function useDnsConnectionSetup(domainId?: string) {
  const t = useTranslations("domainConnections");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const { user, loading: userLoading, error: userError } = useMe();
  const client = useApolloClient();
  const unavailable =
    !org?.id || !user?.id || orgLoading || userLoading || !!orgError || !!userError;
  const [provider, setProvider] = React.useState<"route53" | "cloudflare" | null>(null);
  const [page, setPage] = React.useState(1);
  const [selection, setSelection] = React.useState<{ id: string; version: number } | null>(null);
  const [zone, setZone] = React.useState<DnsZone | null>(null);
  const [binding, setBinding] = React.useState<
    RegisterCloudflareZoneMutation["registerCloudflareDnsZone"]["data"] | null
  >(null);
  const [busy, setBusy] = React.useState(false);
  const inFlight = React.useRef(false);
  const [message, setMessage] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [uncertain, setUncertain] = React.useState(false);
  const [connectionRecovery, setConnectionRecovery] = React.useState(false);
  const [recoveredConnections, setRecoveredConnections] = React.useState(false);
  const supportQuery = useQuery<DnsConnectionSupportQuery>(DNS_CONNECTION_SUPPORT, {
    skip: unavailable,
    fetchPolicy: "no-cache",
    context,
  });
  const support =
    unavailable || supportQuery.error
      ? null
      : (supportQuery.data?.dnsProviderConnectionSupport ?? null);
  const allowed =
    !supportQuery.loading && support?.allowed === true && support.dnsWritesSupported === false;
  const connectionsQuery = useQuery<DnsConnectionsPageQuery, DnsConnectionsPageQueryVariables>(
    DNS_CONNECTIONS_PAGE,
    {
      variables: { page, pageSize: 20 },
      skip: unavailable || provider !== "cloudflare" || !allowed,
      fetchPolicy: "no-cache",
      context,
    }
  );
  const connectionPage = connectionsQuery.error
    ? null
    : (connectionsQuery.data?.dnsProviderConnectionsPage ?? null);
  const validPage =
    connectionPage?.page === page &&
    connectionPage.pageSize === 20 &&
    connectionPage.items.every(
      (c) =>
        isGuid(c.id) &&
        version(c.version) &&
        c.provider === "CLOUDFLARE" &&
        c.dnsWritesSupported === false
    );
  const connections =
    validPage && (!connectionRecovery || recoveredConnections) ? connectionPage : null;
  const connection = !connectionsQuery.loading
    ? (connections?.items.find(
        (c) => c.id === selection?.id && c.version === selection.version && c.state === "ACTIVE"
      ) ?? null)
    : null;
  const zonesQuery = useQuery<CloudflareZonesQuery, CloudflareZonesQueryVariables>(
    CLOUDFLARE_ZONES,
    {
      variables: {
        input: { connectionId: connection?.id ?? "", expectedVersion: connection?.version ?? 0 },
      },
      skip: !allowed || !connection,
      fetchPolicy: "no-cache",
      context,
    }
  );
  const zones =
    connection && !zonesQuery.error ? (zonesQuery.data?.cloudflareDnsZones ?? null) : null;
  const selectedZone =
    zones?.complete === true && !zonesQuery.loading
      ? (zones.items.find(
          (z) => z.id === zone?.id && z.name === zone.name && z.accountId === zone.accountId
        ) ?? null)
      : null;
  const recordsQuery = useQuery<CloudflareRecordsQuery, CloudflareRecordsQueryVariables>(
    CLOUDFLARE_RECORDS,
    {
      variables: {
        input: {
          connectionId: connection?.id ?? "",
          expectedVersion: connection?.version ?? 0,
          zoneId: selectedZone?.id ?? "",
          zoneName: selectedZone?.name ?? "",
        },
      },
      skip: !allowed || !connection || !selectedZone,
      fetchPolicy: "no-cache",
      context,
    }
  );
  const records =
    !selectedZone || recordsQuery.error ? null : (recordsQuery.data?.cloudflareDnsRecords ?? null);
  const readDomainId = binding?.domainId ?? domainId;
  const targetQuery = useQuery<ManagedDomainQuery, ManagedDomainQueryVariables>(
    GET_MANAGED_DOMAIN,
    {
      variables: { domainId: readDomainId ?? "" },
      skip: unavailable || !readDomainId,
      fetchPolicy: "no-cache",
      context,
    }
  );
  const targetRaw = !targetQuery.error ? targetQuery.data?.astroliftManagedDomain : null;
  const currentDomain =
    targetRaw &&
    readDomainId &&
    targetRaw.id === readDomainId &&
    version(targetRaw.version) &&
    (!binding || targetRaw.version >= binding.domainVersion) &&
    (!org?.slug || targetRaw.organizationSlug === org.slug)
      ? targetRaw
      : null;
  const target = domainId ? currentDomain : null;
  const bindingQuery = useQuery<DnsDomainBindingQuery, DnsDomainBindingQueryVariables>(
    DNS_DOMAIN_BINDING,
    {
      variables: { domainId: readDomainId ?? "" },
      skip: unavailable || !readDomainId || !allowed,
      fetchPolicy: "no-cache",
      context,
    }
  );
  const bindingRaw = bindingQuery.error
    ? null
    : (bindingQuery.data?.dnsProviderDomainBinding ?? null);
  const currentBinding =
    currentDomain &&
    bindingRaw?.domainId === currentDomain.id &&
    bindingRaw.domainVersion === currentDomain.version &&
    bindingRaw.zoneName === currentDomain.zone &&
    bindingRaw.dnsWritesSupported === false
      ? bindingRaw
      : null;
  const verification =
    currentBinding && currentDomain
      ? {
          domainId: currentDomain.id,
          domainVersion: currentDomain.version,
          zoneName: currentDomain.zone,
          verificationState: currentDomain.verificationState,
          verificationRecordName: currentDomain.challengeRecordName,
          verificationRecordValue: currentDomain.challengeRecordValue,
        }
      : null;
  const route53Query = useQuery<ListManagedDomainsQuery>(LIST_MANAGED_DOMAINS, {
    skip: unavailable || provider !== "route53",
    fetchPolicy: "no-cache",
    context,
  });
  const route53Domains = !route53Query.error
    ? (route53Query.data?.astroliftManagedDomains ?? []).filter(
        (d) => d.dnsDriver === "route53" && isGuid(d.id)
      )
    : [];
  const epoch = useSetupEpoch([
    org?.id,
    user?.id,
    unavailable,
    allowed,
    domainId,
    target?.id,
    target?.version,
    provider,
    selection,
    selectedZone?.id,
    selectedZone?.name,
    selectedZone?.accountId,
  ]);
  const recoveryEpoch = useSetupEpoch([
    org?.id,
    user?.id,
    unavailable,
    provider,
    page,
    domainId,
    target?.id,
    target?.version,
  ]);
  function onProvider(value: "route53" | "cloudflare") {
    if (busy || uncertain) return;
    setProvider(value);
    setSelection(null);
    setZone(null);
    setBinding(null);
    setActionError(null);
    setMessage(null);
  }
  function onConnection(value: DnsConnection) {
    if (
      busy ||
      !allowed ||
      (uncertain && (!connectionRecovery || !recoveredConnections)) ||
      !connections?.items.some(
        (c) => c.id === value.id && c.version === value.version && c.state === "ACTIVE"
      )
    )
      return;
    setUncertain(false);
    setConnectionRecovery(false);
    setSelection({ id: value.id, version: value.version });
    setZone(null);
    setBinding(null);
  }
  function onZone(value: DnsZone) {
    if (
      busy ||
      zones?.complete !== true ||
      !zones.items.some(
        (z) => z.id === value.id && z.name === value.name && z.accountId === value.accountId
      )
    )
      return;
    setZone(value);
    setBinding(null);
  }
  function onPage(value: number) {
    if (!busy && Number.isSafeInteger(value) && value >= 1) {
      setSelection(null);
      setZone(null);
      setPage(value);
    }
  }
  async function write(
    effect: (current: () => boolean) => Promise<boolean>,
    uncertainOnFailure = false,
    creationRecovery = false
  ) {
    const current = epoch();
    if (!current() || inFlight.current || !allowed || unavailable || uncertain) return false;
    inFlight.current = true;
    setBusy(true);
    setActionError(null);
    setMessage(null);
    try {
      return await effect(current);
    } catch {
      if (current()) {
        setActionError(t("uncertain"));
        if (uncertainOnFailure) {
          setUncertain(true);
          setConnectionRecovery(creationRecovery);
          setRecoveredConnections(false);
        }
      }
      return false;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function onConnect(name: string, token: string) {
    return write(
      async (current) => {
        if (!support?.apiTokenSupported || !name.trim() || !token) return false;
        const result = await client.mutate<
          ConnectCloudflareTokenMutation,
          ConnectCloudflareTokenMutationVariables
        >({
          mutation: CONNECT_CLOUDFLARE_TOKEN,
          variables: { input: { organizationId: org!.id, name: name.trim(), token } },
          context,
        });
        if (!current()) return false;
        const payload = result.data?.connectCloudflareDnsToken;
        if (payload?.ok === false) {
          setActionError(payload.errors[0]?.message ?? t("refused"));
          return false;
        }
        if (payload?.ok !== true) throw new Error(t("uncertain"));
        setMessage(t("connectionSaved"));
        await connectionsQuery.refetch().catch(() => setActionError(t("refreshFailed")));
        return true;
      },
      true,
      true
    );
  }
  async function onOAuth(name: string) {
    return write(async (current) => {
      if (!support?.oauthConfigured || !name.trim()) return false;
      const result = await client.mutate<
        BeginCloudflareOAuthMutation,
        BeginCloudflareOAuthMutationVariables
      >({
        mutation: BEGIN_CLOUDFLARE_OAUTH,
        variables: { input: { organizationId: org!.id, name: name.trim() } },
        context,
      });
      if (!current()) return false;
      const payload = result.data?.beginCloudflareDnsOAuth;
      if (payload?.ok === false) {
        setActionError(payload.errors[0]?.message ?? t("refused"));
        return false;
      }
      if (payload?.ok !== true || !payload.data) throw new Error(t("uncertain"));
      const url = new URL(payload.data.authorizationUrl);
      if (
        url.protocol !== "https:" ||
        url.hostname !== "dash.cloudflare.com" ||
        url.pathname !== "/oauth2/auth" ||
        url.username ||
        url.password ||
        url.port ||
        url.hash
      )
        throw new Error(t("uncertain"));
      window.location.assign(url.toString());
      return true;
    });
  }
  async function onRetest(value: DnsConnection) {
    return write(async (current) => {
      if (
        connectionsQuery.loading ||
        !connections?.items.some((row) => row.id === value.id && row.version === value.version)
      )
        return false;
      const result = await client.mutate<
        RetestDnsConnectionMutation,
        RetestDnsConnectionMutationVariables
      >({
        mutation: RETEST_DNS_CONNECTION,
        variables: { input: { connectionId: value.id, expectedVersion: value.version } },
        context,
      });
      if (!current()) return false;
      const payload = result.data?.retestDnsProviderConnection;
      if (payload?.ok === false) {
        setActionError(payload.errors[0]?.message ?? t("refused"));
        return false;
      }
      if (payload?.ok !== true) throw new Error(t("uncertain"));
      setSelection(null);
      setZone(null);
      setMessage(t("retestAccepted"));
      await connectionsQuery.refetch().catch(() => setActionError(t("refreshFailed")));
      return true;
    });
  }
  async function onDisconnect(value: DnsConnection) {
    return write(async (current) => {
      if (
        connectionsQuery.loading ||
        !connections?.items.some((row) => row.id === value.id && row.version === value.version)
      )
        return false;
      const result = await client.mutate<
        DisconnectDnsConnectionMutation,
        DisconnectDnsConnectionMutationVariables
      >({
        mutation: DISCONNECT_DNS_CONNECTION,
        variables: { input: { connectionId: value.id, expectedVersion: value.version } },
        context,
      });
      if (!current()) return false;
      const payload = result.data?.disconnectDnsProviderConnection;
      if (payload?.ok === false) {
        setActionError(payload.errors[0]?.message ?? t("refused"));
        return false;
      }
      if (payload?.ok !== true) throw new Error(t("uncertain"));
      setSelection(null);
      setZone(null);
      setMessage(t("disconnectAccepted"));
      await connectionsQuery.refetch().catch(() => setActionError(t("refreshFailed")));
      return true;
    });
  }
  async function onRegister() {
    return write(async (current) => {
      if (
        uncertain ||
        !connection ||
        !selectedZone ||
        recordsQuery.loading ||
        records?.complete !== true ||
        !!recordsQuery.error ||
        (domainId && (!target || targetQuery.loading || target.zone !== selectedZone?.name))
      )
        return false;
      const selected = {
        connectionId: connection.id,
        expectedConnectionVersion: connection.version,
        zoneId: selectedZone.id,
        zoneName: selectedZone.name,
      };
      const payload = target
        ? (
            await client.mutate<
              AttachCloudflareZoneMutation,
              AttachCloudflareZoneMutationVariables
            >({
              mutation: ATTACH_CLOUDFLARE_ZONE,
              variables: {
                input: { ...selected, domainId: target.id, expectedDomainVersion: target.version },
              },
              context,
            })
          ).data?.attachCloudflareDnsZone
        : (
            await client.mutate<
              RegisterCloudflareZoneMutation,
              RegisterCloudflareZoneMutationVariables
            >({ mutation: REGISTER_CLOUDFLARE_ZONE, variables: { input: selected }, context })
          ).data?.registerCloudflareDnsZone;
      if (!current()) return false;
      if (payload?.ok === false) {
        setActionError(payload.errors[0]?.message ?? t("refused"));
        return false;
      }
      if (payload?.ok !== true) throw new Error(t("uncertain"));
      const value = payload.data;
      if (
        !value ||
        !isGuid(value.domainId) ||
        !version(value.domainVersion) ||
        value.connectionId !== selected.connectionId ||
        value.connectionVersion !== selected.expectedConnectionVersion ||
        value.zone.id !== selected.zoneId ||
        value.zone.name !== selected.zoneName ||
        value.zone.accountId !== selectedZone.accountId ||
        value.dnsWritesSupported !== false ||
        (target && value.domainId !== target.id)
      ) {
        setMessage(t("acceptedUnverified"));
        setUncertain(true);
        return false;
      }
      setBinding(value);
      setMessage(t("registrationAccepted"));
      if (target)
        await Promise.all([targetQuery.refetch(), bindingQuery.refetch()]).catch(() =>
          setActionError(t("refreshFailed"))
        );
      return true;
    }, true);
  }
  const canVerify =
    allowed &&
    !targetQuery.loading &&
    !bindingQuery.loading &&
    !targetQuery.error &&
    !bindingQuery.error &&
    currentBinding?.canVerify === true;
  async function onVerify() {
    return write(async (current) => {
      if (!canVerify || !verification) return false;
      const result = await client.mutate<VerifyDnsDomainMutation, VerifyDnsDomainMutationVariables>(
        {
          mutation: VERIFY_DNS_DOMAIN,
          variables: { input: { zone: verification.zoneName } },
          context,
        }
      );
      if (!current()) return false;
      const payload = result.data?.verifyManagedDomain;
      if (payload?.ok === false) {
        setActionError(payload.errors[0]?.message ?? t("refused"));
        return false;
      }
      if (payload?.ok !== true) throw new Error(t("uncertain"));
      if (payload.data?.zone !== verification.zoneName) {
        setMessage(t("acceptedUnverified"));
        return false;
      }
      setMessage(
        payload.data.verified === true
          ? t("ownershipVerified")
          : (payload.data.message ?? t("verifyPending"))
      );
      await Promise.all([targetQuery.refetch(), bindingQuery.refetch()]).catch(() =>
        setActionError(t("refreshFailed"))
      );
      return true;
    });
  }
  function onRefresh() {
    if (busy) return;
    setSelection(null);
    setZone(null);
    void supportQuery.refetch().catch(() => {});
    if (provider === "cloudflare") {
      const current = recoveryEpoch();
      void connectionsQuery
        .refetch()
        .then((result) => {
          const value = result.data?.dnsProviderConnectionsPage;
          if (
            current() &&
            connectionRecovery &&
            value?.page === page &&
            value.pageSize === 20 &&
            value.items.every(
              (c) =>
                isGuid(c.id) &&
                version(c.version) &&
                c.provider === "CLOUDFLARE" &&
                c.dnsWritesSupported === false
            )
          )
            setRecoveredConnections(true);
        })
        .catch(() => {});
    }
    if (provider === "route53") void route53Query.refetch().catch(() => {});
    if (readDomainId) {
      void targetQuery.refetch().catch(() => {});
      void bindingQuery.refetch().catch(() => {});
    }
  }
  return {
    provider,
    onProvider,
    support,
    supportLoading: orgLoading || userLoading || supportQuery.loading,
    supportError:
      orgError?.message ??
      userError?.message ??
      supportQuery.error?.message ??
      (!unavailable && !supportQuery.loading && !support ? t("unavailable") : null),
    allowed,
    connections,
    connectionsLoading: connectionsQuery.loading,
    connectionsError:
      connectionsQuery.error?.message ??
      (!connectionsQuery.loading && connectionPage && !validPage ? t("unavailable") : null),
    page,
    onPage,
    connection,
    onConnection,
    zones,
    zonesLoading: zonesQuery.loading,
    zonesError: zonesQuery.error?.message ?? null,
    selectedZone,
    onZone,
    records,
    recordsLoading: recordsQuery.loading,
    recordsError: recordsQuery.error?.message ?? null,
    target,
    targetLoading: !!readDomainId && targetQuery.loading,
    targetError:
      targetQuery.error?.message ??
      (domainId && !targetQuery.loading && !target ? t("targetUnavailable") : null),
    route53Domains,
    route53Loading: route53Query.loading,
    route53Error: route53Query.error?.message ?? null,
    binding,
    currentBinding,
    verification,
    bindingLoading: bindingQuery.loading,
    bindingError: bindingQuery.error?.message ?? null,
    busy,
    message,
    actionError,
    uncertain,
    onConnect,
    onOAuth,
    onRetest,
    onDisconnect,
    onRegister,
    onVerify,
    canVerify,
    onRefresh,
  };
}
export type DnsConnectionSetupState = ReturnType<typeof useDnsConnectionSetup>;
