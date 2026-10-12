"use client";

import {
  AlertCircleIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  Loader2Icon,
  XIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { agentsCrumbs } from "../list/agents-list";

export type NewAgentStep = 1 | 2 | 3;

/** The three steps (spec 44 §5.4): where the agents are, where they live, then what registers. */
export const NEW_AGENT_STEPS: { n: NewAgentStep; key: "source" | "configure" | "review" }[] = [
  { n: 1, key: "source" },
  { n: 2, key: "configure" },
  { n: 3, key: "review" },
];

export interface NewAgentPageProps {
  step: NewAgentStep;
  /** Jump back to an earlier step; later steps are reached with Continue. */
  onStep?: (step: NewAgentStep) => void;
  /** Absent on the first step, and while the register runs. */
  onBack?: () => void;
  /** Continue, or Register agents on Review. An invalid step shows its errors in place. */
  onContinue: () => void;
  continueLabel?: string;
  /** The register is running. */
  busy?: boolean;
  /** Absent while the register runs. */
  onCancel?: () => void;
  children: React.ReactNode;
}

/**
 * Agents › New agent (spec 44 §5.4): a page in three numbered steps. Each
 * step's parts say what is missing beside themselves once Continue is
 * pressed; the only toast is the register's outcome, which the hook owns.
 * Pure chrome: the step bodies are the step views, passed as children.
 */
export function NewAgentPage({
  step,
  onStep,
  onBack,
  onContinue,
  continueLabel,
  busy = false,
  onCancel,
  children,
}: NewAgentPageProps) {
  const t = useTranslations("agentWizard");
  const current = NEW_AGENT_STEPS[step - 1];
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader
        crumbs={agentsCrumbs("agents", [{ label: t("newAgent") }])}
        title={t("newAgent")}
        context={
          <ol aria-label={t("stepsLabel")} className="inline-flex flex-wrap items-center gap-2">
            {NEW_AGENT_STEPS.map((s, i) => {
              const label = (
                <>
                  <span className="font-mono">{s.n}</span> {t(`steps.${s.key}.label`)}
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
                  {s.n < step && onStep && !busy ? (
                    <button
                      type="button"
                      onClick={() => onStep(s.n)}
                      className="hover:text-foreground focus-visible:ring-ring inline-flex items-center gap-1 rounded-sm focus-visible:ring-2 focus-visible:outline-none"
                    >
                      {label}
                      <CheckIcon className="size-3.5" aria-label={t("done")} />
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
              {t("cancel")}
            </Button>
          ) : undefined
        }
      />

      <section
        aria-labelledby="new-agent-step"
        className="bg-card flex max-w-5xl min-w-0 flex-col gap-6 rounded-md border p-6"
      >
        <div className="min-w-0">
          <h2 id="new-agent-step" className="text-lg font-semibold">
            {t(`steps.${current.key}.label`)}
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            {t(`steps.${current.key}.description`)}
          </p>
        </div>
        {children}
      </section>

      <div className="flex max-w-5xl min-w-0 items-center justify-between gap-2">
        <Button type="button" variant="ghost" onClick={onBack} disabled={!onBack}>
          <ArrowLeftIcon className="size-4" />
          {t("back")}
        </Button>
        <Button type="button" onClick={onContinue} disabled={busy}>
          {busy && <Loader2Icon className="size-4 animate-spin" />}
          {continueLabel ?? t("continue")}
          {!busy && step < 3 && <ArrowRightIcon className="size-4" />}
        </Button>
      </div>
    </div>
  );
}

/** One part of a step (Repository, Agents found; Project), its heading, and its error in place. */
export function NewAgentSection({
  title,
  description,
  error,
  children,
}: {
  title: string;
  description?: string;
  /** What this part still needs, shown under it once Continue was pressed. */
  error?: string | null;
  children: React.ReactNode;
}) {
  const id = React.useId();
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-4 border-t pt-6">
      <div className="min-w-0">
        <h3 id={id} className="font-medium">
          {title}
        </h3>
        {description && <p className="text-muted-foreground text-xs">{description}</p>}
      </div>
      {children}
      {error && (
        <p
          role="alert"
          className="text-destructive inline-flex min-w-0 items-start gap-1.5 text-sm"
        >
          <AlertCircleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span className="min-w-0 [overflow-wrap:anywhere]">{error}</span>
        </p>
      )}
    </section>
  );
}
