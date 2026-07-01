"use client";

import { useQuery } from "@apollo/client/react";
import { CheckCircle2Icon, CircleDashedIcon, LoaderCircleIcon } from "lucide-react";

import { GET_APP } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  ProvisioningProgress,
} from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

const STEP_LABELS: Record<string, string> = {
  registry: "Container registry",
  namespace: "Kubernetes namespace",
  identity: "Cloud identity (IRSA)",
  managed_services: "Managed services",
  ready: "Mark ready",
};

function StepIcon({ state }: { state: "done" | "active" | "pending" }) {
  if (state === "done") return <CheckCircle2Icon className="size-4 text-success-fg" />;
  if (state === "active")
    return (
      <LoaderCircleIcon className="size-4 animate-spin text-[color:var(--brand-primary)]" />
    );
  return <CircleDashedIcon className="text-muted-foreground/40 size-4" />;
}

export function ProvisioningProgressPanel({ app }: { app: AstroliftRegisteredApp }) {
  const isProvisioning = app.provisioningStatus === "provisioning";

  const { data } = useQuery<{ astroliftApp: AstroliftRegisteredApp }>(GET_APP, {
    variables: { slug: app.slug, includeDrift: false },
    skip: !isProvisioning,
    pollInterval: 3000,
    fetchPolicy: "network-only",
  });

  if (!isProvisioning) return null;

  const liveApp = data?.astroliftApp ?? app;
  const progress: ProvisioningProgress | null | undefined = liveApp.provisioningProgress;
  const completed = new Set(progress?.completed ?? []);
  const steps = progress?.totalSteps ?? ["registry", "namespace", "ready"];
  const currentStep = progress?.currentStep ?? "provisioning";

  const activeStep = steps.find((s) => !completed.has(s)) ?? null;

  return (
    <div className="border-border bg-muted/20 rounded-lg border p-4">
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
                      : "text-muted-foreground",
                )}
              >
                {STEP_LABELS[step] ?? step}
              </span>
              {isDone && <span className="text-muted-foreground ml-auto text-xs">done</span>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
