/** The current exec transport ends with its WebSocket. It never resumes stdin. */
export interface ReviewedExecTarget {
  workloadId: string;
  workloadVersion: number;
  appId: string;
  appVersion: number;
  environmentId: string;
  environmentName: string;
  environmentVersion: number;
  clusterId: string;
  clusterVersion: number;
  namespace: string;
  podName: string;
  podUid: string;
  container: string;
  podBinding: "PREFLIGHT_ONLY";
  resumable: false;
}

export function isReviewedExecTarget(value: unknown): value is ReviewedExecTarget {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  const strings = [
    "workloadId",
    "appId",
    "environmentId",
    "environmentName",
    "clusterId",
    "namespace",
    "podName",
    "podUid",
    "container",
  ];
  const versions = ["workloadVersion", "appVersion", "environmentVersion", "clusterVersion"];
  return (
    strings.every((field) => typeof row[field] === "string" && row[field] !== "") &&
    versions.every(
      (field) =>
        typeof row[field] === "number" &&
        Number.isInteger(row[field]) &&
        (row[field] as number) >= 0
    ) &&
    row.podBinding === "PREFLIGHT_ONLY" &&
    row.resumable === false
  );
}

export function admitsReviewedSession(expected: ReviewedExecTarget, observed: unknown): boolean {
  if (!isReviewedExecTarget(observed)) return false;
  return Object.keys(expected).every(
    (key) =>
      key === "__typename" ||
      expected[key as keyof ReviewedExecTarget] === observed[key as keyof ReviewedExecTarget]
  );
}
