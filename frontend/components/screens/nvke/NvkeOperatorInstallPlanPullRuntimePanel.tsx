"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { DefinitionList } from "@/components/ui/definition-list";
import { installPlanMatches, installPlanSchema } from "@/lib/nvke/operator-install-plan";
import {
  installPlanPullRuntimeSource,
  validateInstallPlanPullRuntime,
  type NvkeInstallPlanPullRuntime,
} from "@/lib/nvke/operator-install-plan-pull-runtime";
import type { NvkeInstallPlanPullSecretAnalysis } from "@/lib/nvke/operator-install-plan-pull-secret-analysis";
import type { NvkeInstallPlanNodePlatforms } from "@/lib/nvke/operator-install-plan-node-platforms";
import type { NvkeOperatorInstallPlanPullSecretAnalysisPanelProps } from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel";

export interface NvkeOperatorInstallPlanPullRuntimePanelProps extends Pick<
  NvkeOperatorInstallPlanPullSecretAnalysisPanelProps,
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
  | "workload"
  | "binding"
  | "pullAuthority"
> {
  analysis: NvkeInstallPlanPullSecretAnalysis | null;
  nodePlatforms: NvkeInstallPlanNodePlatforms | null;
  correspondence: NvkeInstallPlanPullRuntime | null;
  onReview: () => Promise<boolean>;
}

export function NvkeOperatorInstallPlanPullRuntimePanel(
  props: NvkeOperatorInstallPlanPullRuntimePanelProps
) {
  return (
    <PullRuntimeControls
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
        props.analysis,
        props.nodePlatforms,
      ])}
      {...props}
    />
  );
}

function PullRuntimeControls(props: NvkeOperatorInstallPlanPullRuntimePanelProps) {
  const t = useTranslations("nvke.installPullRuntime");
  const [busy, setBusy] = useState(false),
    [unconfirmed, setUnconfirmed] = useState(false);
  const busyRef = useRef(false);
  let source: ReturnType<typeof installPlanPullRuntimeSource> | null = null;
  let result: NvkeInstallPlanPullRuntime | null = null;
  const parsed = installPlanSchema.safeParse(props.plan),
    authority = props.authority;
  if (
    parsed.success &&
    props.scopeKey &&
    props.canReview &&
    authority &&
    props.charts &&
    props.tree &&
    props.workload &&
    props.binding &&
    props.pullAuthority &&
    props.analysis &&
    props.nodePlatforms &&
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
    installPlanMatches(parsed.data, {
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
      source = installPlanPullRuntimeSource(
        parsed.data,
        props.charts,
        props.tree,
        props.workload,
        props.binding,
        props.pullAuthority,
        props.analysis,
        props.nodePlatforms
      );
      if (
        props.correspondence &&
        !props.loading &&
        !props.pending &&
        !props.error &&
        !busy &&
        !unconfirmed
      )
        result = validateInstallPlanPullRuntime(props.correspondence, source);
    } catch {
      // A stale result cannot stand in for the selected parent captures.
    }
  }
  const enabled = !!source && !busy && !props.pending && !props.loading;
  async function review() {
    if (!enabled || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setUnconfirmed(false);
    try {
      setUnconfirmed(!(await props.onReview()));
    } catch {
      setUnconfirmed(true);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  const code = (value: string) => <code className="text-xs [overflow-wrap:anywhere]">{value}</code>;
  return (
    <Section title={t("title")} description={t("help")}>
      {!source && (
        <p role="status" className="text-muted-foreground text-sm">
          {t("parentsRequired")}
        </p>
      )}
      {(props.error || unconfirmed) && (
        <p role="alert" className="text-warning-fg text-sm">
          {t("unconfirmed")}
        </p>
      )}
      {source && (
        <DefinitionList
          items={[
            {
              term: t("analysis"),
              description: code(
                `${source.analysis.id} · v${source.analysis.version} · ${source.analysis.authorityDigest}`
              ),
            },
            {
              term: t("nodePlatforms"),
              description: code(
                `${source.nodePlatforms.id} · v${source.nodePlatforms.version} · ${source.nodePlatforms.platformDigest}`
              ),
            },
          ]}
        />
      )}
      <Button variant="outline" disabled={!enabled} onClick={() => void review()}>
        {busy || props.pending ? t("reviewing") : t("review")}
      </Button>
      {result && (
        <DefinitionList
          items={[
            { term: t("matchedImages"), description: String(result.imageUseCount) },
            { term: t("matchedNodes"), description: String(result.nodeCount) },
            { term: t("sourceObservedAt"), description: code(result.sourceObservedAt) },
            { term: t("nodeObservedAt"), description: code(result.nodeObservedAt) },
            { term: t("digest"), description: code(result.correspondenceDigest) },
          ]}
        />
      )}
      <p className="text-warning-fg text-sm">{t("closureRequired")}</p>
    </Section>
  );
}
