"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import type { AstroliftCiWorkflowSyncStatus } from "@/graphql/__generated__/schema";
import {
  INSTALL_SOURCE_WEBHOOK,
  PUSH_CI_SECRETS_TO_REPO,
  PUSH_CI_WORKFLOW_TO_REPO,
  VALIDATE_CI_SECRETS,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { GET_PLATFORM_API_URL } from "@/graphql/registry/registry.queries";
import {
  OPEN_CI_WORKFLOW_RECONCILE_PR,
  PULL_CI_WORKFLOW_FROM_REPO,
  REFRESH_CI_WORKFLOW_SYNC_STATUS,
  RESYNC_CI_WORKFLOW,
} from "@/graphql/scm/scm.mutations";

import { DRIFT_BADGE } from "./ci-setup-meta";

interface PlatformUrlResp {
  astroliftPlatformApiUrl: string;
}

type MutationErrors = Array<{ code: string; message: string; field?: string | null }>;

// ---------------------------------------------------------------------
// Managed CI-workflow status + actions (#1210)
//
// Two directions, named after what they do, plus a re-check:
//
//   * Check      — read the repo and reclassify.
//   * Pull       — bring the repo's file INTO the platform and baseline
//                  on it. Stores its text, so the comparison below has
//                  something to show. Pushes nothing.
//   * Push       — send the platform's current template to the repo. A
//                  hand-edited file goes through a reviewable PR rather
//                  than being overwritten in place.
//
// "Resync" used to be the only outbound action and it direct-wrote onto
// an unprotected deploy branch, so pressing it on an edited file lost
// the edit. "Adopt repo copy" was the only inbound one and it kept no
// copy, so a drift badge pointed at a file nobody could read. Both are
// still reachable through the deprecated mutations for API clients.
//
// Each mutation returns the refreshed status; we refetch `GetApp` so
// the badge (sourced from the app query) converges after the action.
// ---------------------------------------------------------------------

interface CiSyncMutationResult {
  ok: boolean;
  errors: MutationErrors;
  data: AstroliftCiWorkflowSyncStatus | null;
}
interface PushResp {
  resyncAstroliftCiWorkflow: CiSyncMutationResult;
}
interface PullResp {
  pullCiWorkflowFromRepo: CiSyncMutationResult;
}
interface RefreshResp {
  refreshCiWorkflowSyncStatus: CiSyncMutationResult;
}
interface ReconcileResp {
  openCiWorkflowReconcilePr: CiSyncMutationResult;
}

/** A toast body with a trailing "View PR" link (a .ts file, so no JSX). */
function prToast(text: string, prUrl: string, label: string) {
  return React.createElement(
    "span",
    null,
    text,
    " ",
    React.createElement(
      Link,
      { href: prUrl, target: "_blank", rel: "noreferrer", className: "underline" },
      label
    )
  );
}

function useWorkflowSync(appId: string) {
  const t = useTranslations("apps.overview.ciWorkflow");
  const [push, { loading: pushing }] = useMutation<PushResp>(RESYNC_CI_WORKFLOW, {
    refetchQueries: ["GetApp"],
    awaitRefetchQueries: true,
  });
  const [pull, { loading: pulling }] = useMutation<PullResp>(PULL_CI_WORKFLOW_FROM_REPO, {
    refetchQueries: ["GetApp"],
    awaitRefetchQueries: true,
  });
  const [refresh, { loading: refreshing }] = useMutation<RefreshResp>(
    REFRESH_CI_WORKFLOW_SYNC_STATUS,
    { refetchQueries: ["GetApp"], awaitRefetchQueries: true }
  );
  const [openReconcile, { loading: reconciling }] = useMutation<ReconcileResp>(
    OPEN_CI_WORKFLOW_RECONCILE_PR,
    { refetchQueries: ["GetApp"], awaitRefetchQueries: true }
  );

  async function onRefresh() {
    try {
      const { data } = await refresh({ variables: { input: { appId } } });
      const payload = data?.refreshCiWorkflowSyncStatus;
      if (!payload?.ok || !payload.data) {
        toast.error(payload?.errors?.[0]?.message ?? t("feedback.refreshFailed"));
        return;
      }
      const label = Object.hasOwn(DRIFT_BADGE, payload.data.state)
        ? t(`states.${payload.data.state}.label`)
        : payload.data.state;
      toast.success(t("feedback.refreshed", { state: label }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("feedback.refreshFailed"));
    }
  }

  async function onPush() {
    try {
      const { data } = await push({ variables: { input: { appId } } });
      const payload = data?.resyncAstroliftCiWorkflow;
      if (!payload?.ok || !payload.data) {
        toast.error(payload?.errors?.[0]?.message ?? t("feedback.pushFailed"));
        return;
      }
      if (payload.data.prUrl) {
        toast.success(prToast(t("feedback.pushPr"), payload.data.prUrl, t("viewPr")));
        return;
      }
      toast.success(t("feedback.pushed"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("feedback.pushFailed"));
    }
  }

  async function onReconcile() {
    try {
      const { data } = await openReconcile({ variables: { input: { appId } } });
      const payload = data?.openCiWorkflowReconcilePr;
      if (!payload?.ok || !payload.data) {
        toast.error(payload?.errors?.[0]?.message ?? t("feedback.reconcileFailed"));
        return;
      }
      if (payload.data.prUrl) {
        toast.success(prToast(t("feedback.reconciled"), payload.data.prUrl, t("viewPr")));
        return;
      }
      toast.success(t("feedback.reconciled"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("feedback.reconcileFailed"));
    }
  }

  /** Throws on failure so the ConfirmDialog stays open and shows the error. */
  async function onPull(): Promise<void> {
    const { data } = await pull({ variables: { input: { appId } } });
    const payload = data?.pullCiWorkflowFromRepo;
    if (!payload?.ok || !payload.data) {
      throw new Error(payload?.errors?.[0]?.message ?? t("feedback.pullFailed"));
    }
    toast.success(t("feedback.pulled"));
  }

  return {
    pushing,
    pulling,
    refreshing,
    reconciling,
    onRefresh,
    onPush,
    onReconcile,
    onPull,
  };
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
    errors: MutationErrors;
    data: {
      secretNames: string[];
      rotatedTokenLast4: string;
      repo: string;
    } | null;
  };
}

/** The "Push & rotate" mutation. Shared with the Secrets tab (#681). */
export function usePushAndRotate(appSlug: string) {
  const t = useTranslations("apps.overview.ciSetup.feedback");
  const [push, { loading }] = useMutation<PushCiSecretsResp>(PUSH_CI_SECRETS_TO_REPO);

  /** Throws on failure so the ConfirmDialog stays open and shows the error. */
  async function onPushAndRotate(): Promise<void> {
    const { data } = await push({ variables: { input: { appSlug } } });
    const payload = data?.pushAstroliftCiSecretsToRepo;
    if (!payload?.ok || !payload.data) {
      throw new Error(payload?.errors?.[0]?.message ?? t("pushFailed"));
    }
    const { secretNames, rotatedTokenLast4, repo } = payload.data;
    toast.success(t("pushed", { count: secretNames.length, repo, last4: rotatedTokenLast4 }));
  }

  return { pushing: loading, onPushAndRotate };
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
    errors: MutationErrors;
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

export type CiSecretsValidation = ValidateCiSecretsResp["validateAstroliftCiSecrets"]["data"];

function useValidateCiSecrets(appSlug: string) {
  const t = useTranslations("apps.overview.ciSetup.feedback");
  const [validate, { loading }] = useMutation<ValidateCiSecretsResp>(VALIDATE_CI_SECRETS);
  const [results, setResults] = React.useState<CiSecretsValidation | null>(null);

  async function onValidate() {
    try {
      const { data } = await validate({ variables: { input: { appSlug } } });
      const payload = data?.validateAstroliftCiSecrets;
      if (!payload?.ok || !payload.data) {
        const err = payload?.errors?.[0];
        // PRECONDITION bucket means an upstream wiring problem rather
        // than a per-secret state — surface the message + clear any
        // stale results from a prior good run.
        toast.error(err?.message ?? t("validateFailed"));
        setResults(null);
        return;
      }
      setResults(payload.data);
      const okCount = payload.data.results.filter((r) => r.isSet && r.isCurrent).length;
      const total = payload.data.results.length;
      if (okCount === total) {
        toast.success(t("validated", { count: total, repo: payload.data.repo }));
      } else {
        toast.warning(
          t("partiallyValidated", { healthy: okCount, total, repo: payload.data.repo })
        );
      }
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  return { validating: loading, results, onValidate };
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
    errors: MutationErrors;
    data: {
      status: string;
      commitSha: string | null;
      prUrl: string | null;
    } | null;
  };
}

function useSyncWorkflowFile(appSlug: string, workflowPath: string) {
  const t = useTranslations("apps.overview.ciSetup.feedback");
  const [sync, { loading }] = useMutation<PushCiWorkflowResp>(PUSH_CI_WORKFLOW_TO_REPO);

  async function onSync() {
    try {
      const { data } = await sync({ variables: { input: { appSlug } } });
      const payload = data?.pushAstroliftCiWorkflowToRepo;
      if (!payload?.ok || !payload.data) {
        toast.error(payload?.errors?.[0]?.message ?? t("syncFailed"));
        return;
      }
      const { status, commitSha, prUrl } = payload.data;
      if (status === "in_sync") {
        toast.success(t("inSync"));
        return;
      }
      if (status === "pr_opened") {
        if (prUrl) {
          toast.success(prToast(t("protectedPr"), prUrl, t("viewPr")));
        } else {
          toast.success(t("protectedPr"));
        }
        return;
      }
      const verb =
        status === "created" ? t("created") : status === "updated" ? t("updated") : status;
      const shortSha = (commitSha ?? "").slice(0, 7);
      const fileName = workflowPath.split("/").at(-1) ?? workflowPath;
      toast.success(
        shortSha
          ? t("syncedCommit", { verb, filename: fileName, commit: shortSha })
          : t("synced", { verb, filename: fileName })
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("syncFailed"));
    }
  }

  return { workflowPath, syncing: loading, onSync };
}

// ---------------------------------------------------------------------
// Install source-host push webhook (#385)
//
// Registers (or refreshes) the source-host push-event webhook on the
// app's repo so the platform receives `push` and `pull_request`
// deliveries. The mutation rotates a shared HMAC secret on every
// click — a re-click on an already-installed hook returns
// ``status=refreshed`` and is otherwise a no-op on the host side
// (GitHub keeps the URL unique).
// ---------------------------------------------------------------------

interface InstallSourceWebhookResp {
  installAstroliftSourceWebhook: {
    ok: boolean;
    errors: MutationErrors;
    data: {
      status: string;
      hookId: string;
      receiverUrl: string;
    } | null;
  };
}

function useInstallSourceWebhook(appSlug: string) {
  const t = useTranslations("apps.overview.ciSetup.feedback");
  const [install, { loading }] = useMutation<InstallSourceWebhookResp>(INSTALL_SOURCE_WEBHOOK, {
    // Re-read the app so the chip + timestamp converge after install.
    refetchQueries: ["GetApp"],
    awaitRefetchQueries: true,
  });

  async function onInstall() {
    try {
      const { data } = await install({ variables: { input: { appSlug } } });
      const payload = data?.installAstroliftSourceWebhook;
      if (!payload?.ok || !payload.data) {
        toast.error(payload?.errors?.[0]?.message ?? t("webhookFailed"));
        return;
      }
      const { status, receiverUrl } = payload.data;
      const verb =
        status === "created" ? t("installed") : status === "refreshed" ? t("refreshed") : status;
      toast.success(
        receiverUrl ? t("webhookUrl", { verb, url: receiverUrl }) : t("webhook", { verb })
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("webhookFailed"));
    }
  }

  return { installing: loading, onInstall };
}

/**
 * Everything the CI setup section talks to the server about: the platform
 * API URL, managed-workflow drift actions, Push & rotate, secret
 * validation, workflow-file sync and the source webhook. The data half of
 * CiSetupSectionView.
 */
export function useCiSetup({
  appId,
  appSlug,
  agentMode = false,
}: {
  appId: string;
  appSlug: string;
  agentMode?: boolean;
}) {
  const { data, loading } = useQuery<PlatformUrlResp>(GET_PLATFORM_API_URL, {
    fetchPolicy: "cache-first",
  });
  const workflowPath = agentMode
    ? `.github/workflows/astrolift-agent-${appSlug}.yml`
    : ".github/workflows/astrolift-ci.yml";

  return {
    apiUrl: data?.astroliftPlatformApiUrl ?? "",
    apiUrlLoading: loading,
    workflowSync: useWorkflowSync(appId),
    pushAndRotate: usePushAndRotate(appSlug),
    validate: useValidateCiSecrets(appSlug),
    syncFile: useSyncWorkflowFile(appSlug, workflowPath),
    webhook: useInstallSourceWebhook(appSlug),
  };
}

export type CiSetupData = ReturnType<typeof useCiSetup>;
