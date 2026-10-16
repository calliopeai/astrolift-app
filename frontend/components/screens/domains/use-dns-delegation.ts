"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import type {
  BindManagedDomainDelegationMutation,
  BindManagedDomainDelegationMutationVariables,
  CloudflareZonesQuery,
  CloudflareZonesQueryVariables,
  DnsConnectionsPageQuery,
  DnsConnectionsPageQueryVariables,
  GrantCloudflareDelegationMutation,
  GrantCloudflareDelegationMutationVariables,
} from "@/graphql/__generated__/operations";
import {
  BIND_MANAGED_DOMAIN_DELEGATION,
  CLOUDFLARE_ZONES,
  DNS_CONNECTIONS_PAGE,
  GRANT_CLOUDFLARE_DELEGATION,
} from "@/graphql/domains/domains.queries";
import { waitForElevation } from "@/lib/auth/step-up-events";
import {
  isParentZone,
  type DelegationConnection,
  type DelegationWrite,
  type DelegationZone,
  type DnsDelegationPanelProps,
} from "./DnsDelegationPanel";
import type { ManagedDomainState } from "./use-managed-domain";

type Envelope = { ok: boolean; errors: { code: string }[] } | undefined;
const context = { queryDeduplication: false };

/**
 * Grant and bind for #1787 over the backend's own mutations. Both need a fresh
 * sign-in: a STEP_UP_REQUIRED refusal opens the app-wide prompt and the write
 * is sent once more after it. The token lives only in the call that sends it.
 */
export function useDnsDelegation(state: ManagedDomainState): DnsDelegationPanelProps | null {
  const t = useTranslations("domainDelegation");
  const client = useApolloClient();
  const domain = state.removed ? null : state.domain;
  const [selected, setSelected] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [message, setMessage] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const canWrite = state.dnsConnectionsAllowed;
  const connectionsQuery = useQuery<DnsConnectionsPageQuery, DnsConnectionsPageQueryVariables>(
    DNS_CONNECTIONS_PAGE,
    {
      variables: { page: 1, pageSize: 50 },
      skip: !domain || !canWrite,
      fetchPolicy: "no-cache",
      context,
    }
  );
  const connections: DelegationConnection[] | null = connectionsQuery.error
    ? null
    : (connectionsQuery.data?.dnsProviderConnectionsPage.items.filter(
        (c) => c.provider === "CLOUDFLARE"
      ) ?? null);
  const connection = connections?.find((c) => c.id === selected && c.state === "ACTIVE") ?? null;
  const zonesQuery = useQuery<CloudflareZonesQuery, CloudflareZonesQueryVariables>(
    CLOUDFLARE_ZONES,
    {
      variables: {
        input: { connectionId: connection?.id ?? "", expectedVersion: connection?.version ?? 0 },
      },
      skip: !domain || !canWrite || !connection,
      fetchPolicy: "no-cache",
      context,
    }
  );
  const zones = connection && !zonesQuery.error ? zonesQuery.data?.cloudflareDnsZones : null;
  const parentZones: DelegationZone[] | null =
    zones && domain
      ? zones.items
          .filter((z) => isParentZone(z.name, domain.zone))
          .sort((a, b) => b.name.length - a.name.length)
          .map((z) => ({ id: z.id, name: z.name }))
      : null;
  if (!domain) return null;
  const current = domain;

  async function send(run: () => Promise<Envelope>, done: string) {
    if (busy) return false;
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        const payload = await run();
        if (payload?.ok === true) {
          setMessage(done);
          await Promise.all([connectionsQuery.refetch(), state.onReloadCurrentDomain()]).catch(() =>
            setError(t("refreshFailed"))
          );
          return true;
        }
        const code = payload?.errors[0]?.code ?? "";
        if (code === "STEP_UP_REQUIRED" && attempt === 0 && (await waitForElevation())) continue;
        setError(code || t("uncertain"));
        return false;
      }
      return false;
    } catch {
      setError(t("uncertain"));
      return false;
    } finally {
      setBusy(false);
    }
  }
  const write = (current.delegationWrite ?? null) as DelegationWrite | null;
  return {
    zone: current.zone,
    boundConnectionId: current.delegationConnectionId ?? null,
    boundZoneName: current.delegationZoneName,
    lastWrite: write,
    canWrite,
    busy,
    message,
    error,
    connections,
    connectionsLoading:
      state.dnsConnectionsLoading || (canWrite && connectionsQuery.loading && !connections),
    selectedConnectionId: connection?.id ?? null,
    onSelectConnection: (id) => {
      if (!busy) setSelected(id || null);
    },
    parentZones,
    zonesLoading: !!connection && zonesQuery.loading,
    zonesError:
      zonesQuery.error?.message ??
      (zones && !zones.complete ? zones.reason || t("uncertain") : null),
    onGrant: (zone, token) => {
      const target = connection;
      if (!target || !token) return Promise.resolve(false);
      return send(
        async () =>
          (
            await client.mutate<
              GrantCloudflareDelegationMutation,
              GrantCloudflareDelegationMutationVariables
            >({
              mutation: GRANT_CLOUDFLARE_DELEGATION,
              variables: {
                input: {
                  connectionId: target.id,
                  expectedVersion: target.version,
                  zoneId: zone.id,
                  zoneName: zone.name,
                  token,
                },
              },
              context,
            })
          ).data?.grantCloudflareDelegation,
        t("granted", { parent: zone.name })
      );
    },
    onBind: (target) =>
      send(
        async () =>
          (
            await client.mutate<
              BindManagedDomainDelegationMutation,
              BindManagedDomainDelegationMutationVariables
            >({
              mutation: BIND_MANAGED_DOMAIN_DELEGATION,
              variables: {
                input: {
                  domainId: current.id,
                  expectedDomainVersion: current.version,
                  connectionId: target.id,
                  expectedConnectionVersion: target.version,
                },
              },
              context,
            })
          ).data?.bindManagedDomainDelegation,
        t("boundMessage", { parent: target.delegationZoneName })
      ),
  };
}
