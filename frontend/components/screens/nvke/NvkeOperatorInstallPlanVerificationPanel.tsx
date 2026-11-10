"use client";

import { useEffect, useId, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { installPlanMatches, installPlanSchema } from "@/lib/nvke/operator-install-plan";
import { validateInstallPlanCharts } from "@/lib/nvke/operator-install-plan-charts";
import { validateInstallPlanTree } from "@/lib/nvke/operator-install-plan-tree";
import {
  validateInstallPlanContext,
  type NvkeInstallPlanContext,
  type NvkeInstallPlanContextSelection,
} from "@/lib/nvke/operator-install-plan-context";
import {
  installPlanVerificationFormSchema,
  installPlanVerificationRequest,
  validateInstallPlanVerification,
  type NvkeInstallPlanVerification,
  type NvkeInstallPlanVerificationForm,
} from "@/lib/nvke/operator-install-plan-verification";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import type { NvkeOperatorInstallPlanContextPanelProps } from "./NvkeOperatorInstallPlanContextPanel";

export interface NvkeOperatorInstallPlanVerificationPanelProps extends Pick<
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
  context: NvkeInstallPlanContext | null;
  contextSelection: NvkeInstallPlanContextSelection | null;
  artifacts: NvkeInstallPlanVerification | null;
  retainedArtifactId: string | null;
  canRetry: boolean;
  allowOriginalPreparation?: boolean;
  onPrepare: (values: NvkeInstallPlanVerificationForm) => Promise<boolean>;
  onReview: (artifactId: string) => Promise<boolean>;
  onRetryPreparation: (artifactId: string) => Promise<boolean>;
  onChooseArtifact?: () => Promise<void>;
}

const defaults: NvkeInstallPlanVerificationForm = {
  artifactId: "",
  maximumBytes: "16777216",
  maximumCaptureAgeSeconds: "3600",
  maximumCalls: "512",
  timeoutSeconds: "300",
};
const settings = [
  "maximumBytes",
  "maximumCaptureAgeSeconds",
  "maximumCalls",
  "timeoutSeconds",
] as const;

export function NvkeOperatorInstallPlanVerificationPanel(
  props: NvkeOperatorInstallPlanVerificationPanelProps
) {
  return (
    <VerificationControls
      key={JSON.stringify([
        props.scopeKey,
        props.site,
        props.operation,
        props.authority,
        props.canReview,
        props.plan,
        props.charts,
        props.tree,
        props.context,
        props.contextSelection,
      ])}
      {...props}
    />
  );
}

function VerificationControls(props: NvkeOperatorInstallPlanVerificationPanelProps) {
  const t = useTranslations("nvke.installVerification"),
    common = useTranslations("nvke.installTree"),
    contextText = useTranslations("nvke.installContext"),
    review = useTranslations("nvke.installReview");
  const id = useId();
  const [retained, setRetained] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [unconfirmed, setUnconfirmed] = useState(false);
  const form = useForm<NvkeInstallPlanVerificationForm>({
    resolver: zodResolver(installPlanVerificationFormSchema),
    defaultValues: defaults,
  });
  const parsed = installPlanSchema.safeParse(props.plan),
    plan = parsed.success ? parsed.data : null,
    authority = props.authority;
  let context: NvkeInstallPlanContext | null = null;
  if (
    plan &&
    props.charts &&
    props.tree &&
    props.context &&
    props.contextSelection &&
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
      context = validateInstallPlanContext(
        props.context,
        plan,
        charts,
        tree,
        props.context.id,
        props.contextSelection
      );
    } catch {
      /* The selected context no longer belongs to this reviewed source. */
    }
  }
  let candidate: NvkeInstallPlanVerification | null = null;
  if (context && plan && props.charts && props.tree && props.artifacts) {
    try {
      candidate = validateInstallPlanVerification(
        props.artifacts,
        plan,
        props.charts,
        props.tree,
        context,
        props.artifacts.id
      );
    } catch {
      /* A stale observation cannot confirm this source. */
    }
  }
  const savedId = context ? (props.retainedArtifactId ?? retained ?? candidate?.id ?? null) : null;
  useEffect(() => {
    if (savedId) form.setValue("artifactId", savedId);
  }, [savedId, form]);
  const artifacts =
    !props.loading && !props.error && !unconfirmed && candidate?.id === savedId ? candidate : null;
  const enabled = !!context && !busy && !props.pending && !props.loading;
  const canPrepare =
    enabled &&
    (props.allowOriginalPreparation ||
      (!savedId && !props.error && !unconfirmed && props.artifacts === null));

  async function run(
    action: "prepare" | "review" | "retry",
    values?: NvkeInstallPlanVerificationForm
  ) {
    const artifactId = savedId ?? values?.artifactId ?? form.getValues("artifactId");
    try {
      if (
        !context ||
        !plan ||
        !props.charts ||
        !props.tree ||
        !validNvkeGuid(artifactId) ||
        new Set([artifactId, context.id, plan.id, plan.operationId, props.charts.id, props.tree.id])
          .size !== 6
      )
        throw new Error("INVALID_SOURCE");
      if (action === "prepare") {
        if (!values || values.artifactId !== artifactId) throw new Error("INVALID_INPUT");
        installPlanVerificationRequest(values, plan, props.charts, props.tree, context);
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
        maximumCaptureAgeSeconds: "",
        maximumCalls: "",
        timeoutSeconds: "",
      });
      setBusy(false);
    }
  }

  const code = (value: string) => <code className="text-xs [overflow-wrap:anywhere]">{value}</code>;
  return (
    <Section title={t("title")} description={t("help")}>
      <p className="text-warning-fg text-sm">{review("originalUncertain")}</p>
      <p className="text-muted-foreground text-sm">{t("verificationRequired")}</p>
      {context && (
        <DefinitionList
          items={[
            { term: t("contextId"), description: code(context.id) },
            { term: t("contextVersion"), description: context.version },
            { term: contextText("contextDigest"), description: code(context.contextDigest) },
            { term: contextText("item"), description: context.item },
            { term: contextText("ordinal"), description: context.ordinal },
          ]}
        />
      )}
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
                <Input
                  id={`${id}-${field}`}
                  inputMode="numeric"
                  {...form.register(field)}
                  disabled={!canPrepare}
                  spellCheck={false}
                />
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
            !context
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
            { term: t("verificationDigest"), description: code(artifacts.verificationDigest) },
            { term: contextText("captureDigest"), description: code(artifacts.captureDigest) },
            {
              term: contextText("frozenChartSha256"),
              description: code(artifacts.frozenChartSha256),
            },
            { term: t("requests"), description: artifacts.requests },
            { term: t("bytes"), description: artifacts.bytes },
            { term: contextText("observedAt"), description: artifacts.observedAt },
          ]}
        />
      )}
    </Section>
  );
}
