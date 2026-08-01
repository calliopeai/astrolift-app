"use client";

import { useQuery } from "@apollo/client/react";
import { useSearchParams } from "next/navigation";
import * as React from "react";

import { LIST_APP_PODS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";

interface PodsResp {
  astroliftAppPods: AstroliftAppPod[];
}

// Same defaults the observability tab uses — keep them in sync so an operator
// switching tabs gets identical pod-freshness behaviour.
const POD_POLL_MS = 5000;

const KNOWN_SIDECARS = new Set([
  "istio-proxy",
  "envoy",
  "linkerd-proxy",
  "datadog-agent",
  "otel-collector",
  "otc-container",
  "newrelic-infrastructure",
  "fluent-bit",
  "fluentd",
  "filebeat",
  "vault-agent",
  "vault-agent-init",
]);

/**
 * The app container an operator almost certainly meant. Prefer the container
 * named after the workload, then the first non-sidecar; a mesh proxy is never
 * what someone opening a shell or tailing logs is after.
 */
function pickDefaultContainer(
  containers: string[],
  workload: string | null | undefined
): string | null {
  if (containers.length === 0) return null;
  if (workload) {
    const match = containers.find((c) => c === workload);
    if (match) return match;
  }
  const nonSidecar = containers.find((c) => !KNOWN_SIDECARS.has(c));
  return nonSidecar ?? containers[0];
}

export interface PodTarget {
  podRows: AstroliftAppPod[];
  selectedPod: string | null;
  setPickedPod: (pod: string) => void;
  podContainers: string[];
  selectedContainer: string | null;
  /** Nullable: LogViewer's container picker offers an "all containers" entry. */
  setPickedContainer: (container: string | null) => void;
  /** First load only — a background poll refresh is not "loading". */
  podsLoading: boolean;
  noPods: boolean;
}

/**
 * Pod + container selection for an app's pod-scoped surfaces (Observe › Logs
 * and Control › Shell). Both pick one pod and one of its containers from the
 * same polled list; splitting the console (#1247) would otherwise have
 * duplicated the selection rules — including the sidecar-skipping default,
 * which is easy to get subtly wrong twice.
 *
 * Selection is derived, not stored: an explicit pick wins while it still
 * exists in the current pod list, then `?pod=` / `?container=` from the URL,
 * and otherwise the default. That way a pod being replaced under the operator
 * resolves to a live target instead of pinning a name that no longer exists —
 * which matters most for the URL case, since a pod name is inherently
 * short-lived and a deep link outlives the pod it names (#1250).
 */
export function usePodTarget(slug: string): PodTarget {
  // PodExpander deep-links a specific pod ("open shell" / "open logs" on an
  // expanded pod row). Honouring it is the whole point of the link: without
  // it the operator picks the misbehaving replica, clicks through, and lands
  // on whichever pod sorts first — invisibly correct on a single-replica app,
  // and quietly the wrong target on anything scaled out.
  const searchParams = useSearchParams();
  const urlPod = searchParams?.get("pod") || null;
  const urlContainer = searchParams?.get("container") || null;

  const pods = useQuery<PodsResp>(LIST_APP_PODS, {
    variables: { appSlug: slug },
    pollInterval: POD_POLL_MS,
    fetchPolicy: "cache-and-network",
  });

  const podRows: AstroliftAppPod[] = React.useMemo(
    () => pods.data?.astroliftAppPods ?? [],
    [pods.data]
  );

  const [pickedPod, setPickedPod] = React.useState<string | null>(null);
  const selectedPod: string | null = React.useMemo(() => {
    // The URL is a *default*, not a pin: once the operator touches the
    // picker their choice wins, and the link's pod is dropped as soon as it
    // stops existing rather than leaving the surface stuck on a dead name.
    const desired = pickedPod ?? urlPod;
    if (desired && podRows.some((p) => p.name === desired)) return desired;
    const running = podRows.find((p) => p.status === "Running");
    return running?.name ?? podRows[0]?.name ?? null;
  }, [pickedPod, urlPod, podRows]);

  const podContainers: string[] = React.useMemo(() => {
    const pod = podRows.find((p) => p.name === selectedPod);
    return pod?.containerStatuses.map((c) => c.name) ?? [];
  }, [podRows, selectedPod]);

  const selectedPodWorkload = React.useMemo(
    () => podRows.find((p) => p.name === selectedPod)?.workload ?? null,
    [podRows, selectedPod]
  );

  const [pickedContainer, setPickedContainer] = React.useState<string | null>(null);
  const selectedContainer: string | null = React.useMemo(() => {
    const desired = pickedContainer ?? urlContainer;
    if (desired && podContainers.includes(desired)) return desired;
    return pickDefaultContainer(podContainers, selectedPodWorkload);
  }, [pickedContainer, urlContainer, podContainers, selectedPodWorkload]);

  const podsLoading = pods.loading && podRows.length === 0;

  return {
    podRows,
    selectedPod,
    setPickedPod,
    podContainers,
    selectedContainer,
    setPickedContainer,
    podsLoading,
    noPods: !podsLoading && podRows.length === 0,
  };
}
