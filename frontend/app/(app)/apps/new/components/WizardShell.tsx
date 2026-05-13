"use client";

import { ArrowLeftIcon, ArrowRightIcon, CheckIcon, XIcon } from "lucide-react";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface WizardStep {
  key: string;
  label: string;
  description?: string;
}

interface WizardShellProps {
  step: number; // 1-indexed
  steps: WizardStep[];
  title?: string;
  description?: string;
  onStepClick?: (idx: number) => void;
  onBack?: () => void;
  onNext?: () => void;
  onCancel?: () => void;
  nextLabel?: string;
  nextDisabled?: boolean;
  nextLoading?: boolean;
  hideNext?: boolean;
  children: React.ReactNode;
}

/**
 * Five-step wizard chrome: title row, progress strip, step body, footer
 * with Back / Next. Steps that have already been visited are clickable
 * so the operator can jump back to a previous step.
 */
export function WizardShell({
  step,
  steps,
  title = "Register app",
  description = "Connect a Git repository. We'll fetch the manifest, register the app, and run the OnboardAppWorkflow.",
  onStepClick,
  onBack,
  onNext,
  onCancel,
  nextLabel = "Next",
  nextDisabled = false,
  nextLoading = false,
  hideNext = false,
  children,
}: WizardShellProps) {
  const current = steps[step - 1];

  return (
    <PageShell
      title={title}
      description={description}
      actions={
        onCancel ? (
          <Button variant="ghost" size="sm" onClick={onCancel}>
            <XIcon className="size-4" />
            Cancel
          </Button>
        ) : undefined
      }
    >
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-3">
        {steps.map((s, i) => {
          const idx = i + 1;
          const isDone = idx < step;
          const isActive = idx === step;
          const canJump = isDone && onStepClick != null;
          return (
            <li key={s.key} className="flex min-w-0 flex-1 items-center gap-2">
              <button
                type="button"
                onClick={canJump ? () => onStepClick?.(idx) : undefined}
                disabled={!canJump}
                className={cn(
                  "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium transition-colors",
                  isActive && "border-primary bg-primary text-primary-foreground",
                  isDone && "border-primary bg-primary text-primary-foreground",
                  !isActive && !isDone && "border-muted-foreground/30 text-muted-foreground",
                  canJump && "cursor-pointer hover:opacity-80"
                )}
                aria-current={isActive ? "step" : undefined}
                aria-label={`Step ${idx}: ${s.label}`}
              >
                {isDone ? <CheckIcon className="size-3.5" /> : idx}
              </button>
              <span
                className={cn(
                  "min-w-0 truncate text-xs",
                  isActive ? "text-foreground font-medium" : "text-muted-foreground"
                )}
              >
                {s.label}
              </span>
              {idx < steps.length && (
                <div
                  className={cn("h-px flex-1", isDone ? "bg-primary" : "bg-muted-foreground/30")}
                />
              )}
            </li>
          );
        })}
      </ol>

      <Card>
        <CardContent className="flex flex-col gap-6 p-6">
          <div>
            <h2 className="text-lg font-semibold">
              Step {step} of {steps.length} — {current?.label}
            </h2>
            {current?.description && (
              <p className="text-muted-foreground mt-1 text-sm">{current.description}</p>
            )}
          </div>
          {children}
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <Button type="button" variant="ghost" onClick={onBack} disabled={!onBack}>
          <ArrowLeftIcon className="size-4" />
          Back
        </Button>
        {!hideNext && (
          <Button type="button" onClick={onNext} disabled={nextDisabled || nextLoading || !onNext}>
            {nextLabel}
            {nextLoading ? null : <ArrowRightIcon className="size-4" />}
          </Button>
        )}
      </div>
    </PageShell>
  );
}
