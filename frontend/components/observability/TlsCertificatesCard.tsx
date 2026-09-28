"use client";

/**
 * TlsCertificatesCard — operator-facing TLS read panel for the app
 * detail observability subroute (#377). Lists certs the cluster's
 * TlsDriver knows about for this app, with an expiry chip
 * (red < 30 days, amber < 90, otherwise muted) and renewal-status
 * badge.
 */

import { RefreshCwIcon, RotateCcwIcon, ShieldCheckIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import {
  type ObservabilityPanelReason,
  panelEmptyState,
} from "@/components/observability/panel-reason";
import { Badge } from "@/components/ui/badge";
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
import type {
  AstroliftAppCertificate,
  CertificateRenewalStatus,
} from "@/graphql/lifecycle/lifecycle.types";

export interface TlsCertificatesCardData {
  astroliftAppCertificates: {
    reason: ObservabilityPanelReason;
    certificates: AstroliftAppCertificate[];
  };
}

const RENEWAL_TONE: Record<
  CertificateRenewalStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  auto: "secondary",
  manual: "outline",
  failed: "destructive",
  unknown: "outline",
};

const RENEWAL_LABEL: Record<CertificateRenewalStatus, string> = {
  auto: "Auto-renew",
  manual: "Manual",
  failed: "Renewal failed",
  unknown: "Unknown",
};

function expiryChipClass(days: number): string {
  // < 30 days = red, < 90 = amber, else muted. Operators have to act
  // on red within the rotation window; amber is a heads-up.
  if (days < 30) {
    return "bg-destructive/10 text-destructive border-destructive/30";
  }
  if (days < 90) {
    return "bg-warning/10 text-warning-fg border-warning-border";
  }
  return "bg-muted text-muted-foreground border-transparent";
}

function formatDays(days: number): string {
  if (days <= 0) return "expired";
  if (days === 1) return "1 day";
  return `${days} days`;
}

function formatNotAfter(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 10);
}

export interface TlsCertificatesCardProps {
  appSlug: string;
}

/** Pure (Storybook first): the data comes from useTlsCertificates. */
export function TlsCertificatesCard({
  appSlug,
  data,
  loading,
  onRefresh,
}: TlsCertificatesCardProps & {
  data: TlsCertificatesCardData | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  const certs = data?.astroliftAppCertificates?.certificates ?? [];
  const reason = data?.astroliftAppCertificates?.reason;
  const isEmptyAfterLoad = !loading && certs.length === 0;
  const empty = panelEmptyState(reason ?? "NO_DATA_YET", {
    thing: "TLS certificates",
    notConfigured:
      "No public hostname or TLS driver is wired for this app. Set up a managed domain to issue a cert.",
    provider: "this cloud",
  });
  const showIssueAction = reason === "NOT_CONFIGURED" || reason === "NO_DATA_YET" || reason == null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheckIcon className="size-4" /> TLS certificates
          </CardTitle>
          <CardDescription>
            Certs the cluster&apos;s TLS driver knows about, filtered by this app&apos;s slug.
            Days-until-expiry are colour-coded: red &lt; 30, amber &lt; 90.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              onRefresh();
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
                // Force re-bind = re-issue + re-bind; for ACM this is
                // a per-cert rotate. The rotate mutation lives on the
                // domain-handshake side (#397) per cert; surface the
                // pointer here until we have a generic rotate-all.
                toast.message("Force re-bind", {
                  description:
                    "Cert re-issue is per-hostname — open the Domains tab and use the cert-rotate action on the affected entry.",
                });
              }}
            >
              <RotateCcwIcon className="size-3" /> Force re-bind
            </Button>
          </Can>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {loading && certs.length === 0 ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : isEmptyAfterLoad ? (
          <div className="p-6">
            <EmptyState
              icon={<ShieldCheckIcon className="size-5" />}
              title={empty.title}
              description={empty.description}
              actionHref={showIssueAction ? `/apps/${appSlug}/domains` : undefined}
              actionLabel={showIssueAction ? "Issue cert" : undefined}
              secondary={
                reason === "ERROR" ? (
                  <Button size="sm" variant="outline" onClick={() => onRefresh()}>
                    Try again
                  </Button>
                ) : undefined
              }
            />
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Hostname</TableHead>
                <TableHead>Issuer</TableHead>
                <TableHead>Not after</TableHead>
                <TableHead>Expires in</TableHead>
                <TableHead>Renewal</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {certs.map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="font-mono text-xs">{c.hostname}</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-xs">
                    {c.issuer || "—"}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{formatNotAfter(c.notAfter)}</TableCell>
                  <TableCell>
                    <span
                      className={`text-2xs inline-flex items-center rounded border px-2 py-0.5 font-mono ${expiryChipClass(c.daysUntilExpiry)}`}
                    >
                      {formatDays(c.daysUntilExpiry)}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant={RENEWAL_TONE[c.renewalStatus]} className="font-mono text-xs">
                      {RENEWAL_LABEL[c.renewalStatus]}
                    </Badge>
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
