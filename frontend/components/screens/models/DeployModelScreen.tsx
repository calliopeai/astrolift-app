"use client";

import { QueryError } from "@/components/QueryError";

import { AlertTriangleIcon, ArrowLeftIcon, ArrowRightIcon, Loader2Icon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
import { FlowSteps } from "@/components/screens/agents/skills/FlowSteps";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { type Fit, gpuFit, MODEL_CATALOG, neededGiB, serviceNameFor } from "./model-catalog";
import type { DeployModelState } from "./use-deploy-model";

const CUSTOM = "__custom__";
const INSTALL_DEFAULT = "__default__";

const STEPS = ["where", "model", "review"] as const;

export type DeployModelScreenProps = DeployModelState & {
  /** Start on a step; stories use it. */
  initialStep?: 1 | 2 | 3;
  /** Preselected environment (stories); the app starts with none. */
  initialEnvId?: string;
  /** A refusal to open with; stories use it. */
  initialError?: string | null;
};

function fitLine(
  t: ReturnType<typeof useTranslations>,
  fit: Fit,
  paramsB: number,
  gpus: number,
  hasEnv: boolean
): string {
  switch (fit.kind) {
    case "fits":
      return t("fit.fits", { need: neededGiB(paramsB), gpus, perGpu: fit.perGpuGiB });
    case "tooSmall":
      return t("fit.tooSmall", {
        need: fit.needGiB,
        gpus,
        perGpu: fit.perGpuGiB,
        total: fit.perGpuGiB * gpus,
      });
    case "noNode":
      return t("fit.noNode", { gpus, most: fit.most });
    case "unknown":
      return hasEnv ? t("fit.unknown") : "";
  }
}

/**
 * Agents › Models › Deploy model: host an open-weight model with vLLM
 * (#2040). Six fields and a GPU bill, so a page in three steps with a review
 * (spec 44 §5.4): where it runs, which model on how many GPUs, then what
 * will be provisioned and whether it fits. Errors stand in place; the
 * outcome is the hook's toast. Pure view; the data half is useDeployModel.
 */
export function DeployModelScreen({
  envs,
  envsLoading,
  envsError,
  onRetryTargets,
  clustersLoading,
  clustersError,
  onRetryClusters,
  clusters,
  loading,
  deploy,
  initialStep = 1,
  initialEnvId = "",
  initialError = null,
}: DeployModelScreenProps) {
  const t = useTranslations("modelDeploy");
  const [step, setStep] = React.useState<1 | 2 | 3>(initialStep);
  const [envId, setEnvId] = React.useState(initialEnvId);
  const [catalogId, setCatalogId] = React.useState(MODEL_CATALOG[0].id);
  const [customId, setCustomId] = React.useState("");
  const [customParams, setCustomParams] = React.useState("");
  const [gpus, setGpus] = React.useState(1);
  const [frontend, setFrontend] = React.useState(INSTALL_DEFAULT);
  const [hfRef, setHfRef] = React.useState("");
  const [envError, setEnvError] = React.useState<string | null>(null);
  const [modelError, setModelError] = React.useState<string | null>(null);
  const [formError, setFormError] = React.useState<string | null>(initialError);

  const env = envs.find((e) => e.id === envId);
  const custom = catalogId === CUSTOM;
  const modelId = custom ? customId.trim() : catalogId;
  const paramsB = custom
    ? Number(customParams) || 0
    : (MODEL_CATALOG.find((m) => m.id === catalogId)?.paramsB ?? 0);
  const capabilities = clusters.find((c) => c.id === env?.clusterId)?.capabilities;
  const fit: Fit =
    env && paramsB ? gpuFit(capabilities, gpus, neededGiB(paramsB)) : { kind: "unknown" };
  const blocked = fit.kind === "noNode" || fit.kind === "tooSmall";
  const line = fitLine(t, fit, paramsB, gpus, Boolean(env));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (envsLoading || envsError || clustersLoading || clustersError) return;
    setFormError(null);
    if (step === 1) {
      if (!env) {
        setEnvError(t("errors.chooseEnv"));
        return;
      }
      setStep(2);
      return;
    }
    if (step === 2) {
      if (!modelId) {
        setModelError(t("errors.enterModel"));
        return;
      }
      setStep(3);
      return;
    }
    if (!env || !modelId || blocked) return;
    const config: Record<string, unknown> = { model: modelId, gpu: gpus };
    if (frontend !== INSTALL_DEFAULT) config.frontend = frontend;
    if (hfRef.trim()) config.hf_token_secret_ref = hfRef.trim();
    setFormError(await deploy({ env, modelId, config }));
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={agentsCrumbs("models", { label: t("title") })}
        title={t("title")}
        context={
          <FlowSteps steps={STEPS.map((k) => ({ label: t(`steps.${k}`) }))} current={step} />
        }
      />

      <p className="text-muted-foreground max-w-2xl text-sm">{t("intro")}</p>

      <QueryError title={t("errors.targets")} error={envsError} onRetry={onRetryTargets} />
      <QueryError title={t("errors.clusters")} error={clustersError} onRetry={onRetryClusters} />
      {clustersLoading && (
        <p role="status" className="text-muted-foreground text-sm">
          {t("loadingClusters")}
        </p>
      )}
      <form
        onSubmit={submit}
        noValidate
        className="bg-card flex max-w-3xl min-w-0 flex-col gap-4 rounded-md border p-6"
      >
        {formError && (
          <div
            role="alert"
            className="border-destructive/40 bg-destructive/5 text-destructive flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm"
          >
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span className="min-w-0 [overflow-wrap:anywhere]">{formError}</span>
          </div>
        )}

        {step === 1 && (
          <Field data-invalid={Boolean(envError) || undefined} className="min-w-0">
            <FieldLabel htmlFor="model-env">{t("appEnvironment")}</FieldLabel>
            {envsLoading ? (
              <Skeleton className="h-9 w-full" />
            ) : (
              <Select
                disabled={Boolean(envsError)}
                value={envId}
                onValueChange={(v) => {
                  setEnvId(v);
                  setEnvError(null);
                }}
              >
                <SelectTrigger
                  id="model-env"
                  aria-label={t("appEnvironment")}
                  className="w-full min-w-0"
                  aria-invalid={Boolean(envError) || undefined}
                >
                  <SelectValue
                    placeholder={
                      envsError
                        ? t("envsUnavailable")
                        : envs.length === 0
                          ? t("noEnvs")
                          : t("chooseWhere")
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {envs.map((e) => (
                    <SelectItem key={e.id} value={e.id}>
                      {e.registeredAppSlug} · {e.name}
                      {e.clusterSlug ? ` (${e.clusterSlug})` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <FieldError className="[overflow-wrap:anywhere]">{envError}</FieldError>
            <FieldDescription>{t("envHelp")}</FieldDescription>
          </Field>
        )}

        {step === 2 && (
          <>
            <Field data-invalid={Boolean(modelError) || undefined} className="min-w-0">
              <FieldLabel htmlFor="model-id">{t("model")}</FieldLabel>
              <Select
                value={catalogId}
                onValueChange={(v) => {
                  setCatalogId(v);
                  setModelError(null);
                }}
              >
                <SelectTrigger id="model-id" aria-label={t("model")} className="w-full min-w-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MODEL_CATALOG.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.label} · {t("gb", { n: neededGiB(m.paramsB) })}
                    </SelectItem>
                  ))}
                  <SelectItem value={CUSTOM}>{t("customId")}</SelectItem>
                </SelectContent>
              </Select>
              {custom && (
                <div className="grid min-w-0 grid-cols-3 gap-2">
                  <Input
                    aria-label={t("hfModelId")}
                    placeholder="org/model"
                    className="col-span-2 min-w-0 font-mono"
                    spellCheck={false}
                    value={customId}
                    aria-invalid={Boolean(modelError) || undefined}
                    onChange={(e) => {
                      setCustomId(e.target.value);
                      setModelError(null);
                    }}
                  />
                  <Input
                    aria-label={t("paramsLabel")}
                    placeholder={t("paramsPlaceholder")}
                    inputMode="decimal"
                    className="min-w-0 font-mono"
                    value={customParams}
                    onChange={(e) => setCustomParams(e.target.value)}
                  />
                </div>
              )}
              <FieldError className="[overflow-wrap:anywhere]">{modelError}</FieldError>
            </Field>

            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field className="min-w-0">
                <FieldLabel htmlFor="model-gpus">{t("gpus")}</FieldLabel>
                <Input
                  id="model-gpus"
                  type="number"
                  min={0}
                  max={16}
                  className="font-mono"
                  value={gpus}
                  onChange={(e) => setGpus(Math.max(0, Math.min(16, Number(e.target.value) || 0)))}
                />
              </Field>
              <Field className="min-w-0">
                <FieldLabel htmlFor="model-frontend">{t("frontend")}</FieldLabel>
                <Select value={frontend} onValueChange={setFrontend}>
                  <SelectTrigger
                    id="model-frontend"
                    aria-label={t("frontend")}
                    className="w-full min-w-0"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={INSTALL_DEFAULT}>{t("clusterDefault")}</SelectItem>
                    <SelectItem value="rust">Rust</SelectItem>
                    <SelectItem value="python">Python</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </div>

            <Field className="min-w-0">
              <FieldLabel htmlFor="model-hf">{t("hfTokenLabel")}</FieldLabel>
              <Input
                id="model-hf"
                placeholder="services/<org>/<owner>/hf#token"
                className="font-mono"
                spellCheck={false}
                value={hfRef}
                onChange={(e) => setHfRef(e.target.value)}
              />
            </Field>

            <FitLine line={line} blocked={blocked} />
          </>
        )}

        {step === 3 && env && (
          <>
            <DefinitionList
              items={[
                {
                  term: t("review.environment"),
                  description: (
                    <span className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                      {env.registeredAppSlug} · {env.name}
                    </span>
                  ),
                },
                {
                  term: t("review.cluster"),
                  description: (
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">
                      {env.clusterSlug ?? t("none")}
                    </span>
                  ),
                },
                {
                  term: t("model"),
                  description: (
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">{modelId}</span>
                  ),
                },
                {
                  term: t("review.serviceName"),
                  description: (
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">
                      {serviceNameFor(modelId)}
                    </span>
                  ),
                },
                {
                  term: t("gpus"),
                  description: <span className="font-mono text-xs">{gpus}</span>,
                },
                {
                  term: t("review.frontend"),
                  description: frontend === INSTALL_DEFAULT ? t("clusterDefault") : frontend,
                },
                {
                  term: t("review.hfToken"),
                  description: (
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">
                      {hfRef.trim() || t("none")}
                    </span>
                  ),
                },
              ]}
            />
            <FitLine line={line} blocked={blocked} />
          </>
        )}

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 border-t pt-4">
          <Button type="button" variant="ghost" asChild className="mr-auto">
            <Link href="/models">{t("cancel")}</Link>
          </Button>
          {step > 1 && (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setFormError(null);
                setStep((step - 1) as 1 | 2);
              }}
            >
              <ArrowLeftIcon className="size-4" />
              {t("back")}
            </Button>
          )}
          {step < 3 ? (
            <Button
              type="submit"
              disabled={
                Boolean(envsError || clustersError) ||
                clustersLoading ||
                (step === 1 && envsLoading)
              }
            >
              {t("continue")}
              <ArrowRightIcon className="size-4" />
            </Button>
          ) : (
            <Button
              type="submit"
              disabled={
                !env ||
                !modelId ||
                blocked ||
                loading ||
                clustersLoading ||
                Boolean(envsError || clustersError)
              }
            >
              {loading && <Loader2Icon className="size-4 animate-spin" />}
              {loading ? t("deploying") : t("deploy")}
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}

function FitLine({ line, blocked }: { line: string; blocked: boolean }) {
  if (!line) return null;
  return (
    <p
      role="status"
      className={cn(
        "text-sm [overflow-wrap:anywhere]",
        blocked ? "text-destructive" : "text-muted-foreground"
      )}
    >
      {line}
    </p>
  );
}
