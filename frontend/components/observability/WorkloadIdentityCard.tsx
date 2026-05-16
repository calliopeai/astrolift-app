"use client";

/**
 * WorkloadIdentityCard — operator-facing IRSA / Workload Identity /
 * Federated Identity binding panel for the app detail observability
 * subroute (#377).
 *
 * Shows the role ARN (or principal), the trust-policy summary line,
 * and the cloud's last-used timestamp when available. Empty state
 * covers both "no binding" and "this cloud doesn't yet implement
 * the read" with the same UX.
 */

import { useQuery } from "@apollo/client/react";
import { KeyRoundIcon, RefreshCwIcon, RotateCcwIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_APP_IDENTITY_BINDING } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppIdentityBinding,
  IdentityBindingKind,
} from "@/graphql/lifecycle/lifecycle.types";

interface Resp {
  astroliftAppIdentityBinding: AstroliftAppIdentityBinding | null;
}

const KIND_LABEL: Record<IdentityBindingKind, string> = {
  irsa: "IAM Roles for Service Accounts (AWS)",
  workload_identity: "GKE Workload Identity (GCP)",
  federated: "AKS Federated Credentials (Azure)",
  unknown: "Unknown binding",
};

function formatLastUsed(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export interface WorkloadIdentityCardProps {
  appSlug: string;
  environmentName?: string;
}

export function WorkloadIdentityCard({ appSlug, environmentName }: WorkloadIdentityCardProps) {
  const { data, loading, refetch } = useQuery<Resp>(GET_APP_IDENTITY_BINDING, {
    variables: { appSlug, environmentName: environmentName ?? null },
    fetchPolicy: "cache-and-network",
    notifyOnNetworkStatusChange: true,
  });

  const binding = data?.astroliftAppIdentityBinding ?? null;
  const isEmptyAfterLoad = !loading && binding === null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRoundIcon className="size-4" /> Workload identity
          </CardTitle>
          <CardDescription>
            The cloud-IAM identity bound to this app&apos;s pods. Source: cluster&apos;s
            WorkloadIdentityDriver ({" "}
            <code className="font-mono text-[11px]">describe_identity</code> ).
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
                // Force re-bind = re-apply the SA annotation that the
                // controller (eks-pod-identity, gke-wi, aks-fic) reads.
                // We don't yet have a generic "rebind WI" mutation in
                // the lifecycle surface; surface intent via a toast so
                // operators know the next deploy will re-stamp the
                // annotation.
                toast.message("Force re-bind", {
                  description:
                    "The next deployment will re-apply the ServiceAccount annotation. Trigger a redeploy from the Deployments tab.",
                });
              }}
            >
              <RotateCcwIcon className="size-3" /> Force re-bind
            </Button>
          </Can>
        </div>
      </CardHeader>
      <CardContent>
        {loading && binding === null ? (
          <div className="space-y-2">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-1/2" />
          </div>
        ) : isEmptyAfterLoad ? (
          <EmptyState
            icon={<KeyRoundIcon className="size-5" />}
            title="No workload identity binding yet"
            description="Either no role is provisioned for this app, or this cluster's provider plugin doesn't yet implement live identity reads. Configure WI to grant pods cloud-IAM credentials."
            actionHref={`/apps/${appSlug}/security`}
            actionLabel="Configure workload identity"
          />
        ) : binding ? (
          <dl className="grid grid-cols-1 gap-4 text-sm md:grid-cols-2">
            <div>
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">Kind</dt>
              <dd className="mt-1">
                <Badge variant="secondary" className="font-mono text-xs">
                  {KIND_LABEL[binding.kind]}
                </Badge>
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">Last used</dt>
              <dd className="text-muted-foreground mt-1 font-mono text-xs">
                {formatLastUsed(binding.lastUsedAt)}
              </dd>
            </div>
            <div className="md:col-span-2">
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">
                Role / principal
              </dt>
              <dd className="mt-1 font-mono text-xs break-all">{binding.roleArnOrPrincipal}</dd>
            </div>
            <div className="md:col-span-2">
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">
                Trust policy
              </dt>
              <dd className="text-muted-foreground mt-1 font-mono text-xs">
                {binding.trustPolicySummary}
              </dd>
            </div>
          </dl>
        ) : null}
      </CardContent>
    </Card>
  );
}
