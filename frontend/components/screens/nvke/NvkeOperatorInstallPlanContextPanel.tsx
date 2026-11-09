"use client";
import { useEffect, useId, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { installPlanMatches, installPlanSchema } from "@/lib/nvke/operator-install-plan";
import { validateInstallPlanCharts } from "@/lib/nvke/operator-install-plan-charts";
import {
  validateInstallPlanTree,
  type NvkeInstallPlanTree,
} from "@/lib/nvke/operator-install-plan-tree";
import {
  installPlanContextFormSchema,
  installPlanContextRequest,
  installPlanContextSelectionSchema,
  validateInstallPlanContext,
  type NvkeInstallPlanContext,
  type NvkeInstallPlanContextForm,
  type NvkeInstallPlanContextSelection,
} from "@/lib/nvke/operator-install-plan-context";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import type { NvkeOperatorInstallPlanTreePanelProps } from "./NvkeOperatorInstallPlanTreePanel";

export interface NvkeOperatorInstallPlanContextPanelProps extends Pick<
  NvkeOperatorInstallPlanTreePanelProps,
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
> {
  tree: NvkeInstallPlanTree | null;
  artifacts: NvkeInstallPlanContext | null;
  retainedArtifactId: string | null;
  retainedSelection: NvkeInstallPlanContextSelection | null;
  canRetry: boolean;
  allowOriginalPreparation?: boolean;
  onPrepare: (values: NvkeInstallPlanContextForm) => Promise<boolean>;
  onReview: (artifactId: string, selection: NvkeInstallPlanContextSelection) => Promise<boolean>;
  onRetryPreparation: (
    artifactId: string,
    selection: NvkeInstallPlanContextSelection
  ) => Promise<boolean>;
  onChooseArtifact?: () => Promise<void>;
}

const defaults: NvkeInstallPlanContextForm = {
  artifactId: "",
  item: "",
  ordinal: "",
  revision: "1",
  upgrade: false,
  maximumBytes: "16777216",
  maximumArtifactBytes: "1048576",
  maximumCalls: "512",
  maximumIterations: "64",
  timeoutSeconds: "300",
};
const captureFields = [
  "revision",
  "maximumBytes",
  "maximumArtifactBytes",
  "maximumCalls",
  "maximumIterations",
  "timeoutSeconds",
] as const;

export function NvkeOperatorInstallPlanContextPanel(
  props: NvkeOperatorInstallPlanContextPanelProps
) {
  return (
    <ContextControls
      key={JSON.stringify([
        props.scopeKey,
        props.site,
        props.operation,
        props.authority,
        props.canReview,
        props.plan,
        props.charts,
        props.tree,
      ])}
      {...props}
    />
  );
}

function ContextControls(props: NvkeOperatorInstallPlanContextPanelProps) {
  const t = useTranslations("nvke.installContext"),
    common = useTranslations("nvke.installTree"),
    review = useTranslations("nvke.installReview");
  const id = useId();
  const [retained, setRetained] = useState<string | null>(null);
  const [localSelection, setLocalSelection] = useState<NvkeInstallPlanContextSelection | null>(
    null
  );
  const [busy, setBusy] = useState(false),
    [unconfirmed, setUnconfirmed] = useState(false);
  const form = useForm<NvkeInstallPlanContextForm>({
    resolver: zodResolver(installPlanContextFormSchema),
    defaultValues: defaults,
  });
  const upgrade = useWatch({ control: form.control, name: "upgrade" });
  const parsed = installPlanSchema.safeParse(props.plan),
    authority = props.authority;
  const plan = parsed.success ? parsed.data : null;
  let sourceReady = false;
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
      const charts = validateInstallPlanCharts(props.charts, plan, props.charts.id);
      validateInstallPlanTree(props.tree, plan, charts, props.tree.id);
      sourceReady = true;
    } catch {
      /* Invalidated parent. */
    }
  }
  const retainedSelection = props.retainedSelection ?? localSelection;
  let candidate: NvkeInstallPlanContext | null = null;
  if (sourceReady && plan && props.charts && props.tree && props.artifacts && retainedSelection) {
    try {
      candidate = validateInstallPlanContext(
        props.artifacts,
        plan,
        props.charts,
        props.tree,
        props.artifacts.id,
        retainedSelection
      );
    } catch {
      /* Invalidated result. */
    }
  }
  const savedId = sourceReady
    ? (props.retainedArtifactId ?? retained ?? candidate?.id ?? null)
    : null;
  useEffect(() => {
    if (savedId) form.setValue("artifactId", savedId);
    if (savedId && retainedSelection) {
      form.setValue("item", retainedSelection.item);
      form.setValue("ordinal", String(retainedSelection.ordinal));
    }
  }, [savedId, retainedSelection, form]);
  const artifacts =
    !props.loading && !props.error && !unconfirmed && candidate?.id === savedId ? candidate : null;
  const enabled = sourceReady && !busy && !props.pending && !props.loading;
  const canPrepare =
    enabled &&
    (props.allowOriginalPreparation ||
      (!savedId && !props.error && !unconfirmed && props.artifacts === null));
  async function run(action: "prepare" | "review" | "retry", values?: NvkeInstallPlanContextForm) {
    const artifactId = savedId ?? values?.artifactId ?? form.getValues("artifactId");
    let selection: NvkeInstallPlanContextSelection;
    try {
      const ordinal = values?.ordinal ?? form.getValues("ordinal");
      if (savedId && retainedSelection)
        selection = installPlanContextSelectionSchema.parse(retainedSelection);
      else {
        if (!/^(0|[1-9][0-9]{0,3})$/.test(ordinal) || ordinal.trim() !== ordinal)
          throw new Error("INVALID_ITEM");
        selection = installPlanContextSelectionSchema.parse({
          item: values?.item ?? form.getValues("item"),
          ordinal: Number(ordinal),
        });
      }
      if (
        !plan ||
        selection.ordinal >= plan.itemCount ||
        !validNvkeGuid(artifactId) ||
        new Set([artifactId, plan.id, plan.operationId, props.charts?.id, props.tree?.id]).size !==
          5
      )
        throw new Error("INVALID_SOURCE");
      if (action === "prepare") {
        if (
          !values ||
          !props.charts ||
          !props.tree ||
          values.artifactId !== artifactId ||
          values.item !== selection.item ||
          Number(values.ordinal) !== selection.ordinal
        )
          throw new Error("INVALID_INPUT");
        installPlanContextRequest(values, plan, props.charts, props.tree);
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
    setLocalSelection(selection);
    try {
      const ok =
        action === "prepare"
          ? await props.onPrepare(values!)
          : action === "review"
            ? await props.onReview(artifactId, selection)
            : await props.onRetryPreparation(artifactId, selection);
      setUnconfirmed(!ok);
    } catch {
      setUnconfirmed(true);
    } finally {
      form.reset({
        artifactId,
        item: selection.item,
        ordinal: String(selection.ordinal),
        revision: "",
        upgrade: false,
        maximumBytes: "",
        maximumArtifactBytes: "",
        maximumCalls: "",
        maximumIterations: "",
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
      {sourceReady && props.tree && (
        <DefinitionList
          items={[
            { term: t("treeId"), description: code(props.tree.id) },
            { term: t("treeVersion"), description: props.tree.version },
            { term: t("treeDigest"), description: code(props.tree.treeDigest) },
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
        {(["item", "ordinal"] as const).map((field) => (
          <div key={field} className="max-w-xl space-y-2">
            <Label htmlFor={`${id}-${field}`}>{t(field)}</Label>
            <Input
              id={`${id}-${field}`}
              inputMode={field === "ordinal" ? "numeric" : "text"}
              {...form.register(field)}
              disabled={!enabled || !!savedId}
              spellCheck={false}
            />
          </div>
        ))}
        {(!savedId || props.allowOriginalPreparation) && (
          <>
            <p className="text-muted-foreground text-sm">
              {t(props.allowOriginalPreparation ? "originalOnly" : "settingsHelp")}
            </p>
            {captureFields.map((field) => (
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
            <div className="flex items-center gap-2">
              <Checkbox
                id={`${id}-upgrade`}
                checked={upgrade}
                onCheckedChange={(value) =>
                  form.setValue("upgrade", value === true, { shouldValidate: true })
                }
                disabled={!canPrepare}
              />
              <Label htmlFor={`${id}-upgrade`}>{t("upgrade")}</Label>
            </div>
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
            { term: common("artifactId"), description: code(artifacts.id) },
            { term: t("item"), description: artifacts.item },
            { term: t("ordinal"), description: artifacts.ordinal },
            { term: t("captureDigest"), description: code(artifacts.captureDigest) },
            { term: t("frozenChartSha256"), description: code(artifacts.frozenChartSha256) },
            { term: t("contextDigest"), description: code(artifacts.contextDigest) },
            { term: t("documentCount"), description: artifacts.documentCount },
            { term: t("lookupCount"), description: artifacts.lookupCount },
            { term: t("observedAt"), description: artifacts.observedAt },
          ]}
        />
      )}
    </Section>
  );
}
