"use client";

/**
 * AutowireStatusBannerView (#1108): surfaces whether a git push will actually
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

import { AlertTriangleIcon, Loader2Icon, PlugZapIcon, RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AstroliftAppAutowireStatus } from "@/graphql/registry/registry.types";

import { Notice } from "./OverviewNotices";
import type { useAutowireRetry } from "./use-autowire-retry";

export type AutowireStatusBannerViewProps = ReturnType<typeof useAutowireRetry> & {
  sourceKind: string;
  sourceRepo: string;
  autowire?: AstroliftAppAutowireStatus | null;
};

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

export function AutowireStatusBannerView({
  sourceKind,
  sourceRepo,
  autowire,
  retrying,
  onRetry,
}: AutowireStatusBannerViewProps) {
  // Autowire only applies to git-sourced apps with a repo.
  if (!autowire) return null;
  if (sourceKind !== "github" && sourceKind !== "gitlab") return null;
  if (!sourceRepo) return null;

  // Not connected: the "connect for auto-deploy" callout.
  if (!autowire.connected) {
    return (
      <Notice
        tone="warning"
        icon={PlugZapIcon}
        title="Connect for auto-deploy"
        description="This app is registered but not wired to its repo. Connect the GitHub App (or an org GitHub connection) so Astrolift can push the CI workflow, install the webhook, and deploy on every push."
        actions={
          <Button asChild size="sm" variant="outline">
            <a href="/settings/source-providers">Connect</a>
          </Button>
        }
      />
    );
  }

  const failing = STEP_LABELS.map((s) => ({ ...s, status: autowire[s.key] })).filter(
    (s) => s.status !== "ok"
  );

  // Connected + fully wired: nothing to show.
  if (failing.length === 0) return null;

  // Connected but incomplete: the repair notice.
  return (
    <Notice
      tone="warning"
      icon={AlertTriangleIcon}
      title="Autowire incomplete"
      description="A git push won't auto-deploy until every step is wired. Retry to complete the setup."
      actions={
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onRetry}
          disabled={retrying}
          className="gap-1"
        >
          {retrying ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <RefreshCwIcon className="size-4" />
          )}
          Retry autowire
        </Button>
      }
    >
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {failing.map((s) => (
          <li key={s.key}>
            {s.label}: <span className="font-mono">{STATUS_COPY[s.status] ?? s.status}</span>
          </li>
        ))}
      </ul>
      {autowire.detail ? (
        <p className="text-muted-foreground text-2xs mt-1 font-mono [overflow-wrap:anywhere]">
          {autowire.detail}
        </p>
      ) : null}
    </Notice>
  );
}
