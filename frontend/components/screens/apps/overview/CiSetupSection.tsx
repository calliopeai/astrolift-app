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
  if (agentMode) {
    const automatic = Boolean(sourceWebhookInstalledAt);
    return (
      <Section
        title={
          <span className="flex items-center gap-2">
            <WebhookIcon className="text-primary size-4" />
            Agent source delivery
          </span>
        }
        description={
          <>
            Agent pushes freeze an immutable package snapshot; they do not deploy a standing app or
            start a run. The next dispatch uses the latest successful snapshot and the image named
            by that package.
          </>
        }
      >
        <div className="bg-card space-y-3 rounded-md border p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">Push → package sync</p>
              <p className="text-muted-foreground text-xs">
                Signed source webhook · deploy branch only · exact manifest or opted-in federation
                bundle.
              </p>
            </div>
            <span
              className={
                automatic
                  ? "bg-success/10 text-success-fg rounded-full px-2 py-1 text-xs font-medium"
                  : "bg-warning/10 text-warning-fg rounded-full px-2 py-1 text-xs font-medium"
              }
            >
              {automatic ? "Automatic on push" : "Webhook not installed"}
            </span>
          </div>
          <div className="grid gap-2 text-xs md:grid-cols-3">
            <div className="bg-muted/40 rounded p-2">
              <p className="font-medium">Config and scripts</p>
              <p className="text-muted-foreground">
                Frozen from the selected chrooted source slice.
              </p>
            </div>
            <div className="bg-muted/40 rounded p-2">
              <p className="font-medium">Container image</p>
              <p className="text-muted-foreground">
                Published separately by the repo&rsquo;s image CI.
              </p>
            </div>
            <div className="bg-muted/40 rounded p-2">
              <p className="font-medium">Runs</p>
              <p className="text-muted-foreground">Never auto-started by a source push.</p>
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

  const rows: SecretRow[] = [
    {
      name: meta.pushCredentialEnv,
      value: pushCredentialRef,
      hint: meta.pushCredentialHint,
    },
    {
      name: meta.registryEnv,
      value: registryUri,
      hint: meta.registryHint,
    },
    {
      name: "ASTROLIFT_APP_SLUG",
      value: appSlug,
      hint: "Stable app identifier — embed in the deploy webhook payload so the platform routes correctly.",
    },
    {
      name: "ASTROLIFT_API_URL",
      value: apiUrl,
      hint: "Public base URL of the Astrolift platform API. The deploy webhook is POSTed here.",
    },
    {
      name: "ASTROLIFT_DEPLOY_TOKEN",
      value: null,
      hint: "App-scoped bearer token. Shown once at mint time on the deploy-tokens page.",
      externalHref: tokensHref,
      externalLabel: "Manage deploy tokens",
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
      toast.success("Reference workflow YAML copied.");
    } catch {
      toast.error("Copy failed — select the text and copy manually.");
    }
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <TerminalIcon className="text-primary size-4" />
          CI setup
        </span>
      }
      description={
        <>
          Review the app&rsquo;s CI secrets and workflow before syncing it to the source repository.
        </>
      }
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
            {managedWorkflow ? "Managed CI workflow" : "Reference GitHub Actions workflow"}
          </span>
          <span className="text-muted-foreground text-2xs">
            {managedWorkflow ? (
              ciWorkflowSyncStatus?.path || "Review before syncing"
            ) : (
              <>
                paste into <span className="font-mono">.github/workflows/astrolift-ci.yml</span>
              </>
            )}
          </span>
        </summary>
        <div className="border-t">
          <pre className="bg-background text-2xs overflow-x-auto p-4 font-mono leading-relaxed">
            {workflowYaml ??
              "Workflow unavailable. Check the app's CI configuration and refresh its current workflow preview."}
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
              Copy workflow YAML
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
  label = "Push & rotate",
}: PushAndRotateButtonViewProps) {
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
        {label}
      </Button>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Push CI secrets to the repo?"
        description="This rotates the deploy token. The old token will stop working immediately."
        confirmLabel="Push & rotate"
        onConfirm={onPushAndRotate}
      />
    </>
  );
}

function ValidateCiSecretsAction({ validating, results, onValidate }: CiSetupData["validate"]) {
  return (
    <div className="flex flex-col gap-3 rounded-md border border-dashed p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">Validate CI secrets</p>
          <p className="text-muted-foreground text-xs">
            Check what GitHub Actions actually sees today — no changes, just a read.
          </p>
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
          Validate
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
                    <CheckIcon className="size-3" /> current
                  </span>
                ) : r.isSet ? (
                  <span className="text-warning-fg inline-flex items-center gap-1">
                    <CheckIcon className="size-3" /> set, stale
                  </span>
                ) : (
                  <span className="text-danger-fg inline-flex items-center gap-1">
                    <XIcon className="size-3" /> not set
                  </span>
                )}
                {r.updatedAt ? (
                  <span className="text-muted-foreground">
                    {new Date(r.updatedAt).toLocaleDateString()}
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
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">Push & rotate</p>
        <p className="text-muted-foreground text-xs">
          Seal and upload all five values to GitHub Actions secrets in one shot. Rotates the deploy
          token as part of the round-trip.
        </p>
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
          <span className="text-background/70">GitHub Actions secret name</span>
        </TooltipContent>
      </Tooltip>

      <code className="text-foreground shrink-0 font-mono text-xs">{row.name}</code>

      <span className="text-muted-foreground mx-1 hidden text-xs sm:inline">=</span>

      <SecretValue row={row} loading={loading} />
    </div>
  );
}

function SecretValue({ row, loading }: { row: SecretRow; loading: boolean }) {
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
              {row.externalLabel ?? "Open"}
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
      toast.success(`${row.name} copied.`);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Copy failed — select the value and copy manually.");
    }
  }

  return (
    <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
      {hasValue ? (
        <code className="text-foreground truncate font-mono text-xs" title={row.value}>
          {row.value}
        </code>
      ) : (
        <span className="text-muted-foreground text-xs italic">pending provisioning…</span>
      )}
      <Button
        size="sm"
        variant="ghost"
        onClick={copy}
        disabled={!hasValue}
        className="shrink-0 gap-1.5"
      >
        {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

function SyncWorkflowFileAction({ workflowPath, syncing, onSync }: CiSetupData["syncFile"]) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">Sync workflow file</p>
        <p className="text-muted-foreground text-xs">
          Commits the rendered <span className="font-mono">{workflowPath}</span> to the deploy
          branch. Idempotent — re-clicks on an in-sync repo are a no-op.
        </p>
      </div>
      <Button size="sm" variant="outline" onClick={onSync} disabled={syncing} className="gap-1.5">
        {syncing ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <FileCheck2Icon className="size-3.5" />
        )}
        Sync workflow file
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
  const installed = Boolean(sourceWebhookInstalledAt);
  const relative = sourceWebhookInstalledAt
    ? formatRelativeWebhookInstall(sourceWebhookInstalledAt)
    : null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium">Source webhook</p>
          {installed ? (
            <span className="bg-success/10 text-2xs text-success-fg inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium">
              <CheckIcon className="size-3" />
              installed · {relative}
            </span>
          ) : (
            <span className="bg-warning/10 text-2xs text-warning-fg inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium">
              not installed
            </span>
          )}
        </div>
        <p className="text-muted-foreground mt-0.5 text-xs">
          Registers the push-event webhook on the source repo pointing at the platform&rsquo;s
          receiver URL. Re-click to rotate the HMAC secret without spawning a duplicate hook.
        </p>
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
        {installed ? "Refresh webhook" : "Install webhook"}
      </Button>
    </div>
  );
}
