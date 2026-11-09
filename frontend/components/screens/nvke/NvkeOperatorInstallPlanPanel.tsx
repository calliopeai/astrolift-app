"use client";
import { useEffect, useId, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { ClipboardListIcon } from "lucide-react";
import type { Column, CursorTableController } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { Textarea } from "@/components/ui/textarea";
import { installWorkloadId, type NvkeInstallWorkload } from "@/lib/nvke/operator-install-review";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import {
  installPlanFormSchema,
  installPlanSchema,
  installPlanMatches,
  type NvkeInstallPlan,
  type NvkeInstallPlanItem,
  type NvkeInstallPlanForm,
} from "@/lib/nvke/operator-install-plan";
import type { NvkeOperatorInstallReviewPanelProps } from "./NvkeOperatorInstallReviewPanel";

export interface NvkeOperatorInstallPlanPanelProps extends Pick<
  NvkeOperatorInstallReviewPanelProps,
  "scopeKey" | "site" | "operation" | "authority" | "canReview" | "pending" | "loading" | "error"
> {
  plan: NvkeInstallPlan | null;
  retainedPlanId: string | null;
  itemTable: CursorTableController<NvkeInstallPlanItem>;
  workloadTable: CursorTableController<NvkeInstallWorkload>;
  canRetry: boolean;
  allowOriginalPreparation?: boolean;
  onChoosePlan?: () => Promise<void>;
  onPrepare: (values: NvkeInstallPlanForm) => Promise<boolean>;
  onReview: (planId: string) => Promise<boolean>;
  onRetryPreparation: (planId: string) => Promise<boolean>;
}
export function NvkeOperatorInstallPlanPanel(props: NvkeOperatorInstallPlanPanelProps) {
  return (
    <PlanControls
      key={JSON.stringify([
        props.scopeKey,
        props.site,
        props.operation.id,
        props.operation.version,
        props.operation.status,
        props.authority,
        props.canReview,
      ])}
      {...props}
    />
  );
}
function PlanControls(props: NvkeOperatorInstallPlanPanelProps) {
  const t = useTranslations("nvke.installPlan"),
    j = useTranslations("nvke.installReview"),
    sourceCopy = useTranslations("nvke.operatorJob");
  const id = useId();
  const [retained, setRetained] = useState<string | null>(null);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const form = useForm<NvkeInstallPlanForm>({
    resolver: zodResolver(installPlanFormSchema),
    defaultValues: { planId: "", requestText: "" },
  });
  const { authority, site, operation, itemTable, workloadTable } = props;
  const sourceReady =
    !!props.scopeKey &&
    props.canReview &&
    operation.siteId === site.id &&
    operation.operationKey === "tooling.management.install" &&
    operation.status === "UNCERTAIN" &&
    !!authority &&
    authority.siteId === site.id &&
    authority.siteVersion === site.version &&
    authority.isEnabled &&
    authority.deletedAt === null &&
    authority.hasCredentials &&
    authority.verificationCode === "AGENT_VERIFIED" &&
    !!authority.verifiedAt &&
    Number.isFinite(Date.parse(authority.verifiedAt));
  const parsed = installPlanSchema.safeParse(props.plan);
  const candidate = parsed.success ? parsed.data : null;
  const savedId = props.retainedPlanId ?? retained ?? candidate?.id ?? null;
  useEffect(() => {
    if (savedId) form.setValue("planId", savedId);
  }, [savedId, form]);
  const plan =
    sourceReady &&
    !props.loading &&
    !props.error &&
    !unconfirmed &&
    candidate &&
    authority &&
    (!savedId || savedId === candidate.id) &&
    installPlanMatches(candidate, {
      siteId: site.id,
      siteVersion: site.version,
      operationId: operation.id,
      operationVersion: operation.version,
      authorityId: authority.id,
      authorityVersion: authority.version,
      instanceId: authority.instanceId,
      bundleManifestSha256: authority.bundleManifestSha256,
      nativeRevision: site.upstreamRevision,
    })
      ? candidate
      : null;
  const enabled = sourceReady && !busy && !props.pending && !props.loading;
  const itemList = useCursorTableList(itemTable, {
    id: "nvke.operator.install.plan.items",
    label: t("items"),
    searchPlaceholder: t("itemSearch"),
  });
  const workloadList = useCursorTableList(workloadTable, {
    id: "nvke.operator.install.plan.workloads",
    label: j("workloads"),
    searchPlaceholder: j("search"),
  });
  const itemColumns: Column<NvkeInstallPlanItem>[] = [
    { id: "ordinal", header: t("ordinal"), cell: (row) => row.ordinal },
    {
      id: "name",
      header: t("name"),
      cell: (row) => (
        <code className="block max-w-64 truncate" title={row.name}>
          {row.name}
        </code>
      ),
    },
    { id: "type", header: t("type"), cell: (row) => row.type },
    { id: "originalType", header: t("originalType"), cell: (row) => row.originalType },
    {
      id: "observation",
      header: j("observation"),
      cell: (row) =>
        t(
          row.originalObservation === "not-observed"
            ? "notObserved"
            : row.originalObservation === "last-observed-in-unsuccessful-run"
              ? "lastObserved"
              : "returned"
        ),
    },
    {
      id: "action",
      header: t("proposedAction"),
      cell: (row) => t(row.proposedAction === "skip" ? "skip" : "execute"),
    },
    {
      id: "digest",
      header: t("contentDigest"),
      cell: (row) => (
        <code className="block max-w-48 truncate" title={row.contentSha256}>
          {row.contentSha256}
        </code>
      ),
    },
    {
      id: "originalDigest",
      header: t("originalDigest"),
      cell: (row) => (
        <code className="block max-w-48 truncate" title={row.originalContentSha256}>
          {row.originalContentSha256}
        </code>
      ),
    },
  ];
  const workloadColumns: Column<NvkeInstallWorkload>[] = [
    { id: "namespace", header: t("namespace"), cell: (row) => row.namespace },
    { id: "kind", header: j("kind"), cell: (row) => row.kind },
    {
      id: "name",
      header: t("name"),
      cell: (row) => (
        <code className="block max-w-64 truncate" title={row.name}>
          {row.name}
        </code>
      ),
    },
    {
      id: "observation",
      header: j("observation"),
      cell: (row) => j(row.observation === "ready" ? "ready" : "requiresReview"),
    },
    ...(
      [
        ["generation", "generation"],
        ["observedGeneration", "observedGeneration"],
        ["desiredReplicas", "desired"],
        ["readyReplicas", "readyCount"],
        ["updatedReplicas", "updated"],
      ] as const
    ).map(([field, label]) => ({
      id: field,
      header: j(label),
      cell: (row: NvkeInstallWorkload) => <code>{row[field] ?? j("unknown")}</code>,
    })),
  ];
  async function run(action: "prepare" | "review" | "retry", values?: NvkeInstallPlanForm) {
    const planId = savedId ?? values?.planId ?? form.getValues("planId");
    if (!validNvkeGuid(planId) || planId === operation.id) {
      form.setError("planId", { type: "identity" });
      return;
    }
    if (
      !enabled ||
      !validNvkeGuid(planId) ||
      planId === operation.id ||
      (action === "prepare" &&
        (!values ||
          (!props.allowOriginalPreparation && (!!savedId || !!props.error || unconfirmed)))) ||
      (action === "retry" && !props.canRetry)
    )
      return;
    setBusy(true);
    setRetained(planId);
    try {
      const ok =
        action === "prepare"
          ? await props.onPrepare(values!)
          : action === "review"
            ? await props.onReview(planId)
            : await props.onRetryPreparation(planId);
      setUnconfirmed(!ok);
    } catch {
      setUnconfirmed(true);
    } finally {
      form.setValue("requestText", "");
      setBusy(false);
    }
  }
  return (
    <Section title={t("title")} description={t("help")}>
      <p className="text-warning-fg text-sm">{j("originalUncertain")}</p>
      <p className="text-muted-foreground text-sm">{t("evidenceHelp")}</p>
      <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
        {sourceCopy("review", {
          site: site.name,
          siteVersion: site.version,
          operation: operation.id,
          operationVersion: operation.version,
          authorityVersion: authority?.version ?? 0,
          instance: authority?.instanceId ?? "—",
          bundle: authority?.bundleManifestSha256 ?? "—",
          revision: site.upstreamRevision,
        })}
      </p>
      <form
        className="space-y-3"
        autoComplete="off"
        onSubmit={form.handleSubmit((values) => run("prepare", values))}
      >
        <div className="max-w-xl space-y-2">
          <Label htmlFor={`${id}-plan`}>{t("planId")}</Label>
          <Input
            id={`${id}-plan`}
            {...form.register("planId")}
            {...(savedId ? { value: savedId } : {})}
            disabled={!enabled || !!savedId}
            spellCheck={false}
          />
        </div>
        {(!savedId || props.allowOriginalPreparation) && (
          <div className="space-y-2">
            <Label htmlFor={`${id}-request`}>{t("privateRequest")}</Label>
            <Textarea
              id={`${id}-request`}
              {...form.register("requestText")}
              disabled={
                !enabled || (!props.allowOriginalPreparation && (!!props.error || unconfirmed))
              }
              spellCheck={false}
              autoComplete="off"
              rows={6}
            />
            <p className="text-muted-foreground text-sm">
              {t(props.allowOriginalPreparation ? "originalOnly" : "privateHelp")}
            </p>
          </div>
        )}
        {Object.keys(form.formState.errors).length > 0 && (
          <p role="alert" className="text-danger-fg text-sm">
            {t("invalidInput")}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            type="submit"
            disabled={
              !enabled ||
              (!props.allowOriginalPreparation && (!!savedId || !!props.error || unconfirmed))
            }
          >
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
          {props.onChoosePlan && (
            <Button
              type="button"
              variant="outline"
              disabled={!enabled}
              onClick={() => void props.onChoosePlan?.()}
            >
              {t("choosePlan")}
            </Button>
          )}
        </div>
      </form>
      {props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {props.error}
        </p>
      )}
      {unconfirmed && (
        <p role="alert" className="text-warning-fg text-sm">
          {t("unconfirmed")}
        </p>
      )}
      {!plan && !props.error && !unconfirmed && (
        <p role="status" className="text-muted-foreground text-sm">
          {props.loading ? t("loading") : props.plan ? t("unavailable") : t("unloaded")}
        </p>
      )}
      {plan && (
        <>
          <DefinitionList
            items={[
              {
                term: t("planId"),
                description: <code className="text-xs [overflow-wrap:anywhere]">{plan.id}</code>,
              },
              {
                term: j("digest"),
                description: (
                  <code className="text-xs [overflow-wrap:anywhere]">{plan.plan.planDigest}</code>
                ),
              },
              {
                term: t("provenance"),
                description: t(
                  plan.plan.source.originalDeploymentEvidence ===
                    "reconstructed-prepared-deployment"
                    ? "reconstructed"
                    : "retained"
                ),
              },
              { term: t("firstRemaining"), description: plan.plan.firstRemainingOrdinal },
              { term: t("retainedBytes"), description: <code>{plan.retainedBytes}</code> },
              { term: t("proposedBytes"), description: <code>{plan.proposedBytes}</code> },
              {
                term: j("observedAt"),
                description: (
                  <time dateTime={plan.infrastructure.observedAt}>
                    {plan.infrastructure.observedAt}
                  </time>
                ),
              },
              {
                term: j("nativeRun"),
                description: (
                  <code className="text-xs [overflow-wrap:anywhere]">
                    {plan.plan.source.nativeRunId}
                  </code>
                ),
              },
              { term: t("originalExit"), description: plan.plan.source.nativeExitCode },
            ]}
          />
          <h3 className="text-sm font-medium">{t("items")}</h3>
          <ListPage
            embedded
            list={itemList}
            rows={itemTable.state === "error" ? [] : itemTable.rows}
            label={t("items")}
            loading={itemTable.state === "loading"}
            stale={itemTable.isStale}
            error={itemTable.state === "error" ? { message: t("pageFailed") } : null}
            onRetry={itemTable.retry}
            nextCursor={itemTable.nextCursor}
            totalCount={itemTable.totalCount}
            columns={itemColumns}
            getRowId={(row) => row.name}
            empty={{ icon: <ClipboardListIcon />, title: t("emptyItems") }}
          />
          <h3 className="text-sm font-medium">{j("workloads")}</h3>
          <ListPage
            embedded
            list={workloadList}
            rows={workloadTable.state === "error" ? [] : workloadTable.rows}
            label={j("workloads")}
            loading={workloadTable.state === "loading"}
            stale={workloadTable.isStale}
            error={workloadTable.state === "error" ? { message: t("pageFailed") } : null}
            onRetry={workloadTable.retry}
            nextCursor={workloadTable.nextCursor}
            totalCount={workloadTable.totalCount}
            columns={workloadColumns}
            getRowId={installWorkloadId}
            empty={{ icon: <ClipboardListIcon />, title: j("empty") }}
          />
        </>
      )}
    </Section>
  );
}
