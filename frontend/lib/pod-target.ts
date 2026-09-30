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
export function pickDefaultContainer(
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

/** Resolve a live pod, falling back after a pod named in an old link is replaced. */
export function selectPodName(
  pods: ReadonlyArray<{ name: string; status: string }>,
  desired: string | null
): string | null {
  if (desired && pods.some((pod) => pod.name === desired)) return desired;
  return pods.find((pod) => pod.status === "Running")?.name ?? pods[0]?.name ?? null;
}

export function selectContainerName(
  containers: string[],
  workload: string | null | undefined,
  desired: string | null
): string | null {
  if (desired && containers.includes(desired)) return desired;
  return pickDefaultContainer(containers, workload);
}
