"use client";

import { CheckCircle2Icon, CircleDashedIcon, LoaderCircleIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

import type { useProvisioningProgress } from "./use-provisioning-progress";

export type ProvisioningProgressViewProps = ReturnType<typeof useProvisioningProgress>;

const STEP_LABELS: Record<string, string> = {
  registry: "Container registry",
  namespace: "Kubernetes namespace",
  identity: "Cloud identity (IRSA)",
  managed_services: "Managed services",
  ready: "Mark ready",
};

function StepIcon({ state }: { state: "done" | "active" | "pending" }) {
  if (state === "done") return <CheckCircle2Icon className="text-success-fg size-4" />;
  if (state === "active")
    return <LoaderCircleIcon className="size-4 animate-spin text-[color:var(--brand-primary)]" />;
  return <CircleDashedIcon className="text-muted-foreground/40 size-4" />;
}

/** Live step list while an app provisions; renders nothing otherwise. */
export function ProvisioningProgressView({
  isProvisioning,
  progress,
}: ProvisioningProgressViewProps) {
  if (!isProvisioning) return null;

  const completed = new Set(progress?.completed ?? []);
  const steps = progress?.totalSteps ?? ["registry", "namespace", "ready"];
  const currentStep = progress?.currentStep ?? "provisioning";

  const activeStep = steps.find((s) => !completed.has(s)) ?? null;

  return (
    <Card>
      <CardContent>
        <div className="mb-3 flex items-center gap-2">
          <LoaderCircleIcon className="size-4 animate-spin text-[color:var(--brand-primary)]" />
          <span className="text-sm font-medium">Provisioning infrastructure…</span>
          <span className="text-muted-foreground ml-auto font-mono text-xs">{currentStep}</span>
        </div>
        <ol className="space-y-2">
          {steps.map((step) => {
            const isDone = completed.has(step);
            const isActive = step === activeStep && !isDone;
            return (
              <li key={step} className="flex items-center gap-2.5 text-sm">
                <StepIcon state={isDone ? "done" : isActive ? "active" : "pending"} />
                <span
                  className={cn(
                    isDone
                      ? "text-foreground"
                      : isActive
                        ? "text-foreground"
                        : "text-muted-foreground"
                  )}
                >
                  {STEP_LABELS[step] ?? step}
                </span>
                {isDone && <span className="text-muted-foreground ml-auto text-xs">done</span>}
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
