"use client";

import { AlertTriangleIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

import {
  type DraftField,
  type NewBrainDraft,
  type Problem,
  slugFromName,
  teamsOf,
  type WizardProject,
  withName,
} from "./new-brain-model";
import { WizardField } from "./WizardField";

export interface BrainDetailsStepProps {
  draft: NewBrainDraft;
  onChange: (next: NewBrainDraft) => void;
  projects: WizardProject[];
  projectsLoading: boolean;
  projectsError: string | null;
  onRetryProjects: () => void;
  /** `app.deploy`: without it the layer is created without a first deploy. */
  canDeploy: boolean;
  problems: Partial<Record<DraftField, Problem>>;
  serverErrors: Partial<Record<DraftField, string>>;
}

/**
 * Step 2 (#2427): the layer's name and slug, the team that owns it and the
 * project it is registered in (a layer belongs to its project's team), the
 * environment its first deploy goes to, and whether to deploy now. Pure.
 */
export function BrainDetailsStep({
  draft,
  onChange,
  projects,
  projectsLoading,
  projectsError,
  onRetryProjects,
  canDeploy,
  problems,
  serverErrors,
}: BrainDetailsStepProps) {
  const t = useTranslations("brains.wizard");
  const teams = teamsOf(projects);
  const inTeam = projects.filter((p) => p.team.id === draft.teamId);
  const shown = (field: DraftField) => {
    const problem = problems[field];
    return problem && problem !== "required" ? t(`problems.${problem}`) : null;
  };
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <div className="grid min-w-0 gap-4 sm:grid-cols-2">
        <WizardField
          id="brain-name"
          label={t("name")}
          help={t("nameHelp")}
          problem={shown("name")}
          serverError={serverErrors.name}
        >
          <Input
            id="brain-name"
            value={draft.name}
            placeholder={t("namePlaceholder")}
            onChange={(e) => onChange(withName(draft, e.target.value))}
            aria-describedby="brain-name-note"
          />
        </WizardField>
        <WizardField
          id="brain-slug"
          label={t("slug")}
          required
          help={t("slugHelp")}
          problem={shown("slug")}
          serverError={serverErrors.slug}
        >
          <Input
            id="brain-slug"
            className="font-mono"
            value={draft.slug}
            placeholder={slugFromName(t("namePlaceholder")) || undefined}
            maxLength={63}
            onChange={(e) => onChange({ ...draft, slug: e.target.value, slugTouched: true })}
            aria-invalid={Boolean(shown("slug") || serverErrors.slug) || undefined}
            aria-describedby="brain-slug-note"
            autoComplete="off"
            spellCheck={false}
          />
        </WizardField>
      </div>

      {projectsLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : projectsError ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-sm">
          <AlertTriangleIcon className="text-danger size-4" aria-hidden />
          <span className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
            {projectsError}
          </span>
          <Button type="button" size="sm" variant="outline" onClick={onRetryProjects}>
            {t("retry")}
          </Button>
        </div>
      ) : (
        <div className="grid min-w-0 gap-4 sm:grid-cols-2">
          <WizardField
            id="brain-team"
            label={t("team")}
            required
            help={t("teamHelp")}
            serverError={serverErrors.teamId}
          >
            <Select
              value={draft.teamId}
              onValueChange={(teamId) =>
                onChange({
                  ...draft,
                  teamId,
                  // A project of another team cannot hold this layer.
                  projectId:
                    projects.find((p) => p.id === draft.projectId)?.team.id === teamId
                      ? draft.projectId
                      : (projects.find((p) => p.team.id === teamId)?.id ?? ""),
                })
              }
            >
              <SelectTrigger id="brain-team" className="w-full">
                <SelectValue placeholder={teams.length ? t("chooseTeam") : t("noTeams")} />
              </SelectTrigger>
              <SelectContent>
                {teams.map((team) => (
                  <SelectItem key={team.id} value={team.id}>
                    <span className="font-mono text-xs">{team.slug}</span>{" "}
                    <span className="text-muted-foreground">{team.name}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </WizardField>
          <WizardField
            id="brain-project"
            label={t("project")}
            required
            help={t("projectHelp")}
            serverError={serverErrors.projectId}
          >
            <Select
              value={draft.projectId}
              onValueChange={(projectId) => onChange({ ...draft, projectId })}
              disabled={!draft.teamId}
            >
              <SelectTrigger id="brain-project" className="w-full">
                <SelectValue placeholder={draft.teamId ? t("chooseProject") : t("teamFirst")} />
              </SelectTrigger>
              <SelectContent>
                {inTeam.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    <span className="font-mono text-xs">{p.slug}</span>{" "}
                    <span className="text-muted-foreground">{p.name}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </WizardField>
        </div>
      )}

      <div className="grid min-w-0 gap-4 sm:grid-cols-2">
        <WizardField
          id="brain-environment"
          label={t("environment")}
          required
          help={t("environmentHelp")}
          problem={shown("environmentName")}
          serverError={serverErrors.environmentName}
        >
          <Input
            id="brain-environment"
            className="font-mono"
            value={draft.environmentName}
            onChange={(e) => onChange({ ...draft, environmentName: e.target.value })}
            aria-invalid={
              Boolean(shown("environmentName") || serverErrors.environmentName) || undefined
            }
            aria-describedby="brain-environment-note"
            autoComplete="off"
            spellCheck={false}
          />
        </WizardField>
        <div className="flex min-w-0 flex-col gap-2 sm:pt-7">
          <div className="flex items-start gap-2">
            <Checkbox
              id="brain-deploy"
              checked={canDeploy && draft.deploy}
              disabled={!canDeploy}
              onCheckedChange={(checked) => onChange({ ...draft, deploy: checked === true })}
              aria-describedby="brain-deploy-note"
            />
            <Label htmlFor="brain-deploy" className="font-normal">
              {t("deploy")}
            </Label>
          </div>
          <p id="brain-deploy-note" className="text-muted-foreground text-xs">
            {canDeploy ? t("deployHelp") : t("deployDenied")}
          </p>
        </div>
      </div>
    </div>
  );
}
