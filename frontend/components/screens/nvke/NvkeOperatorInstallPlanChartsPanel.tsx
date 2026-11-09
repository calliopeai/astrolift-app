"use client";
import { useEffect, useId, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { installPlanMatches, installPlanSchema } from "@/lib/nvke/operator-install-plan";
import {
  installPlanChartsFormSchema,
  installPlanChartsSchema,
  validateInstallPlanCharts,
  type NvkeInstallPlanCharts,
  type NvkeInstallPlanChartsForm,
} from "@/lib/nvke/operator-install-plan-charts";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import type { NvkeOperatorInstallPlanPanelProps } from "./NvkeOperatorInstallPlanPanel";

export interface NvkeOperatorInstallPlanChartsPanelProps extends Pick<
  NvkeOperatorInstallPlanPanelProps,
  | "scopeKey"
  | "site"
  | "operation"
  | "authority"
  | "canReview"
  | "pending"
  | "loading"
  | "error"
  | "plan"
> {
  artifacts: NvkeInstallPlanCharts | null;
  retainedArtifactId: string | null;
  canRetry: boolean;
  allowOriginalPreparation?: boolean;
  onPrepare: (values: NvkeInstallPlanChartsForm) => Promise<boolean>;
  onReview: (artifactId: string) => Promise<boolean>;
  onRetryPreparation: (artifactId: string) => Promise<boolean>;
  onChooseArtifact?: () => Promise<void>;
}

export function NvkeOperatorInstallPlanChartsPanel(props: NvkeOperatorInstallPlanChartsPanelProps) {
  return (
    <ChartControls
      key={JSON.stringify([
        props.scopeKey,
        props.site,
        props.operation.id,
        props.operation.version,
        props.operation.status,
        props.authority,
        props.canReview,
        props.plan,
      ])}
      {...props}
    />
  );
}

function ChartControls(props: NvkeOperatorInstallPlanChartsPanelProps) {
  const t = useTranslations("nvke.installCharts"),
    j = useTranslations("nvke.installReview");
  const id = useId();
  const [retained, setRetained] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [unconfirmed, setUnconfirmed] = useState(false);
  const form = useForm<NvkeInstallPlanChartsForm>({
    resolver: zodResolver(installPlanChartsFormSchema),
    defaultValues: {
      artifactId: "",
      maximumBytes: "",
      maximumExpandedBytes: "",
      timeoutSeconds: "300",
      allowPlainHttp: false,
    },
  });
  const parsed = installPlanSchema.safeParse(props.plan),
    authority = props.authority;
  const plan = parsed.success ? parsed.data : null;
  const sourceReady =
    !!props.scopeKey &&
    props.canReview &&
    props.operation.siteId === props.site.id &&
    props.operation.operationKey === "tooling.management.install" &&
    props.operation.status === "UNCERTAIN" &&
    !!authority &&
    authority.siteId === props.site.id &&
    authority.siteVersion === props.site.version &&
    authority.isEnabled &&
    authority.deletedAt === null &&
    authority.hasCredentials &&
    authority.verificationCode === "AGENT_VERIFIED" &&
    !!authority.verifiedAt &&
    Number.isFinite(Date.parse(authority.verifiedAt)) &&
    !!plan &&
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
    });
  const parsedArtifacts = installPlanChartsSchema.safeParse(props.artifacts);
  let candidate: NvkeInstallPlanCharts | null = null;
  if (sourceReady && plan && parsedArtifacts.success) {
    try {
      candidate = validateInstallPlanCharts(parsedArtifacts.data, plan, parsedArtifacts.data.id);
    } catch {
      /* The previous result is hidden after invalidation. */
    }
  }
  const savedId = sourceReady
    ? (props.retainedArtifactId ?? retained ?? candidate?.id ?? null)
    : null;
  useEffect(() => {
    if (savedId) form.setValue("artifactId", savedId);
  }, [savedId, form]);
  const artifacts =
    !props.loading && !props.error && !unconfirmed && candidate?.id === savedId ? candidate : null;
  const enabled = sourceReady && !busy && !props.pending && !props.loading;
  const canPrepare =
    enabled &&
    (props.allowOriginalPreparation ||
      (!savedId && !props.error && !unconfirmed && props.artifacts === null));
  async function run(action: "prepare" | "review" | "retry", values?: NvkeInstallPlanChartsForm) {
    const artifactId = savedId ?? values?.artifactId ?? form.getValues("artifactId");
    if (
      !validNvkeGuid(artifactId) ||
      artifactId === props.operation.id ||
      artifactId === plan?.id
    ) {
      form.setError("artifactId", { type: "identity" });
      return;
    }
    if (
      !enabled ||
      (action === "prepare" && (!values || values.artifactId !== artifactId || !canPrepare)) ||
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
        maximumExpandedBytes: "",
        timeoutSeconds: "300",
        allowPlainHttp: false,
      });
      setBusy(false);
    }
  }
  return (
    <Section title={t("title")} description={t("help")}>
      <p className="text-warning-fg text-sm">{j("originalUncertain")}</p>
      <p className="text-muted-foreground text-sm">{t("verificationRequired")}</p>
      {sourceReady && plan && (
        <DefinitionList
          items={[
            {
              term: t("planId"),
              description: <code className="text-xs [overflow-wrap:anywhere]">{plan.id}</code>,
            },
            { term: t("planVersion"), description: plan.version },
            {
              term: t("planDigest"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{plan.plan.planDigest}</code>
              ),
            },
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
          <Label htmlFor={`${id}-artifact`}>{t("artifactId")}</Label>
          <Input
            id={`${id}-artifact`}
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
                if (!canPrepare || !globalThis.crypto?.randomUUID) return;
                form.setValue("artifactId", globalThis.crypto.randomUUID(), {
                  shouldValidate: true,
                });
              }}
            >
              {t("newId")}
            </Button>
          )}
        </div>
        {(!savedId || props.allowOriginalPreparation) && (
          <>
            <div className="grid gap-3 md:grid-cols-3">
              {(
                [
                  ["maximumBytes", "archiveLimit"],
                  ["maximumExpandedBytes", "expandedLimit"],
                  ["timeoutSeconds", "timeout"],
                ] as const
              ).map(([field, label]) => (
                <div key={field} className="space-y-2">
                  <Label htmlFor={`${id}-${field}`}>{t(label)}</Label>
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
            <Controller
              control={form.control}
              name="allowPlainHttp"
              render={({ field }) => (
                <div className="flex items-center gap-2">
                  <Checkbox
                    id={`${id}-http`}
                    checked={field.value}
                    onCheckedChange={(value) => field.onChange(value === true)}
                    disabled={!canPrepare}
                    ref={field.ref}
                  />
                  <Label htmlFor={`${id}-http`}>{t("allowHttp")}</Label>
                </div>
              )}
            />
            <p className="text-muted-foreground text-sm">
              {t(props.allowOriginalPreparation ? "originalOnly" : "budgetHelp")}
            </p>
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
              {t("choose")}
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
          {t("unconfirmed")}
        </p>
      )}
      {!artifacts && !props.error && !unconfirmed && (
        <p role="status" className="text-muted-foreground text-sm">
          {t(
            !sourceReady
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
            {
              term: t("artifactId"),
              description: <code className="text-xs [overflow-wrap:anywhere]">{artifacts.id}</code>,
            },
            {
              term: t("artifactDigest"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{artifacts.artifactDigest}</code>
              ),
            },
            { term: t("chartCount"), description: artifacts.chartCount },
            { term: t("archiveBytes"), description: <code>{artifacts.bytes}</code> },
            { term: t("expandedBytes"), description: <code>{artifacts.expandedBytes}</code> },
          ]}
        />
      )}
    </Section>
  );
}
