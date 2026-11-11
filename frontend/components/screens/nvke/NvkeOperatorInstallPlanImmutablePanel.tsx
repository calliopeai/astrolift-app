"use client";

import { useEffect, useId, useState } from "react";
import { useForm } from "react-hook-form";
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
  installPlanImmutableFormSchema,
  installPlanImmutableRequest,
  validateInstallPlanImmutableArtifact,
  type NvkeInstallPlanImmutableArtifact,
  type NvkeInstallPlanImmutableForm,
  type NvkeImmutableMode,
} from "@/lib/nvke/operator-install-plan-immutable";
import {
  validateInstallPlanImageBinding,
  type NvkeInstallPlanImageBinding,
} from "@/lib/nvke/operator-install-plan-image-binding";
import type { NvkeInstallPlanWorkload } from "@/lib/nvke/operator-install-plan-workload";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import type { NvkeOperatorInstallPlanContextPanelProps } from "./NvkeOperatorInstallPlanContextPanel";

export interface NvkeOperatorInstallPlanImmutablePanelProps extends Pick<
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
  mode: NvkeImmutableMode;
  binding: NvkeInstallPlanImageBinding | null;
  artifacts: NvkeInstallPlanImmutableArtifact | null;
  retainedArtifactId: string | null;
  canRetry: boolean;
  allowOriginalPreparation?: boolean;
  onPrepare: (values: NvkeInstallPlanImmutableForm) => Promise<boolean>;
  onReview: (artifactId: string) => Promise<boolean>;
  onRetryPreparation: (artifactId: string) => Promise<boolean>;
  onChooseArtifact?: () => Promise<void>;
}

const defaults = (mode: NvkeImmutableMode): NvkeInstallPlanImmutableForm => ({
  artifactId: "",
  item: "",
  payloadBindings: "",
  maximumBytes: "16777216",
  maximumResources: mode === "payload" ? "10000" : "",
  timeoutSeconds: "300",
});
export function NvkeOperatorInstallPlanImmutablePanel(
  props: NvkeOperatorInstallPlanImmutablePanelProps
) {
  return (
    <ImmutableControls
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
        props.mode,
      ])}
      {...props}
    />
  );
}

function ImmutableControls(props: NvkeOperatorInstallPlanImmutablePanelProps) {
  const t = useTranslations("nvke.installImmutable"),
    common = useTranslations("nvke.installTree"),
    contextText = useTranslations("nvke.installContext"),
    review = useTranslations("nvke.installReview");
  const id = useId();
  const [retained, setRetained] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [unconfirmed, setUnconfirmed] = useState(false);
  const form = useForm<NvkeInstallPlanImmutableForm>({
    resolver: zodResolver(installPlanImmutableFormSchema(props.mode)),
    defaultValues: defaults(props.mode),
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
        validateInstallPlanImageBinding(
          props.binding,
          plan,
          charts,
          tree,
          props.workload,
          props.binding.id
        )
      );
    } catch {
      /* The tree no longer belongs to this reviewed source. */
    }
  }
  let candidate: NvkeInstallPlanImmutableArtifact | null = null;
  if (
    reviewedTree &&
    plan &&
    props.charts &&
    props.tree &&
    props.workload &&
    props.binding &&
    props.artifacts
  ) {
    try {
      candidate = validateInstallPlanImmutableArtifact(
        props.artifacts,
        props.mode,
        plan,
        props.charts,
        props.tree,
        props.workload,
        props.binding,
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
    values?: NvkeInstallPlanImmutableForm
  ) {
    const artifactId = savedId ?? values?.artifactId ?? form.getValues("artifactId");
    try {
      if (
        !reviewedTree ||
        !plan ||
        !props.charts ||
        !props.tree ||
        !props.workload ||
        !props.binding ||
        !validNvkeGuid(artifactId) ||
        new Set([
          artifactId,
          plan.id,
          plan.operationId,
          props.charts.id,
          props.tree.id,
          props.workload.id,
          props.binding.id,
        ]).size !== 7
      )
        throw new Error("INVALID_SOURCE");
      if (action === "prepare") {
        if (!values || values.artifactId !== artifactId) throw new Error("INVALID_INPUT");
        installPlanImmutableRequest(
          values,
          props.mode,
          plan,
          props.charts,
          props.tree,
          props.workload,
          props.binding
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
        maximumBytes: "",
        payloadBindings: "",
        item: "",
        maximumResources: "",
        timeoutSeconds: "",
      });
      setBusy(false);
    }
  }

  const settings =
    props.mode === "payload"
      ? (["item", "maximumBytes", "maximumResources", "timeoutSeconds"] as const)
      : (["payloadBindings", "maximumBytes", "timeoutSeconds"] as const);
  const code = (value: string) => <code className="text-xs [overflow-wrap:anywhere]">{value}</code>;
  return (
    <Section
      title={t(props.mode === "payload" ? "titlePayload" : "titleExecution")}
      description={t(props.mode === "payload" ? "helpPayload" : "helpExecution")}
    >
      <p className="text-warning-fg text-sm">{review("originalUncertain")}</p>
      <p className="text-muted-foreground text-sm">{t("closureRequired")}</p>
      <form
        noValidate
        autoComplete="off"
        className="space-y-3"
        onSubmit={form.handleSubmit((values) => run("prepare", values))}
      >
        <div className="max-w-xl space-y-2">
          <Label htmlFor={`${id}-artifactId`}>{common("artifactId")}</Label>
          <Input
            id={`${id}-artifactId`}
            {...form.register("artifactId")}
            {...(savedId ? { value: savedId } : {})}
            disabled={!enabled || !!savedId}
            spellCheck={false}
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
            {settings.map((field) => (
              <div key={field} className="max-w-xl space-y-2">
                <Label htmlFor={`${id}-${field}`}>{t(field)}</Label>
                {field === "payloadBindings" ? (
                  <>
                    <Textarea
                      id={`${id}-${field}`}
                      rows={6}
                      className="font-mono text-xs"
                      aria-describedby={`${id}-bindings-help`}
                      {...form.register(field)}
                      disabled={!canPrepare}
                      spellCheck={false}
                    />
                    <p id={`${id}-bindings-help`} className="text-muted-foreground text-sm">
                      {t("bindingsHelp")}
                    </p>
                  </>
                ) : (
                  <Input
                    id={`${id}-${field}`}
                    inputMode={field === "item" ? "text" : "numeric"}
                    {...form.register(field)}
                    disabled={!canPrepare}
                    spellCheck={false}
                  />
                )}
              </div>
            ))}
          </>
        )}
        {Object.keys(form.formState.errors).length > 0 && (
          <p role="alert" className="text-danger-fg text-sm">
            {t("invalidInput")}
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
          {t(
            !reviewedTree
              ? "parentRequired"
              : props.loading
                ? "loading"
                : props.artifacts
                  ? "unavailable"
                  : "unloaded"
          )}
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
            { term: t("fileCount"), description: artifacts.fileCount },
            { term: t("sourceObservedAt"), description: artifacts.sourceObservedAt },
            ...("payloadDigest" in artifacts
              ? [
                  { term: t("payloadDigest"), description: code(artifacts.payloadDigest) },
                  { term: t("payloadBytes"), description: artifacts.payloadBytes },
                  { term: t("resourceCount"), description: artifacts.resourceCount },
                  { term: t("imageUseCount"), description: artifacts.imageUseCount },
                  { term: t("rewriteCount"), description: artifacts.rewriteCount },
                ]
              : [
                  { term: t("executionDigest"), description: code(artifacts.executionDigest) },
                  { term: t("bytes"), description: artifacts.bytes },
                  { term: t("phaseCount"), description: artifacts.phaseCount },
                  { term: t("skippedPhaseCount"), description: artifacts.skippedPhaseCount },
                  { term: t("importCount"), description: artifacts.importCount },
                ]),
            { term: contextText("observedAt"), description: artifacts.observedAt },
          ]}
        />
      )}
    </Section>
  );
}
