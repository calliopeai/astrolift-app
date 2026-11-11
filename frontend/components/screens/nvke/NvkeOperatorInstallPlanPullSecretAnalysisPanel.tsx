"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { installPlanMatches, installPlanSchema } from "@/lib/nvke/operator-install-plan";
import { validateInstallPlanCharts } from "@/lib/nvke/operator-install-plan-charts";
import { validateInstallPlanTree } from "@/lib/nvke/operator-install-plan-tree";
import {
  installPlanPullSecretAnalysisFormSchema,
  installPlanPullSecretAnalysisRequest,
  validateInstallPlanPullSecretAnalysis,
  installPlanPullSecretAnalysisSource,
  type NvkeInstallPlanPullSecretAnalysis,
  type NvkeInstallPlanPullSecretAnalysisForm,
} from "@/lib/nvke/operator-install-plan-pull-secret-analysis";
import type { NvkeInstallPlanImageBinding } from "@/lib/nvke/operator-install-plan-image-binding";
import type { NvkeInstallPlanPullAuthority } from "@/lib/nvke/operator-install-plan-pull-authority";
import type { NvkeInstallPlanWorkload } from "@/lib/nvke/operator-install-plan-workload";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import type { NvkeOperatorInstallPlanContextPanelProps } from "./NvkeOperatorInstallPlanContextPanel";

export interface NvkeOperatorInstallPlanPullSecretAnalysisPanelProps extends Pick<
  NvkeOperatorInstallPlanContextPanelProps,
  | "scopeKey"
  | "site"
  | "operation"
  | "authority"
  | "canReview"
  | "pending"
  | "loading"
  | "error"
  | "plan"
  | "charts"
  | "tree"
> {
  workload: NvkeInstallPlanWorkload | null;
  binding: NvkeInstallPlanImageBinding | null;
  pullAuthority: NvkeInstallPlanPullAuthority | null;
  artifacts: NvkeInstallPlanPullSecretAnalysis | null;
  retainedArtifactId: string | null;
  canRetry: boolean;
  allowOriginalPreparation?: boolean;
  onPrepare: (values: NvkeInstallPlanPullSecretAnalysisForm) => Promise<boolean>;
  onReview: (artifactId: string) => Promise<boolean>;
  onRetryPreparation: (artifactId: string) => Promise<boolean>;
  onChooseArtifact?: () => Promise<void>;
}

const defaults = (): NvkeInstallPlanPullSecretAnalysisForm => ({
  artifactId: "",
  imageSelections: "",
  maximumBytes: "16777216",
  maximumHelperBytes: "1048576",
  timeoutSeconds: "300",
});
export function NvkeOperatorInstallPlanPullSecretAnalysisPanel(
  props: NvkeOperatorInstallPlanPullSecretAnalysisPanelProps
) {
  return (
    <PullSecretAnalysisControls
      key={JSON.stringify([
        props.scopeKey,
        props.site,
        props.operation,
        props.authority,
        props.canReview,
        props.plan,
        props.charts,
        props.tree,
        props.workload,
        props.binding,
        props.pullAuthority,
      ])}
      {...props}
    />
  );
}

function PullSecretAnalysisControls(props: NvkeOperatorInstallPlanPullSecretAnalysisPanelProps) {
  const t = useTranslations("nvke.installImmutable"),
    pullText = useTranslations("nvke.installPullSecretAnalysis"),
    common = useTranslations("nvke.installTree"),
    contextText = useTranslations("nvke.installContext"),
    review = useTranslations("nvke.installReview");
  const id = useId();
  const [retained, setRetained] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [unconfirmed, setUnconfirmed] = useState(false);
  const busyRef = useRef(false);
  const form = useForm<NvkeInstallPlanPullSecretAnalysisForm>({
    resolver: zodResolver(installPlanPullSecretAnalysisFormSchema),
    defaultValues: defaults(),
  });
  const parsed = installPlanSchema.safeParse(props.plan),
    plan = parsed.success ? parsed.data : null,
    authority = props.authority;
  let reviewedTree = false;
  if (
    plan &&
    props.charts &&
    props.tree &&
    props.scopeKey &&
    props.canReview &&
    authority &&
    props.operation.operationKey === "tooling.management.install" &&
    props.operation.status === "UNCERTAIN" &&
    props.operation.siteId === props.site.id &&
    authority.siteId === props.site.id &&
    authority.siteVersion === props.site.version &&
    authority.isEnabled &&
    authority.deletedAt === null &&
    authority.hasCredentials &&
    authority.verificationCode === "AGENT_VERIFIED" &&
    authority.verifiedAt &&
    Number.isFinite(Date.parse(authority.verifiedAt)) &&
    installPlanMatches(plan, {
      siteId: props.site.id,
      siteVersion: props.site.version,
      operationId: props.operation.id,
      operationVersion: props.operation.version,
      authorityId: authority.id,
      authorityVersion: authority.version,
      instanceId: authority.instanceId,
      bundleManifestSha256: authority.bundleManifestSha256,
      nativeRevision: props.site.upstreamRevision,
    })
  ) {
    try {
      const charts = validateInstallPlanCharts(props.charts, plan, props.charts.id),
        tree = validateInstallPlanTree(props.tree, plan, charts, props.tree.id);
      reviewedTree = !!(
        props.workload &&
        props.binding &&
        props.pullAuthority &&
        installPlanPullSecretAnalysisSource(
          plan,
          charts,
          tree,
          props.workload,
          props.binding,
          props.pullAuthority
        )
      );
    } catch {
      /* The tree no longer belongs to this reviewed source. */
    }
  }
  let candidate: NvkeInstallPlanPullSecretAnalysis | null = null;
  if (
    reviewedTree &&
    plan &&
    props.charts &&
    props.tree &&
    props.workload &&
    props.binding &&
    props.pullAuthority &&
    props.artifacts
  ) {
    try {
      candidate = validateInstallPlanPullSecretAnalysis(
        props.artifacts,
        plan,
        props.charts,
        props.tree,
        props.workload,
        props.binding,
        props.pullAuthority,
        props.artifacts.id
      );
    } catch {
      /* A stale observation cannot confirm this source. */
    }
  }
  const savedId = reviewedTree
    ? (props.retainedArtifactId ?? retained ?? candidate?.id ?? null)
    : null;
  useEffect(() => {
    if (savedId) form.setValue("artifactId", savedId);
  }, [savedId, form]);
  const artifacts =
    !props.loading && !props.error && !unconfirmed && candidate?.id === savedId ? candidate : null;
  const enabled = reviewedTree && !busy && !props.pending && !props.loading;
  const canPrepare =
    enabled &&
    (props.allowOriginalPreparation ||
      (!savedId && !props.error && !unconfirmed && props.artifacts === null));

  async function run(
    action: "prepare" | "review" | "retry",
    values?: NvkeInstallPlanPullSecretAnalysisForm
  ) {
    if (busyRef.current) return;
    const artifactId = savedId ?? values?.artifactId ?? form.getValues("artifactId");
    try {
      if (
        !reviewedTree ||
        !plan ||
        !props.charts ||
        !props.tree ||
        !props.workload ||
        !props.binding ||
        !props.pullAuthority ||
        !validNvkeGuid(artifactId) ||
        new Set([
          artifactId,
          plan.id,
          plan.operationId,
          props.charts.id,
          props.tree.id,
          props.workload.id,
          props.binding.id,
          props.pullAuthority.id,
        ]).size !== 8
      )
        throw new Error("INVALID_SOURCE");
      if (action === "prepare") {
        if (!values || values.artifactId !== artifactId) throw new Error("INVALID_INPUT");
        installPlanPullSecretAnalysisRequest(
          values,
          plan,
          props.charts,
          props.tree,
          props.workload,
          props.binding,
          props.pullAuthority
        );
      }
    } catch {
      form.setError("artifactId", { type: "source" });
      return;
    }
    if (
      !enabled ||
      (action === "prepare" && !canPrepare) ||
      (action === "retry" && (!savedId || !props.canRetry))
    )
      return;
    busyRef.current = true;
    setBusy(true);
    setRetained(artifactId);
    try {
      const ok =
        action === "prepare"
          ? await props.onPrepare(values!)
          : action === "review"
            ? await props.onReview(artifactId)
            : await props.onRetryPreparation(artifactId);
      setUnconfirmed(!ok);
    } catch {
      setUnconfirmed(true);
    } finally {
      form.reset({
        artifactId,
        imageSelections: "",
        maximumBytes: "",
        maximumHelperBytes: "",
        timeoutSeconds: "",
      });
      busyRef.current = false;
      setBusy(false);
    }
  }

  const limits = ["maximumBytes", "maximumHelperBytes", "timeoutSeconds"] as const;
  const code = (value: string) => <code className="text-xs [overflow-wrap:anywhere]">{value}</code>;
  return (
    <Section title={pullText("title")} description={pullText("help")}>
      <p className="text-warning-fg text-sm">{review("originalUncertain")}</p>
      <p className="text-muted-foreground text-sm">{pullText("closureRequired")}</p>
      <form
        noValidate
        autoComplete="off"
        className="space-y-3"
        onSubmit={form.handleSubmit((values) => run("prepare", values))}
      >
        <div className="max-w-xl space-y-2">
          <Label htmlFor={`${id}-artifactId`}>{common("artifactId")}</Label>
          <Controller
            control={form.control}
            name="artifactId"
            render={({ field }) => (
              <Input
                {...field}
                id={`${id}-artifactId`}
                value={savedId ?? field.value}
                disabled={!enabled || !!savedId}
                spellCheck={false}
              />
            )}
          />
          {!savedId && (
            <Button
              type="button"
              variant="outline"
              disabled={!canPrepare}
              onClick={() => {
                if (canPrepare && globalThis.crypto?.randomUUID)
                  form.setValue("artifactId", globalThis.crypto.randomUUID(), {
                    shouldValidate: true,
                  });
              }}
            >
              {common("newId")}
            </Button>
          )}
        </div>
        {(!savedId || props.allowOriginalPreparation) && (
          <>
            <p className="text-muted-foreground text-sm">
              {t(props.allowOriginalPreparation ? "originalOnly" : "settingsHelp")}
            </p>
            <div className="space-y-2">
              <Label htmlFor={`${id}-imageSelections`}>{pullText("imageSelections")}</Label>
              <Textarea
                id={`${id}-imageSelections`}
                rows={6}
                className="font-mono text-xs"
                aria-describedby={`${id}-pods-help`}
                {...form.register("imageSelections")}
                disabled={!canPrepare}
                spellCheck={false}
                placeholder={
                  '[{"podKey":"namespace/pod","imagePointer":"/spec/containers/0/image","ensureSecretPulledImages":true}]'
                }
              />
              <p id={`${id}-pods-help`} className="text-muted-foreground text-sm">
                {pullText("imagesHelp")}
              </p>
            </div>
            <details
              open={limits.some((field) => !!form.formState.errors[field]) ? true : undefined}
              className="space-y-3"
            >
              <summary className="cursor-pointer text-sm font-medium">
                {pullText("limitsTitle")}
              </summary>
              <p className="text-muted-foreground text-sm">{pullText("limitsHelp")}</p>
              <div className="grid gap-3 sm:grid-cols-2">
                {limits.map((field) => (
                  <div key={field} className="space-y-2">
                    <Label htmlFor={`${id}-${field}`}>
                      {field === "maximumBytes" || field === "timeoutSeconds"
                        ? t(field)
                        : pullText(field)}
                    </Label>
                    <Input
                      id={`${id}-${field}`}
                      inputMode="numeric"
                      {...form.register(field)}
                      disabled={!canPrepare}
                      spellCheck={false}
                    />
                  </div>
                ))}
              </div>
            </details>
          </>
        )}
        {Object.keys(form.formState.errors).length > 0 && (
          <p role="alert" className="text-danger-fg text-sm">
            {pullText("invalidInput")}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={!canPrepare}>
            {t("prepare")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={!enabled}
            onClick={() => void run("review")}
          >
            {t("review")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={!enabled || !savedId || !props.canRetry}
            onClick={() => void run("retry")}
          >
            {t("retry")}
          </Button>
          {props.onChooseArtifact && (
            <Button
              type="button"
              variant="outline"
              disabled={!enabled}
              onClick={() => void props.onChooseArtifact?.()}
            >
              {common("choose")}
            </Button>
          )}
        </div>
      </form>
      {props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {t("failed")}
        </p>
      )}
      {unconfirmed && (
        <p role="alert" className="text-warning-fg text-sm">
          {common("unconfirmed")}
        </p>
      )}
      {!artifacts && !props.error && !unconfirmed && (
        <p role="status" className="text-muted-foreground text-sm">
          {!reviewedTree
            ? pullText("parentRequired")
            : t(props.loading ? "loading" : props.artifacts ? "unavailable" : "unloaded")}
        </p>
      )}
      {artifacts && (
        <DefinitionList
          items={[
            { term: common("artifactId"), description: code(artifacts.id) },
            { term: t("artifactDigest"), description: code(artifacts.artifactDigest) },
            { term: t("sourceDigest"), description: code(artifacts.sourceDigest) },
            { term: contextText("captureDigest"), description: code(artifacts.captureDigest) },
            { term: t("bindingDigest"), description: code(artifacts.bindingDigest) },
            { term: t("workloadDigest"), description: code(artifacts.workloadDigest) },
            { term: pullText("authorityDigest"), description: code(artifacts.authorityDigest) },
            {
              term: pullText("pullAuthorityDigest"),
              description: code(artifacts.pullAuthorityDigest),
            },
            { term: pullText("imageUseCount"), description: artifacts.imageUseCount },
            { term: pullText("candidateCount"), description: artifacts.candidateCount },
            {
              term: pullText("missingReferenceCount"),
              description: artifacts.missingReferenceCount,
            },
            { term: pullText("sourceObservedAt"), description: artifacts.sourceObservedAt },
            { term: pullText("analyzedAt"), description: artifacts.analyzedAt },
          ]}
        />
      )}
    </Section>
  );
}
