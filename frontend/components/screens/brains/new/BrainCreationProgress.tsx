"use client";

import {
  AlertTriangleIcon,
  ArrowRightIcon,
  CheckIcon,
  CircleIcon,
  Loader2Icon,
  RotateCcwIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Skeleton } from "@/components/ui/skeleton";
import type { BrainCreationFieldsFragment } from "@/graphql/__generated__/operations";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";
import { cn } from "@/lib/utils";

import { shortSha } from "../brain-status-model";
import { brainHref } from "../detail/brain-tabs-model";
import { type CreationStep, creationSteps } from "./new-brain-model";

export type BrainCreation = BrainCreationFieldsFragment;

export interface BrainCreationProgressProps {
  /** Null until the first read answers, or when nothing has this id. */
  creation: BrainCreation | null;
  loading: boolean;
  /** The read failed: polling stops until Retry. */
  error: string | null;
  onRetry: () => void;
  /** Whether the request asked for a first deploy. */
  deploy: boolean;
  /** For a FAILED creation: the step it stopped at (`failedStep`). */
  failed: CreationStep | null;
  /** Back to the form with the values still filled in; absent after a reload. */
  onEdit?: () => void;
  onStartOver: () => void;
}

/**
 * A layer being created (#2424 `astroliftBrainCreation`), polled until READY
 * or FAILED: each step with its state, the repository, the scaffold commit
 * and the first deployment. READY opens the layer; FAILED shows the server's
 * reason and offers the form again. The work runs on the server, so leaving
 * the page stops nothing. Pure.
 */
export function BrainCreationProgress({
  creation,
  loading,
  error,
  onRetry,
  deploy,
  failed,
  onEdit,
  onStartOver,
}: BrainCreationProgressProps) {
  const t = useTranslations("brains.creation");
  const crumbs = [
    areaSwitcher(NAV, "apps", "brains"),
    { label: t("listCrumb"), href: "/brains" },
    { label: creation?.slug ?? t("crumb") },
  ];
  if (!creation) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <ShellHeader
          crumbs={crumbs}
          title={loading ? <Skeleton className="h-6 w-56" /> : t("missingTitle")}
        />
        {loading ? (
          <Skeleton className="h-48 w-full max-w-3xl" />
        ) : (
          <Notice
            tone="error"
            title={error ? t("readFailed") : t("missingTitle")}
            detail={error ?? t("missingDescription")}
            actions={
              <>
                {error && (
                  <Button size="sm" variant="outline" onClick={onRetry}>
                    {t("retry")}
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={onStartOver}>
                  {t("startOver")}
                </Button>
              </>
            }
          />
        )}
      </div>
    );
  }
  const steps = creationSteps(creation.state, { deploy, failed });
  const ready = creation.state === "READY";
  const isFailed = creation.state === "FAILED";
  const name = creation.app?.name || creation.slug;
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader
        crumbs={crumbs}
        title={
          ready
            ? t("readyTitle", { name })
            : isFailed
              ? t("failedTitle", { slug: creation.slug })
              : t("runningTitle", { slug: creation.slug })
        }
        primaryAction={
          ready ? (
            <Button asChild size="sm">
              <Link href={brainHref(creation.slug)}>
                {t("open")}
                <ArrowRightIcon className="size-4" />
              </Link>
            </Button>
          ) : undefined
        }
      />

      <section
        aria-label={t("progress")}
        aria-busy={!ready && !isFailed}
        className="bg-card flex max-w-3xl min-w-0 flex-col gap-5 rounded-md border p-6"
      >
        {isFailed && (
          <Notice
            tone="error"
            title={t("failedReason")}
            detail={
              creation.error ? `${creation.error.code}: ${creation.error.message}` : t("noReason")
            }
            actions={
              <>
                {onEdit && (
                  <Button size="sm" variant="outline" onClick={onEdit}>
                    <RotateCcwIcon className="size-3.5" />
                    {t("editAndRetry")}
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={onStartOver}>
                  {t("startOver")}
                </Button>
              </>
            }
          />
        )}
        {error && !isFailed && (
          <Notice
            tone="warn"
            title={t("pollFailed")}
            detail={error}
            actions={
              <Button size="sm" variant="outline" onClick={onRetry}>
                {t("retry")}
              </Button>
            }
          />
        )}
        <ol className="flex min-w-0 flex-col gap-3">
          {steps.map(({ step, state }) => (
            <li key={step} className="flex min-w-0 items-start gap-3">
              <StepMark state={state} />
              <div className="min-w-0">
                <p
                  className={cn(
                    "text-sm",
                    state === "todo" ? "text-muted-foreground" : "text-foreground font-medium",
                    state === "failed" && "text-danger-fg"
                  )}
                >
                  {t(`steps.${step}`)}
                  <span className="sr-only"> {t(`stepStates.${state}`)}</span>
                </p>
                <p className="text-muted-foreground text-xs">{t(`stepHelp.${step}`)}</p>
              </div>
            </li>
          ))}
        </ol>
        <DefinitionList
          items={[
            { term: t("repo"), description: <span className="font-mono">{creation.repo}</span> },
            {
              term: t("templateRef"),
              description: <span className="font-mono">{creation.templateRef}</span>,
            },
            {
              term: t("commit"),
              description: creation.commitSha ? (
                <span className="font-mono" title={creation.commitSha}>
                  {shortSha(creation.commitSha)}
                </span>
              ) : (
                <span className="text-muted-foreground">{t("notYet")}</span>
              ),
            },
            {
              term: t("deployment"),
              description: creation.deploymentId ? (
                <Link
                  href={`/deployments/${creation.deploymentId}`}
                  className="text-primary font-mono hover:underline"
                >
                  {creation.deploymentId.slice(0, 8)}
                </Link>
              ) : (
                <span className="text-muted-foreground">
                  {deploy ? t("notYet") : t("noDeploy")}
                </span>
              ),
            },
          ]}
        />
        {!ready && !isFailed && <p className="text-muted-foreground text-xs">{t("leaveNote")}</p>}
      </section>
    </div>
  );
}

function StepMark({ state }: { state: "done" | "current" | "todo" | "failed" }) {
  const base = "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border";
  if (state === "done")
    return (
      <span className={cn(base, "border-success-border bg-success/15 text-success-fg")} aria-hidden>
        <CheckIcon className="size-3" />
      </span>
    );
  if (state === "failed")
    return (
      <span className={cn(base, "border-danger-border bg-danger/15 text-danger-fg")} aria-hidden>
        <XIcon className="size-3" />
      </span>
    );
  if (state === "current")
    return (
      <span className={cn(base, "border-info-border bg-info/15 text-info-fg")} aria-hidden>
        <Loader2Icon className="size-3 animate-spin motion-reduce:animate-none" />
      </span>
    );
  return (
    <span className={cn(base, "text-muted-foreground")} aria-hidden>
      <CircleIcon className="size-2" />
    </span>
  );
}

function Notice({
  tone,
  title,
  detail,
  actions,
}: {
  tone: "error" | "warn";
  title: string;
  detail: string;
  actions?: React.ReactNode;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex min-w-0 flex-wrap items-start gap-3 rounded-md border px-4 py-3",
        tone === "error"
          ? "border-danger-border bg-danger/5"
          : "border-warning-border bg-warning/10"
      )}
    >
      <AlertTriangleIcon
        className={cn("mt-0.5 size-4 shrink-0", tone === "error" ? "text-danger" : "text-warning")}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "text-sm font-medium",
            tone === "error" ? "text-danger-fg" : "text-warning-fg"
          )}
        >
          {title}
        </p>
        <p className="text-muted-foreground mt-0.5 font-mono text-xs [overflow-wrap:anywhere]">
          {detail}
        </p>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
