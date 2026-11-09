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
import {
  installPlanTreeFormSchema,
  installPlanTreeRequest,
  installPlanTreeSchema,
  validateInstallPlanTree,
  type NvkeInstallPlanTree,
  type NvkeInstallPlanTreeForm,
} from "@/lib/nvke/operator-install-plan-tree";
import {
  validateInstallPlanCharts,
  type NvkeInstallPlanCharts,
} from "@/lib/nvke/operator-install-plan-charts";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import type { NvkeOperatorInstallPlanPanelProps } from "./NvkeOperatorInstallPlanPanel";

export interface NvkeOperatorInstallPlanTreePanelProps extends Pick<
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
  charts: NvkeInstallPlanCharts | null;
  artifacts: NvkeInstallPlanTree | null;
  retainedArtifactId: string | null;
  canRetry: boolean;
  allowOriginalPreparation?: boolean;
  onPrepare: (values: NvkeInstallPlanTreeForm) => Promise<boolean>;
  onReview: (artifactId: string) => Promise<boolean>;
  onRetryPreparation: (artifactId: string) => Promise<boolean>;
  onChooseArtifact?: () => Promise<void>;
}

export function NvkeOperatorInstallPlanTreePanel(props: NvkeOperatorInstallPlanTreePanelProps) {
  return (
    <TreeControls
      key={JSON.stringify([
        props.scopeKey,
        props.site,
        props.operation.id,
        props.operation.version,
        props.operation.status,
        props.authority,
        props.canReview,
        props.plan,
        props.charts,
      ])}
      {...props}
    />
  );
}

function TreeControls(props: NvkeOperatorInstallPlanTreePanelProps) {
  const t = useTranslations("nvke.installTree"),
    j = useTranslations("nvke.installReview");
  const id = useId();
  const [retained, setRetained] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [unconfirmed, setUnconfirmed] = useState(false);
  const form = useForm<NvkeInstallPlanTreeForm>({
    resolver: zodResolver(installPlanTreeFormSchema),
    defaultValues: {
      artifactId: "",
      maximumBytes: "",
    },
  });
  const parsed = installPlanSchema.safeParse(props.plan),
    authority = props.authority;
  const plan = parsed.success ? parsed.data : null;
  const planReady =
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
  let charts: NvkeInstallPlanCharts | null = null;
  if (planReady && plan && props.charts) {
    try {
      charts = validateInstallPlanCharts(props.charts, plan, props.charts.id);
    } catch {
      /* Invalidated parent. */
    }
  }
  const sourceReady = planReady && !!charts;
  const parsedArtifacts = installPlanTreeSchema.safeParse(props.artifacts);
  let candidate: NvkeInstallPlanTree | null = null;
  if (sourceReady && plan && charts && parsedArtifacts.success) {
    try {
      candidate = validateInstallPlanTree(
        parsedArtifacts.data,
        plan,
        charts,
        parsedArtifacts.data.id
      );
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
  async function run(action: "prepare" | "review" | "retry", values?: NvkeInstallPlanTreeForm) {
    const artifactId = savedId ?? values?.artifactId ?? form.getValues("artifactId");
    if (
      !validNvkeGuid(artifactId) ||
      artifactId === props.operation.id ||
      artifactId === plan?.id ||
      artifactId === charts?.id
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
    if (action === "prepare") {
      try {
        if (!values || !plan || !charts) return;
        installPlanTreeRequest(values, plan, charts);
      } catch {
        form.setError("maximumBytes", { type: "budget" });
        return;
      }
    }
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
      });
      setBusy(false);
    }
  }
  return (
    <Section title={t("title")} description={t("help")}>
      <p className="text-warning-fg text-sm">{j("originalUncertain")}</p>
      <p className="text-muted-foreground text-sm">{t("verificationRequired")}</p>
      {sourceReady && plan && charts && (
        <DefinitionList
          items={[
            {
              term: t("planId"),
              description: <code className="text-xs [overflow-wrap:anywhere]">{plan.id}</code>,
            },
            { term: t("planVersion"), description: plan.version },
            {
              term: t("chartId"),
              description: <code className="text-xs [overflow-wrap:anywhere]">{charts.id}</code>,
            },
            { term: t("chartVersion"), description: charts.version },
            {
              term: t("chartDigest"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{charts.artifactDigest}</code>
              ),
            },
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
            <div className="max-w-xl space-y-2">
              <Label htmlFor={`${id}-maximumBytes`}>{t("treeLimit")}</Label>
              <Input
                id={`${id}-maximumBytes`}
                inputMode="numeric"
                {...form.register("maximumBytes")}
                disabled={!canPrepare}
                spellCheck={false}
              />
            </div>
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
              term: t("treeDigest"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{artifacts.treeDigest}</code>
              ),
            },
            { term: t("fileCount"), description: artifacts.fileCount },
            { term: t("treeBytes"), description: <code>{artifacts.bytes}</code> },
          ]}
        />
      )}
    </Section>
  );
}
