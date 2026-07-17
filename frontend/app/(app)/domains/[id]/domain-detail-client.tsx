"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/clusters.queries";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftManagedDomains: AstroliftManagedDomain[];
}

/**
 * Managed domain detail (#1106). Reuses LIST_MANAGED_DOMAINS (no singular
 * query exists). A managed domain is an org-level DNS *zone*; per-app DNS
 * records + certificate status live on each app's Domains tab (the app-scoped
 * astroliftAppDomains/astroliftAppCertificates queries are keyed by appSlug,
 * not by zone), so those are intentionally out of scope here.
 */
export function DomainDetailClient({ id }: { id: string }) {
  const { data, loading } = useQuery<Resp>(LIST_MANAGED_DOMAINS, {
    fetchPolicy: "cache-and-network",
  });

  const d = React.useMemo(
    () => (data?.astroliftManagedDomains ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!d}
      breadcrumb={{ label: "Managed domains", href: "/domains" }}
      heading={d ? d.zone : `Domain ${id.slice(0, 8)}`}
      createdAt={d?.createdAt}
      notFoundLabel="managed domain"
      overview={
        d
          ? [
              { term: "Zone", description: <span className="font-mono text-sm">{d.zone}</span> },
              { term: "DNS driver", description: <Badge variant="outline">{d.dnsDriver}</Badge> },
              {
                term: "Default for",
                description: (
                  <Badge variant="secondary" className="capitalize">
                    {d.defaultFor.replace(/_/g, " ")}
                  </Badge>
                ),
              },
              { term: "Wildcard managed", description: d.isWildcardManaged ? "Yes" : "No" },
              { term: "Organization", description: <span className="font-mono text-xs">{d.organizationSlug}</span> },
              { term: "Created", description: <DetailTimestamp iso={d.createdAt} /> },
            ]
          : []
      }
    >
      {d ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">DNS records &amp; certificates</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Tenant apps and preview environments under{" "}
              <span className="font-mono text-xs">{d.zone}</span> get their DNS records and TLS
              certificates created and renewed automatically by the{" "}
              <span className="font-mono text-xs">{d.dnsDriver}</span> driver. Per-hostname record and
              certificate status is shown on each app&rsquo;s Domains tab.
            </p>
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
