"use client";

import { CheckCircle2Icon, CircleDashedIcon, LoaderCircleIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import { Notice } from "./OverviewNotices";
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
  if (state === "done") return <CheckCircle2Icon className="text-success-fg size-3.5" />;
  if (state === "active")
    return <LoaderCircleIcon className="text-primary size-3.5 animate-spin" />;
  return <CircleDashedIcon className="text-muted-foreground/40 size-3.5" />;
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
    <Notice
      tone="progress"
      icon={LoaderCircleIcon}
      spin
      title="Provisioning infrastructure"
      description={<span className="font-mono">{currentStep}</span>}
    >
      <ol className="flex flex-wrap gap-x-4 gap-y-1.5">
        {steps.map((step) => {
          const isDone = completed.has(step);
          const isActive = step === activeStep && !isDone;
          return (
            <li key={step} className="flex items-center gap-1.5">
              <StepIcon state={isDone ? "done" : isActive ? "active" : "pending"} />
              <span
                className={cn(isDone || isActive ? "text-foreground" : "text-muted-foreground")}
              >
                {STEP_LABELS[step] ?? step}
              </span>
              {isDone && <span className="sr-only">done</span>}
            </li>
          );
        })}
      </ol>
    </Notice>
  );
}
