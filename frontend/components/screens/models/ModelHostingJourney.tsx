"use client";

import { useTranslations } from "next-intl";
import { CheckIcon, ArrowRightIcon } from "lucide-react";

export interface ModelHostingJourneyProps {
  sourceAnchor: string;
  clusterAnchor: string;
  reviewAnchor: string;
  modelSelected: boolean;
  clusterSelected: boolean;
  readyForReview: boolean;
  accepted: boolean;
}

export function ModelHostingJourney(props: ModelHostingJourneyProps) {
  const t = useTranslations("models.shared.hosting");
  const current = !props.modelSelected ? 0 : !props.clusterSelected ? 1 : 2;
  const steps = [
    {
      title: "modelStep",
      anchor: props.sourceAnchor,
      complete: props.modelSelected,
      enabled: true,
    },
    {
      title: "clusterStep",
      anchor: props.clusterAnchor,
      complete: props.clusterSelected,
      enabled: props.modelSelected,
    },
    {
      title: "reviewStep",
      anchor: props.reviewAnchor,
      complete: props.accepted,
      enabled: props.clusterSelected,
    },
  ] as const;
  return (
    <nav aria-label={t("journeyTitle")} className="@container space-y-3">
      <ol className="grid gap-3 @lg:grid-cols-3">
        {steps.map((step, index) => {
          const selected = index === current;
          const status = step.complete
            ? index === 2
              ? "requestAccepted"
              : "selectedStep"
            : selected
              ? props.readyForReview
                ? "readyForReview"
                : "currentStep"
              : "upNext";
          const content = (
            <>
              <span
                className="bg-surface-2 flex size-8 shrink-0 items-center justify-center rounded-full font-medium"
                aria-hidden="true"
              >
                {step.complete ? <CheckIcon className="size-4" /> : index + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{t(step.title)}</span>
                <span className="text-muted-foreground block text-xs">{t(status)}</span>
              </span>
              {step.enabled && <ArrowRightIcon className="size-4 shrink-0" aria-hidden="true" />}
            </>
          );
          const style =
            "bg-surface-1 flex h-full items-center gap-3 rounded-lg border p-4 " +
            (selected ? "border-primary" : "border-border");
          return (
            <li key={step.title} aria-current={selected ? "step" : undefined}>
              {step.enabled ? (
                <a href={`#${step.anchor}`} className={style}>
                  {content}
                </a>
              ) : (
                <div className={style}>{content}</div>
              )}
            </li>
          );
        })}
      </ol>
      <p role="status" className="text-muted-foreground text-sm">
        {t(
          props.accepted
            ? "acceptedHelp"
            : props.readyForReview
              ? "readyHelp"
              : current === 0
                ? "chooseModelHelp"
                : current === 1
                  ? "chooseClusterHelp"
                  : "reviewChecksHelp"
        )}
      </p>
    </nav>
  );
}
