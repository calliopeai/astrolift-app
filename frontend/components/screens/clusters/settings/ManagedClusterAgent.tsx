"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import { Loader2Icon, RocketIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import {
  GET_CLUSTER_AGENT_INSTALL,
  INSTALL_CLUSTER_AGENT,
  REVIEW_CLUSTER_AGENT_INSTALL,
} from "@/graphql/clusters/clusters.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";

interface Installation {
  id: string;
  clusterId: string;
  requestId: string;
  status: "QUEUED" | "INSTALLING" | "AWAITING_HEARTBEAT" | "SUCCEEDED" | "REFUSED" | "UNCERTAIN";
  secretConfirmed: boolean;
  deploymentConfirmed: boolean;
  heartbeatConfirmed: boolean;
  retryable: boolean;
  errorCode: string;
  errorMessage: string;
}
interface InstallInput {
  clusterId: string;
  expectedVersion: number;
  expectedSource: string;
  requestId: string;
}

export function ManagedClusterAgent({ clusterId }: { clusterId: string }) {
  const t = useTranslations("clusterSettings.agent");
  const client = useApolloClient();
  const currentCluster = React.useRef(clusterId);
  React.useEffect(() => {
    currentCluster.current = clusterId;
    return () => {
      currentCluster.current = "";
    };
  }, [clusterId]);
  const attempt = React.useRef<InstallInput | null>(null);
  const submitting = React.useRef(false);
  const [submitted, setSubmitted] = React.useState<Installation | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [install] = useMutation<{ installClusterAgent: MutationResult<Installation> }>(
    INSTALL_CLUSTER_AGENT
  );
  const active = submitted?.clusterId === clusterId ? submitted : null;
  const observed = useQuery<{ astroliftClusterAgentInstall: Installation }>(
    GET_CLUSTER_AGENT_INSTALL,
    {
      variables: { installId: active?.id ?? "" },
      skip: !active,
      fetchPolicy: "network-only",
      pollInterval: 2000,
    }
  );
  const row =
    observed.data?.astroliftClusterAgentInstall?.clusterId === clusterId
      ? observed.data.astroliftClusterAgentInstall
      : active;
  const pending = !!row && ["QUEUED", "INSTALLING", "AWAITING_HEARTBEAT"].includes(row.status);
  const { stopPolling } = observed;
  React.useEffect(() => {
    if (row && !pending) stopPolling();
  }, [row, pending, stopPolling]);

  async function onInstall() {
    if (submitting.current) return;
    submitting.current = true;
    const requestedCluster = clusterId;
    setBusy(true);
    setError(null);
    try {
      let input = attempt.current;
      if (
        !input ||
        input.clusterId !== requestedCluster ||
        row?.status === "SUCCEEDED" ||
        row?.status === "REFUSED"
      ) {
        const review = await client.query<{
          astroliftClusterAgentInstallReview: {
            clusterId: string;
            version: number;
            source: string;
          };
        }>({
          query: REVIEW_CLUSTER_AGENT_INSTALL,
          variables: { clusterId: requestedCluster },
          fetchPolicy: "network-only",
        });
        const value = review.data?.astroliftClusterAgentInstallReview;
        if (!value || value.clusterId !== requestedCluster)
          throw new Error(t("feedbackDeployFailed"));
        input = {
          clusterId: requestedCluster,
          expectedVersion: value.version,
          expectedSource: value.source,
          requestId: crypto.randomUUID(),
        };
        attempt.current = input;
      }
      if (currentCluster.current !== requestedCluster) return;
      const { data } = await install({ variables: { input } });
      if (currentCluster.current !== requestedCluster) return;
      const result = data?.installClusterAgent;
      if (!result?.ok || !result.data || result.data.clusterId !== requestedCluster) {
        throw new Error(result?.errors?.[0]?.message ?? t("feedbackDeployFailed"));
      }
      setSubmitted(result.data);
      observed.startPolling(2000);
    } catch (cause) {
      if (currentCluster.current === requestedCluster)
        setError(cause instanceof Error ? cause.message : t("feedbackDeployFailed"));
    } finally {
      submitting.current = false;
      if (currentCluster.current === requestedCluster) setBusy(false);
    }
  }
  const confirmed =
    row?.status === "SUCCEEDED" &&
    row.secretConfirmed &&
    row.deploymentConfirmed &&
    row.heartbeatConfirmed;
  const failure = error ?? observed.error?.message ?? row?.errorMessage;
  return (
    <Section title={t("title")} description={t("managedDescription")} divided>
      <div className="space-y-3">
        <Button
          size="sm"
          onClick={() => void onInstall()}
          disabled={busy || pending}
          className="gap-1.5"
        >
          {busy || pending ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <RocketIcon className="size-3.5" />
          )}
          {t("deploy")}
        </Button>
        {pending && (
          <p role="status" className="text-muted-foreground text-xs">
            {t("managedPending")}
          </p>
        )}
        {confirmed && (
          <p role="status" className="text-xs">
            {t("managedConnected")}
          </p>
        )}
        {failure && (
          <p role="alert" className="text-danger-fg text-xs">
            {failure}
          </p>
        )}
      </div>
    </Section>
  );
}
