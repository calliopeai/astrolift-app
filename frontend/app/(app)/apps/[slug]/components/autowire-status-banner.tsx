"use client";

/**
 * AutowireStatusBanner (#1108) — surfaces whether a git push will actually
 * auto-deploy this app.
 *
 * Registration completes the autowire (CI workflow → source webhook → deploy
 * secret) when an org source connection exists. This banner renders the two
 * states that need operator attention:
 *
 *   - not connected → a "connect for auto-deploy" callout (the app is
 *     registered but nothing wires it to the repo yet);
 *   - connected but a step is `missing` / `error` / `phantom` → an "autowire
 *     incomplete" banner listing the failing steps + a "Retry autowire" button
 *     that re-runs the chain and repairs a phantom webhook.
 *
 * Fully-wired apps (and non-git sources) render nothing.
 */

import { useMutation } from "@apollo/client/react";
import { AlertTriangleIcon, Loader2Icon, PlugZapIcon, RefreshCwIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { RETRY_ASTROLIFT_AUTOWIRE } from "@/graphql/lifecycle/lifecycle.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftAppAutowireStatus } from "@/graphql/registry/registry.types";

interface RetryResp {
  retryAstroliftAutowire: MutationResult<{
    connected: boolean;
    allOk: boolean;
    ciWorkflow: string;
    webhook: string;
    secrets: string;
    detail: string;
  }>;
}

interface Props {
  appSlug: string;
  sourceKind: string;
  sourceRepo: string;
  autowire?: AstroliftAppAutowireStatus | null;
}

const STEP_LABELS: { key: "ciWorkflow" | "webhook" | "secrets"; label: string }[] = [
  { key: "ciWorkflow", label: "CI workflow" },
  { key: "webhook", label: "Source webhook" },
  { key: "secrets", label: "Deploy secret" },
];

// Per-status copy. `phantom` is the webhook-only "marked installed but nothing
// delivers" state the issue reports.
const STATUS_COPY: Record<string, string> = {
  ok: "wired",
  missing: "not set up",
  error: "failed",
  phantom: "not delivering",
};

export function AutowireStatusBanner({ appSlug, sourceKind, sourceRepo, autowire }: Props) {
  const [retry, { loading }] = useMutation<RetryResp>(RETRY_ASTROLIFT_AUTOWIRE, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug, includeDrift: true } }],
    awaitRefetchQueries: true,
  });

  async function handleRetry() {
    try {
      const { data } = await retry({ variables: { input: { appSlug } } });
      const payload = data?.retryAstroliftAutowire;
      if (!payload?.ok) {
        toast.error(payload?.errors?.[0]?.message ?? "Retry failed");
        return;
      }
      if (payload.data?.allOk) {
        toast.success("Auto-deploy wired up");
      } else if (!payload.data?.connected) {
        toast.message("Connect a GitHub App or org connection to wire auto-deploy");
      } else {
        toast.warning(payload.data?.detail || "Some autowire steps still need attention");
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Retry failed");
    }
  }

  // Autowire only applies to git-sourced apps with a repo.
  if (!autowire) return null;
  if (sourceKind !== "github" && sourceKind !== "gitlab") return null;
  if (!sourceRepo) return null;

  // Not connected: the "connect for auto-deploy" callout.
  if (!autowire.connected) {
    return (
      <section className="border-warning-border bg-warning/5 flex flex-wrap items-start gap-3 rounded-lg border p-4">
        <PlugZapIcon className="text-warning-fg mt-0.5 size-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Connect for auto-deploy</p>
          <p className="text-muted-foreground mt-1 max-w-2xl text-xs">
            This app is registered but not wired to its repo. Connect the GitHub App (or an org
            GitHub connection) so Astrolift can push the CI workflow, install the webhook, and
            deploy on every push.
          </p>
        </div>
        <a
          href="/settings/source-providers"
          className="bg-foreground text-background hover:bg-foreground/90 shrink-0 rounded-md px-3 py-1.5 text-xs font-medium"
        >
          Connect
        </a>
      </section>
    );
  }

  const failing = STEP_LABELS.map((s) => ({ ...s, status: autowire[s.key] })).filter(
    (s) => s.status !== "ok"
  );

  // Connected + fully wired: nothing to show.
  if (failing.length === 0) return null;

  // Connected but incomplete: the repair banner.
  return (
    <section
      className="border-warning-border bg-warning/10 rounded-md border p-4"
      aria-live="polite"
    >
      <div className="flex items-start gap-3">
        <AlertTriangleIcon aria-hidden className="text-warning-fg size-5 shrink-0" />
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="text-warning-fg text-sm font-semibold">Autowire incomplete</p>
            <p className="text-warning-fg text-xs leading-snug">
              A git push won&apos;t auto-deploy until every step is wired. Retry to complete the
              setup.
            </p>
          </div>
          <ul className="text-warning-fg list-disc space-y-0.5 pl-5 text-xs">
            {failing.map((s) => (
              <li key={s.key}>
                {s.label}: {STATUS_COPY[s.status] ?? s.status}
              </li>
            ))}
          </ul>
          {autowire.detail ? (
            <p className="text-muted-foreground text-2xs font-mono break-all">{autowire.detail}</p>
          ) : null}
        </div>
        <Button
          type="button"
          size="sm"
          onClick={handleRetry}
          disabled={loading}
          className="shrink-0 gap-1"
        >
          {loading ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <RefreshCwIcon className="size-4" />
          )}
          Retry autowire
        </Button>
      </div>
    </section>
  );
}
