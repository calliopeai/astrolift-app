"use client";

import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

export interface FlowStep {
  label: string;
}

export interface FlowStepsProps {
  steps: FlowStep[];
  /** 1-based. */
  current: number;
}

/**
 * `1 Skill · 2 Instructions`: a stepped create page's steps, in the header's
 * context slot (spec 44 §5.4), as the Register cluster page shows them. Pure.
 */
export function FlowSteps({ steps, current }: FlowStepsProps) {
  const tr = useTranslations("agentSkills.common");
  return (
    <ol aria-label={tr("steps")} className="inline-flex min-w-0 flex-wrap items-center gap-2">
      {steps.map((s, i) => {
        const n = i + 1;
        return (
          <li
            key={s.label}
            aria-current={n === current ? "step" : undefined}
            className={cn(
              "inline-flex min-w-0 items-center gap-1.5",
              n === current ? "text-foreground font-medium" : "text-muted-foreground"
            )}
          >
            {i > 0 && (
              <span aria-hidden className="text-muted-foreground">
                ·
              </span>
            )}
            <span className="font-mono">{n}</span> {s.label}
          </li>
        );
      })}
    </ol>
  );
}
