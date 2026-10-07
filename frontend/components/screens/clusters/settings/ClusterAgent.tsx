"use client";

import { CheckCircleIcon, CopyIcon, KeyRoundIcon, Loader2Icon, RocketIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import * as React from "react";
import { useTranslations } from "next-intl";

import type { useClusterAgent } from "./use-cluster-agent";

export type ClusterAgentViewProps = ReturnType<typeof useClusterAgent> & {
  /**
   * Why the control plane may not deploy the agent here: its ClusterRole is
   * beyond the minimal RBAC contract (calliope-installer#447). Issuing a key
   * for an agent the cluster's owner installs stays available.
   */
  deployWithheldReason?: string | null;
};

/**
 * Cluster keep-alive agent card (#808). Issues (or rotates) the scoped
 * agent key the in-cluster keep-alive agent signs its heartbeat with. The
 * raw key is shown EXACTLY ONCE — the operator copies it into the agent's
 * Secret, then it's unrecoverable (only its hash persists). Also shows the
 * install snippet templated with this cluster's heartbeat URL.
 */
export function ClusterAgentView({
  clusterId,
  provisioned,
  heartbeatIntervalSeconds,
  issued: receivedIssued,
  issuing,
  deploying,
  onIssue,
  onDeploy,
  onDismissIssued,
  deployWithheldReason,
}: ClusterAgentViewProps) {
  const t = useTranslations("clusterSettings.agent");
  const issued = receivedIssued?.clusterId === clusterId ? receivedIssued : null;
  const [copyState, setCopyState] = React.useState({
    source: issued,
    key: false,
    snippet: false,
    error: false,
  });
  if (copyState.source !== issued) {
    setCopyState({ source: issued, key: false, snippet: false, error: false });
  }
  async function copy(text: string, kind: "key" | "snippet") {
    try {
      await navigator.clipboard.writeText(text);
      setCopyState((previous) =>
        previous.source === issued ? { ...previous, [kind]: true, error: false } : previous
      );
      setTimeout(() => {
        setCopyState((previous) =>
          previous.source === issued ? { ...previous, [kind]: false } : previous
        );
      }, 2000);
    } catch {
      setCopyState((previous) =>
        previous.source === issued ? { ...previous, [kind]: false, error: true } : previous
      );
    }
  }
  // Install snippet: a kubectl one-liner that creates the agent's Secret
  // from the issued key + heartbeat URL. The agent Deployment reads both
  // from this Secret. Path-only URL in local dev (no APP_BASE_URL) — the
  // operator templates the host in then.
  const heartbeatUrl = issued?.heartbeatUrl ?? `/api/clusters/v1/${clusterId}/heartbeat/`;
  const snippet = issued
    ? [
        "kubectl create secret generic astrolift-agent \\",
        "  --namespace astrolift-system \\",
        `  --from-literal=heartbeat_url='${heartbeatUrl}' \\`,
        `  --from-literal=agent_key='${issued.agentKey}'`,
      ].join("\n")
    : null;

  return (
    <Section
      title={t("title")}
      description={t.rich("description", {
        seconds: heartbeatIntervalSeconds,
        namespace: (chunks) => <code className="font-mono text-xs">{chunks}</code>,
        interval: (chunks) => <span className="font-mono">{chunks}</span>,
      })}
      action={
        provisioned && (
          <Badge variant="outline" className="shrink-0 gap-1">
            <CheckCircleIcon className="size-3" />
            {t("keyIssued")}
          </Badge>
        )
      }
      divided
    >
      <div className="flex min-w-0 flex-col gap-3">
        {deployWithheldReason && (provisioned || issued) && (
          <p id="agent-deploy-withheld" className="text-warning-fg text-xs">
            {deployWithheldReason}
          </p>
        )}
        {issued ? (
          <>
            <div className="border-warning-border bg-warning/10 rounded-md border p-3">
              <p className="text-warning-fg text-xs font-medium">{t("copyNow")}</p>
              <div className="mt-2 flex min-w-0 items-center gap-2">
                <code className="bg-background/60 min-w-0 flex-1 truncate rounded px-2 py-1 font-mono text-xs">
                  {issued.agentKey}
                </code>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void copy(issued.agentKey, "key")}
                  className="gap-1.5"
                >
                  <CopyIcon className="size-3.5" />
                  {copyState.source === issued && copyState.key ? t("copied") : t("copy")}
                </Button>
              </div>
            </div>
            {snippet && (
              <div className="min-w-0 space-y-1.5">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium">{t("installSecret")}</p>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void copy(snippet, "snippet")}
                    className="h-7 gap-1.5"
                  >
                    <CopyIcon className="size-3" />
                    {copyState.source === issued && copyState.snippet ? t("copied") : t("copy")}
                  </Button>
                </div>
                <pre className="bg-muted/40 overflow-x-auto rounded-md border p-3 font-mono text-xs whitespace-pre">
                  {snippet}
                </pre>
                <p className="text-muted-foreground text-xs">{t("installHelp")}</p>
              </div>
            )}
            {copyState.source === issued && copyState.error && (
              <p role="alert" className="text-danger-fg text-xs">
                {t("copyFailed")}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                onClick={onDeploy}
                disabled={deploying || !!deployWithheldReason}
                aria-describedby={deployWithheldReason ? "agent-deploy-withheld" : undefined}
                className="gap-1.5"
              >
                {deploying ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RocketIcon className="size-3.5" />
                )}
                {t("deploy")}
              </Button>
              <Button size="sm" variant="ghost" onClick={onDismissIssued}>
                {t("done")}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">
              {t.rich("deployHelp", {
                secret: (chunks) => <code className="font-mono text-xs">{chunks}</code>,
              })}
            </p>
          </>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              {provisioned && (
                <Button
                  size="sm"
                  onClick={onDeploy}
                  disabled={deploying || !!deployWithheldReason}
                  aria-describedby={deployWithheldReason ? "agent-deploy-withheld" : undefined}
                  className="gap-1.5"
                >
                  {deploying ? (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  ) : (
                    <RocketIcon className="size-3.5" />
                  )}
                  {t("deploy")}
                </Button>
              )}
              <Button
                size="sm"
                variant={provisioned ? "outline" : "default"}
                onClick={onIssue}
                disabled={issuing}
                className="gap-1.5"
              >
                {issuing ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <KeyRoundIcon className="size-3.5" />
                )}
                {provisioned ? t("rotate") : t("issue")}
              </Button>
            </div>
            {provisioned && (
              <p className="text-muted-foreground text-xs">
                {t.rich("rotationHelp", {
                  secret: (chunks) => <code className="font-mono text-xs">{chunks}</code>,
                })}
              </p>
            )}
          </div>
        )}
      </div>
    </Section>
  );
}
