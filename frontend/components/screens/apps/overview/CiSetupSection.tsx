"use client";

import {
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  ExternalLinkIcon,
  FileCheck2Icon,
  KeyIcon,
  Loader2Icon,
  ShieldCheckIcon,
  TerminalIcon,
  UploadCloudIcon,
  WebhookIcon,
  XIcon,
} from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { AstroliftCiWorkflowSyncStatus } from "@/graphql/__generated__/schema";

import {
  formatRelativeWebhookInstall,
  renderWorkflowYaml,
  resolveProviderCiMeta,
} from "./ci-setup-meta";
import { WorkflowSyncStatusControl } from "./WorkflowSyncStatusControl";
import type { CiSetupData, usePushAndRotate } from "./use-ci-setup";

/**
 * CI setup section on the consolidated Settings page (#382, #854).
 *
 * Lists the GitHub Actions secrets an operator needs to wire CI to
 * Astrolift, with copy-buttons on the live values, and a paste-ready
 * reference workflow YAML keyed off those exact secret names. The
 * push-credential and registry rows + workflow steps are provider-aware
 * (``providerCiMeta`` keyed by the app's default-cluster slug), so a
 * GCP/Azure/raw-k8s app sees the right secret names instead of the AWS
 * ECR/IAM-role shape.
 *
 * The deploy-token value itself never appears here — that's a one-shot
 * reveal handled by ``DeployTokenControl`` on the tokens page. We render
 * the row as masked dots with a link out to the existing reveal flow so
 * there's exactly one place token plaintext can be observed.
 */
export interface CiSetupSectionViewProps extends CiSetupData {
  appSlug: string;
  /** Container-registry coordinate the build pushes to. ECR repo URI on
   *  AWS, Artifact Registry path on GCP, ACR login server on Azure, or a
   *  generic registry URI on raw-k8s. */
  registryUri: string;
  /** Reference to the push credential the CI runner uses. IAM role ARN on
   *  AWS, Workload Identity provider on GCP, federated client id on Azure.
   *  Empty until cluster bootstrap provisions it (#309). */
  pushCredentialRef: string;
  /** Provider-plugin slug of the app's default cluster (#854) — one of
   *  `aws` / `gcp` / `azure` / `k8s_native`. Drives the provider-correct
   *  secret names, hints, and reference workflow. Empty when no default
   *  cluster is bound yet; the workflow then requires a backend preview. */
  providerPluginSlug: string;
  deployBranch?: string | null;
  /** ISO timestamp of the last successful webhook install / refresh
   *  (#385). `null` until the operator clicks "Install webhook" for
   *  the first time. */
  sourceWebhookInstalledAt: string | null;
  /** Managed-CI-workflow versioned-sync/drift status (#1210). `null`
   *  when the app has never been versioned-synced and the detail
   *  resolver returned nothing. */
  ciWorkflowSyncStatus: AstroliftCiWorkflowSyncStatus | null;
  /** Agent packages sync source snapshots; they do not use the app image
   * build/deploy contract rendered by the normal CI card. */
  agentMode?: boolean;
  /** The app's deploy-tokens page (the one place a token is revealed). */
  tokensHref: string;
}

interface SecretRow {
  /** GitHub Actions secret name — what the workflow references. */
  name: string;
  /** Live value to copy. ``null`` means the value lives behind another flow. */
  value: string | null;
  /** Short description rendered as a tooltip on the row icon. */
  hint: string;
  /** When ``value`` is null, render an "open this flow" link instead of copy. */
  externalHref?: string;
  externalLabel?: string;
}

export function CiSetupSectionView({
  appSlug,
  registryUri,
  pushCredentialRef,
  providerPluginSlug,
  deployBranch,
  sourceWebhookInstalledAt,
  ciWorkflowSyncStatus,
  agentMode = false,
  tokensHref,
  apiUrl,
  apiUrlLoading,
  workflowSync,
  pushAndRotate,
  validate,
  syncFile,
  webhook,
}: CiSetupSectionViewProps) {
  const t = useTranslations("apps.overview.ciSetup");
  if (agentMode) {
    const automatic = Boolean(sourceWebhookInstalledAt);
    return (
      <Section
        title={
          <span className="flex items-center gap-2">
            <WebhookIcon className="text-primary size-4" />
            {t("agentTitle")}
          </span>
        }
        description={<>{t("agentDescription")}</>}
      >
        <div className="bg-card space-y-3 rounded-md border p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">{t("agentSyncTitle")}</p>
              <p className="text-muted-foreground text-xs">{t("agentSyncHint")}</p>
            </div>
            <span
              className={
                automatic
                  ? "bg-success/10 text-success-fg rounded-full px-2 py-1 text-xs font-medium"
                  : "bg-warning/10 text-warning-fg rounded-full px-2 py-1 text-xs font-medium"
              }
            >
              {automatic ? t("agentAutomatic") : t("notInstalled")}
            </span>
          </div>
          <div className="grid gap-2 text-xs md:grid-cols-3">
            <div className="bg-muted/40 rounded p-2">
              <p className="font-medium">{t("agentConfig")}</p>
              <p className="text-muted-foreground">{t("agentConfigHint")}</p>
            </div>
            <div className="bg-muted/40 rounded p-2">
              <p className="font-medium">{t("agentImage")}</p>
              <p className="text-muted-foreground">{t("agentImageHint")}</p>
            </div>
            <div className="bg-muted/40 rounded p-2">
              <p className="font-medium">{t("agentRuns")}</p>
              <p className="text-muted-foreground">{t("agentRunsHint")}</p>
            </div>
          </div>
        </div>

        {ciWorkflowSyncStatus ? (
          <WorkflowSyncStatusControl status={ciWorkflowSyncStatus} {...workflowSync} />
        ) : null}

        <Can permission="app.update">
          <SyncWorkflowFileAction {...syncFile} />
          <InstallSourceWebhookAction
            sourceWebhookInstalledAt={sourceWebhookInstalledAt}
            {...webhook}
          />
        </Can>
      </Section>
    );
  }

  const meta = resolveProviderCiMeta(providerPluginSlug);
  const providerKey = ["gcp", "azure", "k8s_native"].includes(providerPluginSlug)
    ? providerPluginSlug
    : "aws";

  const rows: SecretRow[] = [
    {
      name: meta.pushCredentialEnv,
      value: pushCredentialRef,
      hint: t(`providers.${providerKey}.pushHint`),
    },
    {
      name: meta.registryEnv,
      value: registryUri,
      hint: t(`providers.${providerKey}.registryHint`),
    },
    {
      name: "ASTROLIFT_APP_SLUG",
      value: appSlug,
      hint: t("appHint"),
    },
    {
      name: "ASTROLIFT_API_URL",
      value: apiUrl,
      hint: t("apiHint"),
    },
    {
      name: "ASTROLIFT_DEPLOY_TOKEN",
      value: null,
      hint: t("tokenHint"),
      externalHref: tokensHref,
      externalLabel: t("manageTokens"),
    },
  ];

  const workflowYaml = renderWorkflowYaml(meta, {
    providerSlug: providerPluginSlug,
    renderedText: ciWorkflowSyncStatus?.renderedText,
    deployBranch,
  });
  const managedWorkflow = !["gcp", "azure", "k8s_native"].includes(providerPluginSlug);

  async function copyYaml() {
    if (!workflowYaml) return;
    try {
      await navigator.clipboard.writeText(workflowYaml);
      toast.success(t("yamlCopied"));
    } catch {
      toast.error(t("copyTextFailed"));
    }
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <TerminalIcon className="text-primary size-4" />
          {t("title")}
        </span>
      }
      description={<>{t("description")}</>}
    >
      <TooltipProvider>
        <div className="bg-card overflow-hidden rounded-md border">
          {rows.map((row, idx) => (
            <SecretRowItem
              key={row.name}
              row={row}
              isLast={idx === rows.length - 1}
              loading={apiUrlLoading && row.name === "ASTROLIFT_API_URL"}
            />
          ))}
        </div>
      </TooltipProvider>

      <details className="group bg-muted/30 rounded-md border">
        <summary className="hover:bg-muted/50 flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium">
          <span className="flex items-center gap-2">
            <ChevronDownIcon className="size-4 transition-transform group-open:rotate-180" />
            {managedWorkflow ? t("managedWorkflow") : t("referenceWorkflow")}
          </span>
          <span className="text-muted-foreground text-2xs">
            {managedWorkflow ? (
              ciWorkflowSyncStatus?.path || t("reviewBeforeSync")
            ) : (
              <>
                {t.rich("pasteInto", {
                  path: (chunks) => <span className="font-mono">{chunks}</span>,
                })}
              </>
            )}
          </span>
        </summary>
        <div className="border-t">
          <pre className="bg-background text-2xs overflow-x-auto p-4 font-mono leading-relaxed">
            {workflowYaml ?? t("workflowUnavailable")}
          </pre>
          <div className="flex justify-end border-t p-3">
            <Button
              size="sm"
              variant="outline"
              onClick={copyYaml}
              disabled={!workflowYaml}
              className="gap-1.5"
            >
              <CopyIcon className="size-3.5" />
              {t("copyYaml")}
            </Button>
          </div>
        </div>
      </details>

      {ciWorkflowSyncStatus ? (
        <WorkflowSyncStatusControl status={ciWorkflowSyncStatus} {...workflowSync} />
      ) : null}

      <Can permission="app.update">
        <ValidateCiSecretsAction {...validate} />
        <PushAndRotateAction {...pushAndRotate} />
        <SyncWorkflowFileAction {...syncFile} />
        <InstallSourceWebhookAction
          sourceWebhookInstalledAt={sourceWebhookInstalledAt}
          {...webhook}
        />
      </Can>
    </Section>
  );
}

export interface PushAndRotateButtonViewProps extends ReturnType<typeof usePushAndRotate> {
  size?: "sm" | "default";
  variant?: "default" | "outline";
  label?: string;
}

/**
 * "Push & rotate" affordance — the button + ConfirmDialog round-trip.
 * Exported (#681) so the Secrets tab can mirror the action without
 * duplicating the mutation wiring. The Settings page wraps this in a
 * card-style block; the Secrets page uses just the button in its
 * action toolbar.
 */
export function PushAndRotateButtonView({
  pushing,
  onPushAndRotate,
  size = "sm",
  variant = "default",
  label,
}: PushAndRotateButtonViewProps) {
  const t = useTranslations("apps.overview.ciSetup");
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  return (
    <>
      <Button
        size={size}
        variant={variant}
        onClick={() => setConfirmOpen(true)}
        disabled={pushing}
        className="gap-1.5"
      >
        {pushing ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <UploadCloudIcon className="size-3.5" />
        )}
        {label ?? t("pushRotate")}
      </Button>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("rotateTitle")}
        description={t("rotateDescription")}
        confirmLabel={t("pushRotate")}
        onConfirm={onPushAndRotate}
      />
    </>
  );
}

function ValidateCiSecretsAction({ validating, results, onValidate }: CiSetupData["validate"]) {
  const t = useTranslations("apps.overview.ciSetup");
  const format = useFormatter();
  return (
    <div className="flex flex-col gap-3 rounded-md border border-dashed p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">{t("validateTitle")}</p>
          <p className="text-muted-foreground text-xs">{t("validateDescription")}</p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={onValidate}
          disabled={validating}
          className="gap-1.5"
        >
          {validating ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <ShieldCheckIcon className="size-3.5" />
          )}
          {t("validate")}
        </Button>
      </div>
      {results && results.results.length > 0 ? (
        <ul className="divide-border divide-y rounded-md border text-xs">
          {results.results.map((r) => (
            <li
              key={r.secretName}
              className="flex items-center justify-between gap-2 px-2.5 py-1.5"
            >
              <span className="font-mono">{r.secretName}</span>
              <span className="inline-flex items-center gap-1.5">
                {r.isSet && r.isCurrent ? (
                  <span className="text-success-fg inline-flex items-center gap-1">
                    <CheckIcon className="size-3" /> {t("current")}
                  </span>
                ) : r.isSet ? (
                  <span className="text-warning-fg inline-flex items-center gap-1">
                    <CheckIcon className="size-3" /> {t("stale")}
                  </span>
                ) : (
                  <span className="text-danger-fg inline-flex items-center gap-1">
                    <XIcon className="size-3" /> {t("notSet")}
                  </span>
                )}
                {r.updatedAt ? (
                  <span className="text-muted-foreground">
                    {Number.isFinite(new Date(r.updatedAt).getTime())
                      ? format.dateTime(new Date(r.updatedAt), {
                          year: "numeric",
                          month: "short",
                          day: "numeric",
                        })
                      : t("unknownDate")}
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function PushAndRotateAction(props: ReturnType<typeof usePushAndRotate>) {
  const t = useTranslations("apps.overview.ciSetup");
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">{t("pushRotate")}</p>
        <p className="text-muted-foreground text-xs">{t("pushRotateDescription")}</p>
      </div>
      <PushAndRotateButtonView {...props} />
    </div>
  );
}

function SecretRowItem({
  row,
  isLast,
  loading,
}: {
  row: SecretRow;
  isLast: boolean;
  loading: boolean;
}) {
  const t = useTranslations("apps.overview.ciSetup");
  return (
    <div
      className={
        "flex flex-wrap items-center gap-3 px-4 py-3 sm:flex-nowrap" + (isLast ? "" : " border-b")
      }
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="text-muted-foreground shrink-0 cursor-help">
            <KeyIcon className="size-3.5" />
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">
          <span>{row.hint}</span>
          <br />
          <span className="text-background/70">{t("secretNameHint")}</span>
        </TooltipContent>
      </Tooltip>

      <code className="text-foreground shrink-0 font-mono text-xs">{row.name}</code>

      <span className="text-muted-foreground mx-1 hidden text-xs sm:inline">=</span>

      <SecretValue row={row} loading={loading} />
    </div>
  );
}

function SecretValue({ row, loading }: { row: SecretRow; loading: boolean }) {
  const t = useTranslations("apps.overview.ciSetup");
  const [copied, setCopied] = React.useState(false);

  if (row.value === null) {
    return (
      <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
        <span className="text-muted-foreground truncate font-mono text-xs tracking-widest">
          ••••••••••••
        </span>
        {row.externalHref && (
          <Button size="sm" variant="ghost" asChild className="shrink-0 gap-1.5">
            <Link href={row.externalHref}>
              <ExternalLinkIcon className="size-3.5" />
              {row.externalLabel ?? t("open")}
            </Link>
          </Button>
        )}
      </div>
    );
  }

  if (loading) {
    return <Skeleton className="h-5 flex-1" />;
  }

  const hasValue = row.value.length > 0;

  async function copy() {
    if (!hasValue) return;
    try {
      await navigator.clipboard.writeText(row.value!);
      setCopied(true);
      toast.success(t("valueCopied", { name: row.name }));
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(t("copyValueFailed"));
    }
  }

  return (
    <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
      {hasValue ? (
        <code className="text-foreground truncate font-mono text-xs" title={row.value}>
          {row.value}
        </code>
      ) : (
        <span className="text-muted-foreground text-xs italic">{t("pending")}</span>
      )}
      <Button
        size="sm"
        variant="ghost"
        onClick={copy}
        disabled={!hasValue}
        className="shrink-0 gap-1.5"
      >
        {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
        {copied ? t("copied") : t("copy")}
      </Button>
    </div>
  );
}

function SyncWorkflowFileAction({ workflowPath, syncing, onSync }: CiSetupData["syncFile"]) {
  const t = useTranslations("apps.overview.ciSetup");
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">{t("syncTitle")}</p>
        <p className="text-muted-foreground text-xs">
          {t.rich("syncDescription", {
            path: () => <span className="font-mono">{workflowPath}</span>,
          })}
        </p>
      </div>
      <Button size="sm" variant="outline" onClick={onSync} disabled={syncing} className="gap-1.5">
        {syncing ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <FileCheck2Icon className="size-3.5" />
        )}
        {t("syncTitle")}
      </Button>
    </div>
  );
}

/**
 * Source-host push webhook (#385). Chip in the header surfaces the
 * current state: green "installed · 5m ago" when the app row carries an
 * `installedAt`, amber "not installed" otherwise.
 */
function InstallSourceWebhookAction({
  sourceWebhookInstalledAt,
  installing,
  onInstall,
}: { sourceWebhookInstalledAt: string | null } & CiSetupData["webhook"]) {
  const t = useTranslations("apps.overview.ciSetup");
  const relativeText = useTranslations("apps.overview.ciWorkflow.relative");
  const installed = Boolean(sourceWebhookInstalledAt);
  const relative = sourceWebhookInstalledAt
    ? formatRelativeWebhookInstall(sourceWebhookInstalledAt, (key, values) =>
        relativeText(key, values)
      )
    : null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium">{t("webhookTitle")}</p>
          {installed ? (
            <span className="bg-success/10 text-2xs text-success-fg inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium">
              <CheckIcon className="size-3" />
              {t("installed", { relative: relative ?? "" })}
            </span>
          ) : (
            <span className="bg-warning/10 text-2xs text-warning-fg inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium">
              {t("notInstalled")}
            </span>
          )}
        </div>
        <p className="text-muted-foreground mt-0.5 text-xs">{t("webhookDescription")}</p>
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={onInstall}
        disabled={installing}
        className="gap-1.5"
      >
        {installing ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <WebhookIcon className="size-3.5" />
        )}
        {installed ? t("refreshWebhook") : t("installWebhook")}
      </Button>
    </div>
  );
}
