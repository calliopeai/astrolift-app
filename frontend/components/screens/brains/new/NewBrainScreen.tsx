"use client";

import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  Loader2Icon,
  LockIcon,
  XIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";
import { cn } from "@/lib/utils";

import { NEW_BRAIN_STEPS, type NewBrainStep } from "./new-brain-model";

export interface NewBrainScreenProps {
  step: NewBrainStep;
  /** Jump back to an earlier step; later steps are reached with Continue. */
  onStep?: (step: NewBrainStep) => void;
  /** Absent on the first step, and while the create runs. */
  onBack?: () => void;
  onContinue: () => void;
  /** The step still has a field to fix; each field says what, beside itself. */
  continueDisabled?: boolean;
  /** The create request is in flight. */
  busy?: boolean;
  onCancel?: () => void;
  /**
   * The viewer holds no `app.create`, so there is nothing to fill in: the
   * page says so instead of offering a form the server would refuse.
   */
  denied?: boolean;
  children: React.ReactNode;
}

/**
 * Apps › Semantic layers › New (#2427): the same request as `astro brain
 * create`, in four numbered steps: where the repository lives, what the
 * layer is called and who owns it, Mimwell's substrate, then a review. Each
 * field carries its own error in place, a server refusal included. Pure
 * chrome: the step bodies are passed in.
 */
export function NewBrainScreen({
  step,
  onStep,
  onBack,
  onContinue,
  continueDisabled = false,
  busy = false,
  onCancel,
  denied = false,
  children,
}: NewBrainScreenProps) {
  const t = useTranslations("brains.wizard");
  const crumbs = [
    areaSwitcher(NAV, "apps", "brains"),
    { label: t("listCrumb"), href: "/brains" },
    { label: t("title") },
  ];
  if (denied)
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <ShellHeader crumbs={crumbs} title={t("title")} />
        <EmptyState
          icon={<LockIcon className="size-5" />}
          title={t("deniedTitle")}
          description={t("deniedDescription")}
          actionHref="/brains"
          actionLabel={t("backToList")}
        />
      </div>
    );
  const current = NEW_BRAIN_STEPS[step - 1]!;
  const last = step === NEW_BRAIN_STEPS.length;
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader
        crumbs={crumbs}
        title={t("title")}
        context={
          <ol aria-label={t("stepsLabel")} className="inline-flex flex-wrap items-center gap-2">
            {NEW_BRAIN_STEPS.map((s, i) => {
              const done = s.n < step;
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
                  {done && onStep && !busy ? (
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
            <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
              <XIcon className="size-4" />
              {t("cancel")}
            </Button>
          ) : undefined
        }
      />

      <section
        aria-labelledby="new-brain-step"
        className="bg-card flex max-w-4xl min-w-0 flex-col gap-6 rounded-md border p-6"
      >
        <div className="min-w-0">
          <h2 id="new-brain-step" className="text-lg font-semibold">
            {t(`steps.${current.key}.label`)}
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            {t(`steps.${current.key}.description`)}
          </p>
        </div>
        {children}
      </section>

      <div className="flex max-w-4xl min-w-0 items-center justify-between gap-2">
        <Button type="button" variant="ghost" onClick={onBack} disabled={!onBack || busy}>
          <ArrowLeftIcon className="size-4" />
          {t("back")}
        </Button>
        <Button type="button" onClick={onContinue} disabled={continueDisabled || busy}>
          {busy && <Loader2Icon className="size-4 animate-spin" />}
          {last ? t("create") : t("continue")}
          {!busy && !last && <ArrowRightIcon className="size-4" />}
        </Button>
      </div>
    </div>
  );
}
