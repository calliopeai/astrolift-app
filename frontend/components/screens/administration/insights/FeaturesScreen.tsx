"use client";

import { FlagIcon, Loader2Icon, LockIcon } from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { humanizeKey } from "./feature-key";
import type { RuntimeFlag, useFeatureFlags } from "./use-feature-flags";

export type FeaturesScreenProps = ReturnType<typeof useFeatureFlags>;

// Inline on/off switch — the repo has no shadcn/radix Switch primitive,
// so a single-use styled button role="switch" is the right size here.
function FlagSwitch({
  checked,
  disabled,
  pending,
  onToggle,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  pending?: boolean;
  onToggle?: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled || pending}
      onClick={onToggle}
      className={cn(
        "focus-visible:ring-ring relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        checked ? "bg-emerald-600" : "bg-muted-foreground/30",
        (disabled || pending) && "cursor-not-allowed opacity-50"
      )}
    >
      <span
        className={cn(
          "bg-background pointer-events-none inline-flex size-5 translate-x-0.5 items-center justify-center rounded-full shadow-sm transition-transform",
          checked && "translate-x-[22px]"
        )}
      >
        {pending && <Loader2Icon className="text-muted-foreground size-3 animate-spin" />}
      </span>
    </button>
  );
}

function FeatureRow({
  featureKey,
  description,
  enabled,
  meta,
  control,
}: {
  featureKey: string;
  description?: string | null;
  enabled: boolean;
  meta?: React.ReactNode;
  control: React.ReactNode;
}) {
  return (
    <div className="border-border/60 flex items-center gap-4 border-b py-3 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{humanizeKey(featureKey)}</span>
          <code className="text-muted-foreground bg-muted rounded px-1.5 py-0.5 text-xs">
            {featureKey}
          </code>
          {meta}
        </div>
        {description && <p className="text-muted-foreground mt-0.5 text-sm">{description}</p>}
      </div>
      <Badge variant={enabled ? "secondary" : "outline"} className="shrink-0">
        {enabled ? "On" : "Off"}
      </Badge>
      <div className="shrink-0">{control}</div>
    </div>
  );
}

function RowSkeletons() {
  return (
    <div className="flex flex-col gap-3 py-2">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-4">
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-3 w-72" />
          </div>
          <Skeleton className="h-6 w-11 rounded-full" />
        </div>
      ))}
    </div>
  );
}

/** Runtime feature flags (toggle with confirm) and read-only install-time features. */
export function FeaturesScreen({
  runtimeFlags,
  buildTimeFeatures,
  loading,
  pendingKey,
  toggleFlag,
}: FeaturesScreenProps) {
  // A runtime flag is install-wide and takes effect for everyone the moment
  // it flips, so the switch stages the change here and the dialog commits it.
  const [confirmFlag, setConfirmFlag] = React.useState<RuntimeFlag | null>(null);

  return (
    <PageShell
      title="Features"
      description="Toggle runtime feature flags for this install, and review the install-time features that require a redeploy to change."
    >
      <TooltipProvider>
        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FlagIcon className="size-4" /> Runtime flags
              </CardTitle>
              <CardDescription>
                Flip these live — the change takes effect immediately across the install. Backed by
                Constance; platform-admin only.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col">
              {loading && runtimeFlags.length === 0 ? (
                <RowSkeletons />
              ) : runtimeFlags.length === 0 ? (
                <p className="text-muted-foreground py-6 text-center text-sm">
                  No runtime feature flags are published on this install.
                </p>
              ) : (
                runtimeFlags.map((flag) => (
                  <FeatureRow
                    key={flag.key}
                    featureKey={flag.key}
                    description={flag.description}
                    enabled={flag.enabled}
                    control={
                      <FlagSwitch
                        checked={flag.enabled}
                        pending={pendingKey === flag.key}
                        disabled={pendingKey !== null && pendingKey !== flag.key}
                        onToggle={() => setConfirmFlag(flag)}
                        label={`Toggle ${flag.key}`}
                      />
                    }
                  />
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <LockIcon className="size-4" /> Install-time features
              </CardTitle>
              <CardDescription>
                These gate app loading at boot and cannot be flipped at runtime — change the
                environment variable and redeploy. Shown for visibility.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col">
              {loading && buildTimeFeatures.length === 0 ? (
                <RowSkeletons />
              ) : (
                buildTimeFeatures.map((feat) => (
                  <FeatureRow
                    key={feat.key}
                    featureKey={feat.key}
                    description={feat.description}
                    enabled={feat.enabled}
                    meta={
                      <Badge variant="outline" className="font-mono">
                        {feat.envVar}
                      </Badge>
                    }
                    control={
                      <Tooltip>
                        <TooltipTrigger asChild>
                          {/* span wrapper: a disabled button won't emit the
                              hover events radix Tooltip listens for. */}
                          <span className="inline-flex">
                            <FlagSwitch
                              checked={feat.enabled}
                              disabled
                              label={`${feat.key} (requires redeploy)`}
                            />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          Requires a redeploy — set {feat.envVar} and restart.
                        </TooltipContent>
                      </Tooltip>
                    }
                  />
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </TooltipProvider>

      <ConfirmDialog
        open={confirmFlag !== null}
        onOpenChange={(next) => {
          if (!next) setConfirmFlag(null);
        }}
        title={
          confirmFlag
            ? `${confirmFlag.enabled ? "Disable" : "Enable"} ${humanizeKey(confirmFlag.key)}?`
            : "Change feature flag?"
        }
        description={
          confirmFlag?.enabled
            ? "This takes effect immediately for everyone on this install. Turning the flag off can hide whole surfaces and remove entries from the sidebar. You can turn it back on here."
            : "This takes effect immediately for everyone on this install. Turning the flag on can expose new surfaces and add entries to the sidebar. You can turn it back off here."
        }
        confirmLabel={confirmFlag?.enabled ? "Disable flag" : "Enable flag"}
        destructive={confirmFlag?.enabled === true}
        onConfirm={async () => {
          if (confirmFlag) await toggleFlag(confirmFlag);
        }}
      />
    </PageShell>
  );
}
