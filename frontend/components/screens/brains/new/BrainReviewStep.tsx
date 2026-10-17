"use client";

import { PencilIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";

import { AXIS_ORDER, effectivePersistence } from "../substrate-cells";
import type { NewBrainDraft, NewBrainStep } from "./new-brain-model";

export interface BrainReviewStepProps {
  draft: NewBrainDraft;
  /** Names to show for the ids the draft holds. */
  connectionName: string;
  teamSlug: string;
  projectSlug: string;
  canDeploy: boolean;
  onEdit: (step: NewBrainStep) => void;
  /** A refusal the server tied to no field, verbatim. */
  serverError?: string | null;
}

/**
 * Step 4 (#2427): the whole request before it is sent, each part with an
 * edit link back to its step, and what Create does next: the repository is
 * created when it is missing, the Mimwell edition lands as one commit, the
 * layer is registered and, when asked, deployed. Pure.
 */
export function BrainReviewStep({
  draft,
  connectionName,
  teamSlug,
  projectSlug,
  canDeploy,
  onEdit,
  serverError,
}: BrainReviewStepProps) {
  const t = useTranslations("brains.wizard");
  const persistence = effectivePersistence(draft.substrate.cell, draft.substrate.persistence);
  const deploys = canDeploy && draft.deploy;
  const mono = (value: React.ReactNode) => <span className="font-mono">{value}</span>;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {serverError && (
        <p
          role="alert"
          className="border-danger-border bg-danger/5 text-danger-fg rounded-md border px-3 py-2 font-mono text-xs [overflow-wrap:anywhere]"
        >
          {serverError}
        </p>
      )}
      <Part title={t("steps.source.label")} onEdit={() => onEdit(1)}>
        <DefinitionList
          items={[
            { term: t("connection"), description: connectionName },
            { term: t("repo"), description: mono(draft.repo.trim()) },
            { term: t("visibility"), description: t(`visibilities.${draft.visibility}`) },
            {
              term: t("templateRef"),
              description: draft.templateRef.trim()
                ? mono(draft.templateRef.trim())
                : t("pinnedEdition"),
            },
          ]}
        />
      </Part>
      <Part title={t("steps.details.label")} onEdit={() => onEdit(2)}>
        <DefinitionList
          items={[
            { term: t("name"), description: draft.name.trim() || mono(draft.slug) },
            { term: t("slug"), description: mono(draft.slug) },
            { term: t("team"), description: mono(teamSlug) },
            { term: t("project"), description: mono(projectSlug) },
            { term: t("environment"), description: mono(draft.environmentName.trim()) },
            { term: t("deploy"), description: deploys ? t("deployYes") : t("deployNo") },
          ]}
        />
      </Part>
      <Part title={t("steps.substrate.label")} onEdit={() => onEdit(3)}>
        <DefinitionList
          items={[
            {
              term: t("cell"),
              description: mono(
                AXIS_ORDER.map((axis) => `${axis}=${draft.substrate.cell[axis]}`).join(", ")
              ),
            },
            {
              term: t("persistence"),
              description: persistence.length ? mono(persistence.join(", ")) : t("noPersistence"),
            },
            { term: t("replicas"), description: mono(draft.substrate.replicas) },
            {
              term: t("image"),
              description: draft.substrate.image.trim()
                ? mono(draft.substrate.image.trim())
                : t("builtImage"),
            },
            { term: t("port"), description: mono(draft.substrate.port) },
          ]}
        />
      </Part>
      <div className="bg-muted/40 min-w-0 rounded-md border px-4 py-3 text-sm">
        <p className="font-medium">{t("whatHappens")}</p>
        <ol className="text-muted-foreground mt-2 list-decimal space-y-1 pl-5 text-xs">
          <li>{t("happensRepo")}</li>
          <li>{t("happensScaffold")}</li>
          <li>{t("happensRegister")}</li>
          {deploys && <li>{t("happensDeploy", { environment: draft.environmentName.trim() })}</li>}
        </ol>
        {deploys && PRODUCTION.includes(draft.environmentName.trim()) && (
          <p className="text-muted-foreground mt-2 text-xs">{t("productionStepUp")}</p>
        )}
      </div>
    </div>
  );
}

/** The environment names a production deploy gate applies to (`deploy.production`). */
const PRODUCTION = ["production", "prod"];

function Part({
  title,
  onEdit,
  children,
}: {
  title: string;
  onEdit: () => void;
  children: React.ReactNode;
}) {
  const t = useTranslations("brains.wizard");
  return (
    <section className="min-w-0 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{title}</h3>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onEdit}
          aria-label={t("editPart", { part: title })}
        >
          <PencilIcon className="size-3.5" />
          {t("edit")}
        </Button>
      </div>
      {children}
    </section>
  );
}
