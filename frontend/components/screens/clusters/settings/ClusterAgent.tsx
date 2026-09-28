"use client";

import { CheckCircleIcon, CopyIcon, KeyRoundIcon, Loader2Icon, RocketIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";

import type { useClusterAgent } from "./use-cluster-agent";

export type ClusterAgentViewProps = ReturnType<typeof useClusterAgent>;

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
  issued,
  issuing,
  deploying,
  onIssue,
  onDeploy,
  onDismissIssued,
}: ClusterAgentViewProps) {
  const [keyCopied, copyKey] = useCopyToClipboard();
  const [snippetCopied, copySnippet] = useCopyToClipboard();

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
      title="Keep-alive agent"
      description={
        <>
          A lightweight agent in the cluster&apos;s{" "}
          <code className="font-mono text-xs">astrolift-system</code> namespace POSTs a signed
          heartbeat so the platform can show live pod, node, and resource state, and flag the
          cluster offline when it stops. Pulses every{" "}
          <span className="font-mono">{heartbeatIntervalSeconds}s</span>.
        </>
      }
      action={
        provisioned && (
          <Badge variant="outline" className="shrink-0 gap-1">
            <CheckCircleIcon className="size-3" />
            Key issued
          </Badge>
        )
      }
      divided
    >
      <div className="flex min-w-0 flex-col gap-3">
        {issued ? (
          <>
            <div className="border-warning-border bg-warning/10 rounded-md border p-3">
              <p className="text-warning-fg text-xs font-medium">
                Copy this key now — it won&apos;t be shown again.
              </p>
              <div className="mt-2 flex min-w-0 items-center gap-2">
                <code className="bg-background/60 min-w-0 flex-1 truncate rounded px-2 py-1 font-mono text-xs">
                  {issued.agentKey}
                </code>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => copyKey(issued.agentKey)}
                  className="gap-1.5"
                >
                  <CopyIcon className="size-3.5" />
                  {keyCopied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
            {snippet && (
              <div className="min-w-0 space-y-1.5">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium">Install the agent secret</p>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => copySnippet(snippet)}
                    className="h-7 gap-1.5"
                  >
                    <CopyIcon className="size-3" />
                    {snippetCopied ? "Copied" : "Copy"}
                  </Button>
                </div>
                <pre className="bg-muted/40 overflow-x-auto rounded-md border p-3 font-mono text-xs whitespace-pre">
                  {snippet}
                </pre>
                <p className="text-muted-foreground text-xs">
                  Then deploy the agent (it reads the key + URL from this Secret). The cluster
                  appears as Connected within a couple of heartbeat intervals.
                </p>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={onDeploy} disabled={deploying} className="gap-1.5">
                {deploying ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RocketIcon className="size-3.5" />
                )}
                Deploy agent to cluster
              </Button>
              <Button size="sm" variant="ghost" onClick={onDismissIssued}>
                Done
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">
              Applies the agent Deployment — make sure you&apos;ve created the{" "}
              <code className="font-mono text-xs">astrolift-agent</code> Secret first using the
              snippet above.
            </p>
          </>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              {provisioned && (
                <Button size="sm" onClick={onDeploy} disabled={deploying} className="gap-1.5">
                  {deploying ? (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  ) : (
                    <RocketIcon className="size-3.5" />
                  )}
                  Deploy agent to cluster
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
                {provisioned ? "Rotate agent key" : "Issue agent key"}
              </Button>
            </div>
            {provisioned && (
              <p className="text-muted-foreground text-xs">
                Deploying applies the agent Deployment — it reads the key + URL from the{" "}
                <code className="font-mono text-xs">astrolift-agent</code> Secret you created when
                the key was issued. Rotating the key invalidates the old one, so the running agent
                will fail its heartbeat until you redeploy with the new key.
              </p>
            )}
          </div>
        )}
      </div>
    </Section>
  );
}
