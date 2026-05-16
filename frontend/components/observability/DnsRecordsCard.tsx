"use client";

/**
 * DnsRecordsCard — operator-facing DNS read panel for the app detail
 * observability subroute (#377). Shows live A / CNAME / TXT records
 * sourced from the cluster's DnsDriver, with a propagation status
 * dot per row.
 *
 * Empty state covers two scenarios with the same UX: no records yet
 * AND "this cloud doesn't implement list_records yet" — the backend
 * resolver degrades both to an empty list, so the card surfaces the
 * deep-link to "Set up DNS" rather than splitting the UI.
 */

import { useQuery } from "@apollo/client/react";
import { GlobeIcon, RefreshCwIcon, RotateCcwIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_APP_DNS_RECORDS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppDnsRecord,
  DnsPropagationStatus,
} from "@/graphql/lifecycle/lifecycle.types";

interface Resp {
  astroliftAppDnsRecords: AstroliftAppDnsRecord[];
}

const PROPAGATION_DOT: Record<DnsPropagationStatus, "ok" | "warn" | "muted"> = {
  propagated: "ok",
  pending: "warn",
  unknown: "muted",
};

const PROPAGATION_LABEL: Record<DnsPropagationStatus, string> = {
  propagated: "Propagated",
  pending: "Pending",
  unknown: "Unknown",
};

export interface DnsRecordsCardProps {
  appSlug: string;
  /** Optional — names a specific environment's cluster to query. */
  environmentName?: string;
}

export function DnsRecordsCard({ appSlug, environmentName }: DnsRecordsCardProps) {
  const { data, loading, refetch } = useQuery<Resp>(LIST_APP_DNS_RECORDS, {
    variables: { appSlug, environmentName: environmentName ?? null },
    fetchPolicy: "cache-and-network",
    notifyOnNetworkStatusChange: true,
  });

  const records = data?.astroliftAppDnsRecords ?? [];
  const isEmptyAfterLoad = !loading && records.length === 0;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <GlobeIcon className="size-4" /> DNS records
          </CardTitle>
          <CardDescription>
            Live records from the cluster&apos;s DNS driver. Propagation reflects the most recent
            change set; use <code>dig</code> for per-vantage-point checks.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              void refetch();
            }}
            disabled={loading}
          >
            <RefreshCwIcon className="size-3" /> Refresh
          </Button>
          <Can permission="app.update">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                // Force re-bind — the platform re-applies the manifest
                // annotation that triggers external-dns / route53
                // controller. The mutation surface lives on the
                // domain-handshake side (#397); until that's wired into
                // a generic "rebind DNS for app", this surfaces a toast
                // pointing operators at the per-domain re-validate path.
                toast.message("Force re-bind", {
                  description:
                    "Re-applying DNS records is per-domain — head to the Domains tab and click Re-validate on the affected hostname.",
                });
              }}
            >
              <RotateCcwIcon className="size-3" /> Force re-bind
            </Button>
          </Can>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {loading && records.length === 0 ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : isEmptyAfterLoad ? (
          <div className="p-6">
            <EmptyState
              icon={<GlobeIcon className="size-5" />}
              title="No DNS records for this app yet"
              description="Either DNS isn't configured, or this cluster's provider plugin doesn't yet implement live record reads. Either way, set up DNS to get started."
              actionHref={`/apps/${appSlug}/domains`}
              actionLabel="Set up DNS"
            />
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Value</TableHead>
                <TableHead className="text-right">TTL</TableHead>
                <TableHead>Propagation</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((r, i) => (
                <TableRow key={`${r.name}-${r.type}-${r.value}-${i}`}>
                  <TableCell className="font-mono text-xs">{r.name}</TableCell>
                  <TableCell className="font-mono text-xs">{r.type}</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-xs">
                    {r.value}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">{r.ttl}</TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-2 text-xs">
                      <StatusDot status={PROPAGATION_DOT[r.propagationStatus]} />
                      <span>{PROPAGATION_LABEL[r.propagationStatus]}</span>
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
