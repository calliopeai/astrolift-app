"use client";

import { ArrowLeftIcon, ArrowRightIcon, CheckIcon, Loader2Icon, XIcon } from "lucide-react";
import * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { appsCrumbs } from "../list/apps-list";

export type NewAppStep = 1 | 2 | 3;

/** The three steps (spec 44 §5.4): where the code is, how it runs, then what will deploy. */
export const NEW_APP_STEPS: { n: NewAppStep; label: string; description: string }[] = [
  {
    n: 1,
    label: "Source",
    description: "The repository, and the astrolift.toml manifest that says what it runs.",
  },
  {
    n: 2,
    label: "Run",
    description: "The app's name and project, then how and when it deploys.",
  },
  {
    n: 3,
    label: "Review",
    description: "What will be created and what will deploy. Nothing happens until you create it.",
  },
];

export interface NewAppPageProps {
  step: NewAppStep;
  /** Jump back to an earlier step; later steps are reached with Continue. */
  onStep?: (step: NewAppStep) => void;
  /** Absent on the first step, and while the create runs. */
  onBack?: () => void;
  onContinue: () => void;
  /** "Continue", or "Create app" on Review. */
  continueLabel?: string;
  /** The step still has a field to fix; each field says what, beside itself. */
  continueDisabled?: boolean;
  /** The create is running. */
  busy?: boolean;
  /** Absent while the create runs. */
  onCancel?: () => void;
  children: React.ReactNode;
}

/**
 * Apps › New app (spec 44 §5.4): a page in three numbered steps, since it asks
 * for far more than three fields. Each step's fields carry their own errors in
 * place; the only toast is the create's outcome, which the route's hook owns.
 * Pure chrome: the step bodies are the existing step views, passed as children.
 */
export function NewAppPage({
  step,
  onStep,
  onBack,
  onContinue,
  continueLabel = "Continue",
  continueDisabled = false,
  busy = false,
  onCancel,
  children,
}: NewAppPageProps) {
  const current = NEW_APP_STEPS[step - 1];
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader
        crumbs={appsCrumbs({ label: "New app" })}
        title="New app"
        context={
          <ol aria-label="Steps" className="inline-flex flex-wrap items-center gap-2">
            {NEW_APP_STEPS.map((s, i) => {
              const done = s.n < step;
              const label = (
                <>
                  <span className="font-mono">{s.n}</span> {s.label}
                </>
              );
              return (
                <li
                  key={s.n}
                  aria-current={s.n === step ? "step" : undefined}
                  className={cn(
                    "inline-flex items-center gap-1.5",
                    s.n === step ? "text-foreground font-medium" : "text-muted-foreground"
                  )}
                >
                  {i > 0 && <span aria-hidden>·</span>}
                  {done && onStep && !busy ? (
                    <button
                      type="button"
                      onClick={() => onStep(s.n)}
                      className="hover:text-foreground focus-visible:ring-ring inline-flex items-center gap-1 rounded-sm focus-visible:ring-2 focus-visible:outline-none"
                    >
                      {label}
                      <CheckIcon className="size-3.5" aria-label="done" />
                    </button>
                  ) : (
                    <span>{label}</span>
                  )}
                </li>
              );
            })}
          </ol>
        }
        primaryAction={
          onCancel ? (
            <Button variant="ghost" size="sm" onClick={onCancel}>
              <XIcon className="size-4" />
              Cancel
            </Button>
          ) : undefined
        }
      />

      <section
        aria-labelledby="new-app-step"
        className="bg-card flex max-w-5xl min-w-0 flex-col gap-6 rounded-md border p-6"
      >
        <div className="min-w-0">
          <h2 id="new-app-step" className="text-lg font-semibold">
            {current.label}
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">{current.description}</p>
        </div>
        {children}
      </section>

      <div className="flex max-w-5xl min-w-0 items-center justify-between gap-2">
        <Button type="button" variant="ghost" onClick={onBack} disabled={!onBack}>
          <ArrowLeftIcon className="size-4" />
          Back
        </Button>
        <Button type="button" onClick={onContinue} disabled={continueDisabled || busy}>
          {busy && <Loader2Icon className="size-4 animate-spin" />}
          {continueLabel}
          {!busy && step < 3 && <ArrowRightIcon className="size-4" />}
        </Button>
      </div>
    </div>
  );
}

/** One part of a step (Repository, Manifest; App, Deploy strategy), with its heading. */
export function NewAppSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex min-w-0 flex-col gap-4 border-t pt-6">
      <div className="min-w-0">
        <h3 className="font-medium">{title}</h3>
        {description && <p className="text-muted-foreground text-xs">{description}</p>}
      </div>
      {children}
    </section>
  );
}
