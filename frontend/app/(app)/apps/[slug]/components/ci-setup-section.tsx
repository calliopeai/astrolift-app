"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  INSTALL_SOURCE_WEBHOOK,
  PUSH_CI_SECRETS_TO_REPO,
  PUSH_CI_WORKFLOW_TO_REPO,
  VALIDATE_CI_SECRETS,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { GET_PLATFORM_API_URL } from "@/graphql/registry/registry.queries";

/**
 * CI setup section on the consolidated Settings page (#382).
 *
 * Lists the five GitHub Actions secrets an operator needs to wire CI
 * to Astrolift, with copy-buttons on the live values, and a paste-ready
 * reference workflow YAML keyed off those exact secret names.
 *
 * The deploy-token value itself never appears here — that's a one-shot
 * reveal handled by ``DeployTokenControl`` on the tokens page. We render
 * the row as masked dots with a link out to the existing reveal flow so
 * there's exactly one place token plaintext can be observed.
 */

interface Props {
  appSlug: string;
  ecrRepoUri: string;
  ecrPushRoleArn: string;
  /** ISO timestamp of the last successful webhook install / refresh
   *  (#385). `null` until the operator clicks "Install webhook" for
   *  the first time. */
  sourceWebhookInstalledAt: string | null;
}

interface PlatformUrlResp {
  astroliftPlatformApiUrl: string;
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

export function CiSetupSection({
  appSlug,
  ecrRepoUri,
  ecrPushRoleArn,
  sourceWebhookInstalledAt,
}: Props) {
  const { data, loading } = useQuery<PlatformUrlResp>(GET_PLATFORM_API_URL, {
    fetchPolicy: "cache-first",
  });
  const apiUrl = data?.astroliftPlatformApiUrl ?? "";

  const rows: SecretRow[] = [
    {
      name: "ASTROLIFT_PUSH_ROLE_ARN",
      value: ecrPushRoleArn,
      hint:
        "IAM role the GitHub Actions runner assumes via OIDC to push images. Provisioned by Astrolift on cluster bootstrap.",
    },
    {
      name: "ASTROLIFT_ECR_URI",
      value: ecrRepoUri,
      hint: "Container registry URI this app pushes to. Tag with the commit SHA per build.",
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
      externalHref: `/apps/${appSlug}/tokens`,
      externalLabel: "Manage deploy tokens",
    },
  ];

  const workflowYaml = renderWorkflowYaml();

  async function copyYaml() {
    try {
      await navigator.clipboard.writeText(workflowYaml);
      toast.success("Reference workflow YAML copied.");
    } catch {
      toast.error("Copy failed — select the text and copy manually.");
    }
  }

  return (
    <section className="rounded-lg border p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
          <TerminalIcon className="size-5" />
        </div>
        <div>
          <h2 className="text-base font-semibold">CI setup</h2>
          <p className="text-muted-foreground mt-0.5 text-xs">
            Paste these values into your GitHub repo&rsquo;s Actions secrets, then drop in the
            reference workflow below. The workflow keys off these exact names — rename one and the
            run breaks.
          </p>
        </div>
      </div>

      <TooltipProvider>
        <div className="bg-card overflow-hidden rounded-md border">
          {rows.map((row, idx) => (
            <SecretRowItem
              key={row.name}
              row={row}
              isLast={idx === rows.length - 1}
              loading={loading && row.name === "ASTROLIFT_API_URL"}
            />
          ))}
        </div>
      </TooltipProvider>

      <details className="group bg-muted/30 mt-4 rounded-md border">
        <summary className="hover:bg-muted/50 flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium">
          <span className="flex items-center gap-2">
            <ChevronDownIcon className="size-4 transition-transform group-open:rotate-180" />
            Reference GitHub Actions workflow
          </span>
          <span className="text-muted-foreground text-[11px]">
            paste into <span className="font-mono">.github/workflows/astrolift-ci.yml</span>
          </span>
        </summary>
        <div className="border-t">
          <pre className="bg-background overflow-x-auto p-4 font-mono text-[11px] leading-relaxed">
            {workflowYaml}
          </pre>
          <div className="flex justify-end border-t p-3">
            <Button size="sm" variant="outline" onClick={copyYaml} className="gap-1.5">
              <CopyIcon className="size-3.5" />
              Copy workflow YAML
            </Button>
          </div>
        </div>
      </details>

      <Can permission="app.update">
        <ValidateCiSecretsAction appSlug={appSlug} />
        <PushAndRotateAction appSlug={appSlug} />
        <SyncWorkflowFileAction appSlug={appSlug} />
        <InstallSourceWebhookAction
          appSlug={appSlug}
          sourceWebhookInstalledAt={sourceWebhookInstalledAt}
        />
      </Can>
    </section>
  );
}

// ---------------------------------------------------------------------
// Push & rotate (#383)
//
// One-click round-trip that seals each of the five `ASTROLIFT_*` values
// with the repo's libsodium public key and PUTs them as GitHub Actions
// secrets via the viewer's personal GitHub OAuth connection. The deploy
// token is rotated as part of the call — there's no other way to land a
// sealable plaintext for the `ASTROLIFT_DEPLOY_TOKEN` slot. The rotate
// is *immediate* (no grace window) so the dialog copy reflects reality:
// the old token stops working the moment GitHub accepts the new sealed
// value. Operators clicking this affordance have been warned in the
// confirm dialog; in-flight CI runs holding the previous token will
// have to be re-kicked once the new secret lands.
// ---------------------------------------------------------------------

interface PushCiSecretsResp {
  pushAstroliftCiSecretsToRepo: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field?: string | null }>;
    data: {
      secretNames: string[];
      rotatedTokenLast4: string;
      repo: string;
    } | null;
  };
}

/**
 * "Push & rotate" affordance — the button + ConfirmDialog round-trip.
 * Exported (#681) so the Secrets tab can mirror the action without
 * duplicating the mutation wiring. The Settings page wraps this in a
 * card-style block; the Secrets page uses just the button in its
 * action toolbar.
 */
export function PushAndRotateButton({
  appSlug,
  size = "sm",
  variant = "default",
  label = "Push & rotate",
}: {
  appSlug: string;
  size?: "sm" | "default";
  variant?: "default" | "outline";
  label?: string;
}) {
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [push, { loading }] = useMutation<PushCiSecretsResp>(PUSH_CI_SECRETS_TO_REPO);

  async function handleConfirm() {
    const { data } = await push({ variables: { input: { appSlug } } });
    const payload = data?.pushAstroliftCiSecretsToRepo;
    if (!payload?.ok || !payload.data) {
      throw new Error(payload?.errors?.[0]?.message ?? "Couldn't push CI secrets.");
    }
    const { secretNames, rotatedTokenLast4, repo } = payload.data;
    toast.success(
      `Pushed ${secretNames.length} secrets to ${repo} — new token's last 4: ${rotatedTokenLast4}`,
    );
  }

  return (
    <>
      <Button
        size={size}
        variant={variant}
        onClick={() => setConfirmOpen(true)}
        disabled={loading}
        className="gap-1.5"
      >
        {loading ? (
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
        onConfirm={handleConfirm}
      />
    </>
  );
}

/**
 * #693 — Read-only probe of the app's GitHub repo Actions secrets,
 * surfacing per-secret 'isSet / isCurrent' status. No-op on Push;
 * just verifies what's there. Saves operators a deploy-and-find-out
 * cycle when wondering 'did I really push those secrets?'.
 */
interface ValidateCiSecretsResp {
  validateAstroliftCiSecrets: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field?: string | null }>;
    data: {
      repo: string;
      results: Array<{
        secretName: string;
        isSet: boolean;
        isCurrent: boolean;
        updatedAt: string | null;
      }>;
    } | null;
  };
}

function ValidateCiSecretsAction({ appSlug }: { appSlug: string }) {
  const [validate, { loading }] = useMutation<ValidateCiSecretsResp>(VALIDATE_CI_SECRETS);
  const [results, setResults] = React.useState<
    ValidateCiSecretsResp["validateAstroliftCiSecrets"]["data"] | null
  >(null);

  async function handleValidate() {
    try {
      const { data } = await validate({ variables: { input: { appSlug } } });
      const payload = data?.validateAstroliftCiSecrets;
      if (!payload?.ok || !payload.data) {
        const err = payload?.errors?.[0];
        // PRECONDITION bucket means an upstream wiring problem rather
        // than a per-secret state — surface the message + clear any
        // stale results from a prior good run.
        toast.error(err?.message ?? "Couldn't validate CI secrets.");
        setResults(null);
        return;
      }
      setResults(payload.data);
      const okCount = payload.data.results.filter((r) => r.isSet && r.isCurrent).length;
      const total = payload.data.results.length;
      if (okCount === total) {
        toast.success(`All ${total} secrets are set and current on ${payload.data.repo}.`);
      } else {
        toast.warning(`${okCount}/${total} secrets healthy on ${payload.data.repo}.`);
      }
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  return (
    <div className="mt-4 flex flex-col gap-3 rounded-md border border-dashed p-3">
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
          onClick={handleValidate}
          disabled={loading}
          className="gap-1.5"
        >
          {loading ? (
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
            <li key={r.secretName} className="flex items-center justify-between gap-2 px-2.5 py-1.5">
              <span className="font-mono">{r.secretName}</span>
              <span className="inline-flex items-center gap-1.5">
                {r.isSet && r.isCurrent ? (
                  <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300">
                    <CheckIcon className="size-3" /> current
                  </span>
                ) : r.isSet ? (
                  <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-300">
                    <CheckIcon className="size-3" /> set, stale
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-rose-700 dark:text-rose-300">
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

function PushAndRotateAction({ appSlug }: { appSlug: string }) {
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">Push & rotate</p>
        <p className="text-muted-foreground text-xs">
          Seal and upload all five values to GitHub Actions secrets in one shot. Rotates the
          deploy token as part of the round-trip.
        </p>
      </div>
      <PushAndRotateButton appSlug={appSlug} />
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
        "flex flex-wrap items-center gap-3 px-4 py-3 sm:flex-nowrap" +
        (isLast ? "" : " border-b")
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
        <code
          className="text-foreground truncate font-mono text-xs"
          title={row.value}
        >
          {row.value}
        </code>
      ) : (
        <span className="text-muted-foreground text-xs italic">
          pending provisioning…
        </span>
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

/**
 * Reference GitHub Actions workflow.
 *
 * Keyed off the five ``ASTROLIFT_*`` secret names rendered above so the
 * operator can paste this verbatim — no per-app substitutions. The
 * commit SHA is the image tag; the deploy webhook POST tells the
 * platform which image to roll out. Matches the contract documented
 * in spec 09 §6.
 */
function renderWorkflowYaml(): string {
  return `name: astrolift deploy

on:
  push:
    branches: [main]
  workflow_dispatch: {}

permissions:
  id-token: write
  contents: read

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: \${{ secrets.ASTROLIFT_PUSH_ROLE_ARN }}
          aws-region: us-west-2
          role-session-name: astrolift-\${{ github.run_id }}

      - name: Login to Amazon ECR
        id: ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push image
        env:
          IMAGE: \${{ secrets.ASTROLIFT_ECR_URI }}:\${{ github.sha }}
        run: |
          docker build -t "$IMAGE" .
          docker push "$IMAGE"

      - name: Notify Astrolift
        env:
          API_URL: \${{ secrets.ASTROLIFT_API_URL }}
          APP_SLUG: \${{ secrets.ASTROLIFT_APP_SLUG }}
          TOKEN: \${{ secrets.ASTROLIFT_DEPLOY_TOKEN }}
          IMAGE: \${{ secrets.ASTROLIFT_ECR_URI }}:\${{ github.sha }}
        run: |
          curl --fail-with-body -sS -X POST "$API_URL/api/v1/deploys" \\
            -H "Authorization: Bearer $TOKEN" \\
            -H "Content-Type: application/json" \\
            -d "{\\"appSlug\\":\\"$APP_SLUG\\",\\"image\\":\\"$IMAGE\\",\\"commitSha\\":\\"\${{ github.sha }}\\"}"
`;
}

// ---------------------------------------------------------------------
// Sync workflow file (#384)
//
// Renders the canonical ``astrolift-ci.yml`` for the app and commits it
// into ``.github/workflows/`` on the deploy branch. Idempotent: a
// re-click on an in-sync repo returns ``in_sync`` and is a no-op. When
// the deploy branch is protected, the platform lands the file on a side
// branch and opens a PR; the toast then links to that PR rather than to
// a commit. Pairs with "Push & rotate" above so an operator can stand
// up a fresh repo end-to-end without leaving this section.
// ---------------------------------------------------------------------

interface PushCiWorkflowResp {
  pushAstroliftCiWorkflowToRepo: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field?: string | null }>;
    data: {
      status: string;
      commitSha: string | null;
      prUrl: string | null;
    } | null;
  };
}

function SyncWorkflowFileAction({ appSlug }: { appSlug: string }) {
  const [sync, { loading }] = useMutation<PushCiWorkflowResp>(PUSH_CI_WORKFLOW_TO_REPO);

  async function handleClick() {
    try {
      const { data } = await sync({ variables: { input: { appSlug } } });
      const payload = data?.pushAstroliftCiWorkflowToRepo;
      if (!payload?.ok || !payload.data) {
        toast.error(payload?.errors?.[0]?.message ?? "Couldn't sync the workflow file.");
        return;
      }
      const { status, commitSha, prUrl } = payload.data;
      if (status === "in_sync") {
        toast.success("Already up to date.");
        return;
      }
      if (status === "pr_opened") {
        if (prUrl) {
          toast.success(
            <span>
              Branch is protected — opened a PR.{" "}
              <Link href={prUrl} target="_blank" rel="noreferrer" className="underline">
                View PR
              </Link>
            </span>,
          );
        } else {
          toast.success("Branch is protected — opened a PR.");
        }
        return;
      }
      const verb = status === "created" ? "Created" : "Updated";
      const shortSha = (commitSha ?? "").slice(0, 7);
      toast.success(
        shortSha
          ? `${verb} astrolift-ci.yml (commit ${shortSha}).`
          : `${verb} astrolift-ci.yml.`,
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't sync the workflow file.");
    }
  }

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">Sync workflow file</p>
        <p className="text-muted-foreground text-xs">
          Commits the rendered <span className="font-mono">.github/workflows/astrolift-ci.yml</span>{" "}
          to the deploy branch. Idempotent — re-clicks on an in-sync repo are a no-op.
        </p>
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={handleClick}
        disabled={loading}
        className="gap-1.5"
      >
        {loading ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <FileCheck2Icon className="size-3.5" />
        )}
        Sync workflow file
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------
// Install source-host push webhook (#385)
//
// Registers (or refreshes) the source-host push-event webhook on the
// app's repo so the platform receives `push` and `pull_request`
// deliveries. The mutation rotates a shared HMAC secret on every
// click — a re-click on an already-installed hook returns
// ``status=refreshed`` and is otherwise a no-op on the host side
// (GitHub keeps the URL unique). Chip in the header surfaces the
// current state: green "installed · 5m ago" when the app row carries
// an `installedAt`, amber "not installed" otherwise.
// ---------------------------------------------------------------------

interface InstallSourceWebhookResp {
  installAstroliftSourceWebhook: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field?: string | null }>;
    data: {
      status: string;
      hookId: string;
      receiverUrl: string;
    } | null;
  };
}

function InstallSourceWebhookAction({
  appSlug,
  sourceWebhookInstalledAt,
}: {
  appSlug: string;
  sourceWebhookInstalledAt: string | null;
}) {
  const [install, { loading }] = useMutation<InstallSourceWebhookResp>(INSTALL_SOURCE_WEBHOOK, {
    // Re-read the app so the chip + timestamp converge after install.
    refetchQueries: ["GetApp"],
    awaitRefetchQueries: true,
  });

  async function handleClick() {
    try {
      const { data } = await install({ variables: { input: { appSlug } } });
      const payload = data?.installAstroliftSourceWebhook;
      if (!payload?.ok || !payload.data) {
        toast.error(payload?.errors?.[0]?.message ?? "Couldn't install the source webhook.");
        return;
      }
      const { status, receiverUrl } = payload.data;
      const verb = status === "created" ? "Installed" : "Refreshed";
      toast.success(
        receiverUrl
          ? `${verb} push webhook → ${receiverUrl}`
          : `${verb} push webhook.`,
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't install the source webhook.");
    }
  }

  const installed = Boolean(sourceWebhookInstalledAt);
  const relative = sourceWebhookInstalledAt
    ? formatRelativeWebhookInstall(sourceWebhookInstalledAt)
    : null;

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium">Source webhook</p>
          {installed ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
              <CheckIcon className="size-3" />
              installed · {relative}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
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
        onClick={handleClick}
        disabled={loading}
        className="gap-1.5"
      >
        {loading ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <WebhookIcon className="size-3.5" />
        )}
        {installed ? "Refresh webhook" : "Install webhook"}
      </Button>
    </div>
  );
}

/**
 * Compact relative-time formatter for the webhook-install chip.
 *
 * The Settings page already uses ``useFormatters`` for resync state,
 * but threading that hook through every action component crosses the
 * "props in, JSX out" boundary the section component aims for. A
 * local helper keeps this fragment self-contained.
 */
function formatRelativeWebhookInstall(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return "just now";
  const seconds = Math.max(0, Math.floor(diff / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}
