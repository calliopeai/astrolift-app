"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { ActivityIcon, GaugeIcon, ScalingIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SCALE_WORKLOAD } from "@/graphql/lifecycle/lifecycle.mutations";
import { GET_WORKLOAD_SCALING_STATUS } from "@/graphql/registry/registry.queries";
import { cn } from "@/lib/utils";

// 5s poll matches the issue spec — short enough to make the
// 'Scaling…' indicator feel live, long enough that we're not
// hammering kube-apiserver via the get_workload_status driver call.
const SCALING_POLL_MS = 5_000;

interface ScalingStatus {
  hpaEnabled: boolean;
  hpaMinReplicas: number | null;
  hpaMaxReplicas: number | null;
  hpaTargetCpuPct: number;
  currentReplicas: number;
  desiredReplicas: number;
  isScaling: boolean;
  replicaLowerBound: number;
  replicaUpperBound: number;
  sourcedAt: string;
}

interface ScalingResp {
  astroliftWorkloadScalingStatus: ScalingStatus | null;
}

interface ScaleMutationResp {
  scaleAstroliftWorkload: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field: string | null }>;
    data: {
      workloadId: string;
      desiredReplicas: number | null;
      readyReplicas: number | null;
    } | null;
  };
}

export interface ScalingCardLabels {
  title: string;
  manualHeading: string;
  manualDescription: string;
  currentReplicas: string;
  desiredReplicas: string;
  readyReplicas: string;
  hpaHeading: string;
  hpaEnabledLabel: string;
  hpaDisabledLabel: string;
  hpaDescription: string;
  hpaMin: string;
  hpaMax: string;
  hpaTarget: string;
  hpaCurrent: string;
  hpaLastScaleAt: string;
  scalingUp: string;
  scalingDown: string;
  applyButton: string;
  applyingButton: string;
  successToast: string;
  errorToast: string;
  permissionDenied: string;
}

export function ScalingCard({
  workloadId,
  appSlug,
  workloadSlug,
  environmentName,
  canDeploy,
  labels,
}: {
  workloadId: string;
  appSlug: string;
  workloadSlug: string;
  environmentName: string | null;
  canDeploy: boolean;
  labels: ScalingCardLabels;
}) {
  const { data, loading } = useQuery<ScalingResp>(GET_WORKLOAD_SCALING_STATUS, {
    variables: { appSlug, workloadSlug, environmentName },
    pollInterval: SCALING_POLL_MS,
    fetchPolicy: "cache-and-network",
  });

  const [scale, { loading: scaling }] = useMutation<ScaleMutationResp>(SCALE_WORKLOAD, {
    refetchQueries: [
      {
        query: GET_WORKLOAD_SCALING_STATUS,
        variables: { appSlug, workloadSlug, environmentName },
      },
    ],
    awaitRefetchQueries: true,
  });

  const status = data?.astroliftWorkloadScalingStatus ?? null;

  // Pending replicas — what the slider currently shows. We track the
  // last server-side ``desired`` we observed and reset the slider when
  // it changes (HPA reconciled, sibling tab scaled). Pattern matches
  // React's "deriving state on prop change" recipe — preferable to
  // setState-in-effect.
  const desiredFromServer = status?.desiredReplicas ?? 1;
  const [lastSeenDesired, setLastSeenDesired] = React.useState<number>(
    desiredFromServer,
  );
  const [pending, setPending] = React.useState<number>(desiredFromServer);
  if (lastSeenDesired !== desiredFromServer) {
    setLastSeenDesired(desiredFromServer);
    setPending(desiredFromServer);
  }

  if (loading && !status) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{labels.title}</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }
  if (!status) {
    // Resolver returned null = workload doesn't exist for the
    // viewer. The parent already renders the not-found page in that
    // case, so this branch is defensive only.
    return null;
  }

  const dirty = pending !== status.desiredReplicas;
  const lower = status.replicaLowerBound;
  const upper = status.replicaUpperBound;

  async function handleApply() {
    if (!canDeploy) return;
    const { data: resp } = await scale({
      variables: { input: { workloadId, replicas: pending } },
    });
    const payload = resp?.scaleAstroliftWorkload;
    if (payload?.ok) {
      const requested = payload.data?.desiredReplicas ?? pending;
      toast.success(labels.successToast.replace("{replicas}", String(requested)));
    } else {
      const msg = payload?.errors?.[0]?.message ?? "unknown error";
      toast.error(labels.errorToast.replace("{message}", msg));
      // Revert local slider so the displayed value matches reality
      // after a server-side rejection (e.g. bounds violation when
      // the env policy tightened the cap).
      setPending(status?.desiredReplicas ?? 1);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <ScalingIcon className="size-4" />
          {labels.title}
          {status.isScaling && (
            <Badge className="gap-1 bg-warning/15 text-warning-fg">
              <ActivityIcon className="size-3 animate-pulse" />
              {pending > status.currentReplicas ? labels.scalingUp : labels.scalingDown}
            </Badge>
          )}
          {status.hpaEnabled && (
            <Badge
              variant="outline"
              className="gap-1 border-success-border text-success-fg"
            >
              <GaugeIcon className="size-3" />
              {labels.hpaEnabledLabel}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <ManualScaleSection
          status={status}
          pending={pending}
          setPending={setPending}
          lower={lower}
          upper={upper}
          dirty={dirty}
          scaling={scaling}
          canDeploy={canDeploy}
          onApply={handleApply}
          labels={labels}
        />
        {status.hpaEnabled && <HpaGaugeSection status={status} labels={labels} />}
      </CardContent>
    </Card>
  );
}

function ManualScaleSection({
  status,
  pending,
  setPending,
  lower,
  upper,
  dirty,
  scaling,
  canDeploy,
  onApply,
  labels,
}: {
  status: ScalingStatus;
  pending: number;
  setPending: (v: number) => void;
  lower: number;
  upper: number;
  dirty: boolean;
  scaling: boolean;
  canDeploy: boolean;
  onApply: () => void;
  labels: ScalingCardLabels;
}) {
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{labels.manualHeading}</h4>
        <div className="text-muted-foreground flex items-center gap-4 text-xs">
          <span>
            <span className="tracking-wide uppercase">{labels.currentReplicas}</span>
            <span className="ml-1 font-mono tabular-nums">{status.currentReplicas}</span>
          </span>
          <span>
            <span className="tracking-wide uppercase">{labels.desiredReplicas}</span>
            <span className="ml-1 font-mono tabular-nums">{status.desiredReplicas}</span>
          </span>
        </div>
      </div>
      <p className="text-muted-foreground mb-3 text-xs">{labels.manualDescription}</p>
      <div className="flex items-center gap-3">
        <span className="text-muted-foreground font-mono text-xs tabular-nums">{lower}</span>
        <input
          type="range"
          min={lower}
          max={upper}
          step={1}
          value={pending}
          onChange={(e) => setPending(Number(e.target.value))}
          disabled={!canDeploy || scaling}
          className={cn(
            "h-2 w-full cursor-pointer appearance-none rounded-full",
            "bg-muted",
            "[&::-webkit-slider-thumb]:appearance-none",
            "[&::-webkit-slider-thumb]:size-4",
            "[&::-webkit-slider-thumb]:rounded-full",
            "[&::-webkit-slider-thumb]:bg-primary",
            "[&::-moz-range-thumb]:size-4",
            "[&::-moz-range-thumb]:rounded-full",
            "[&::-moz-range-thumb]:border-0",
            "[&::-moz-range-thumb]:bg-primary",
            "disabled:cursor-not-allowed",
            "disabled:opacity-50"
          )}
          aria-label={labels.manualHeading}
        />
        <span className="text-muted-foreground font-mono text-xs tabular-nums">{upper}</span>
        <span className="min-w-[3rem] text-right font-mono text-lg tabular-nums">{pending}</span>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        {!canDeploy && (
          <span className="text-muted-foreground text-xs">{labels.permissionDenied}</span>
        )}
        <Button size="sm" onClick={onApply} disabled={!canDeploy || !dirty || scaling}>
          {scaling ? labels.applyingButton : labels.applyButton}
        </Button>
      </div>
    </div>
  );
}

function HpaGaugeSection({ status, labels }: { status: ScalingStatus; labels: ScalingCardLabels }) {
  // The HPA bar visualises the current replica count along the
  // [min..max] range — pinned at min when scaled down, pinned at max
  // when saturated. Useful for the operator to see "I'm two replicas
  // from the ceiling" in one glance.
  const min = status.hpaMinReplicas ?? 0;
  const max = status.hpaMaxReplicas ?? 0;
  const range = Math.max(0, max - min);
  const offset = Math.max(0, status.currentReplicas - min);
  const percent = range > 0 ? Math.min(100, (offset / range) * 100) : 0;
  // Saturation colouring: red when at the max replica ceiling (HPA
  // has nothing more to give); amber when within one replica; else
  // green.
  const atCeiling = status.currentReplicas >= max && max > 0;
  const nearCeiling = !atCeiling && status.currentReplicas >= max - 1;
  const color = atCeiling ? "bg-danger" : nearCeiling ? "bg-warning" : "bg-success";

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{labels.hpaHeading}</h4>
        <span className="text-muted-foreground font-mono text-xs tabular-nums">
          {status.currentReplicas} / {labels.hpaMin} {min} / {labels.hpaMax} {max} @{" "}
          {status.hpaTargetCpuPct}% {labels.hpaTarget}
        </span>
      </div>
      <p className="text-muted-foreground mb-3 text-xs">{labels.hpaDescription}</p>
      <div className="bg-muted h-3 w-full overflow-hidden rounded-full">
        <div className={cn("h-full transition-all", color)} style={{ width: `${percent}%` }} />
      </div>
      <div className="text-muted-foreground mt-2 flex justify-between text-xs">
        <span className="font-mono">{min}</span>
        <span className="font-mono">
          {labels.hpaCurrent}: {status.currentReplicas}
        </span>
        <span className="font-mono">{max}</span>
      </div>
    </div>
  );
}
