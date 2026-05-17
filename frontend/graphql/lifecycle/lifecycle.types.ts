/**
 * Lifecycle types — facade over the codegen output.
 *
 * Entity types come from `__generated__/schema.ts` so they stay in
 * lockstep with the backend. The narrow string-union types
 * (DeploymentStatus, TriggerKind, PreviewStatus) live here because
 * they're frontend-side switch-exhaustiveness aids — the schema
 * carries them as plain `String!` fields. We override those fields
 * on the generated entity with the narrow unions so consumer code
 * (e.g. STATUS_DOT[d.status]) typechecks against valid values.
 *
 * When the backend grows a new status value, regen the schema and
 * add it to the union here. Codegen will catch a mismatch the next
 * time consumers actually use the field.
 */

import type {
  AstroliftAppCertificate as GeneratedAppCertificate,
  AstroliftAppDnsRecord as GeneratedAppDnsRecord,
  AstroliftAppEnvironment as GeneratedAppEnvironment,
  AstroliftAppHealthSummary as GeneratedAppHealthSummary,
  AstroliftAppIdentityBinding as GeneratedAppIdentityBinding,
  AstroliftAppLogLine as GeneratedAppLogLine,
  AstroliftAppPod as GeneratedAppPod,
  AstroliftCommandRun as GeneratedCommandRun,
  AstroliftContainerStatus as GeneratedContainerStatus,
  AstroliftDeployment as GeneratedDeployment,
  AstroliftDeploymentLogEntry as GeneratedDeploymentLogEntry,
  AstroliftDeploymentMetrics as GeneratedDeploymentMetrics,
  AstroliftPreviewEnvironment as GeneratedPreviewEnvironment,
  AstroliftScheduledJobRun as GeneratedScheduledJobRun,
} from "@/graphql/__generated__/schema";

export type AstroliftGuid = string;

export type DeploymentStatus =
  | "pending_approval"
  | "pending"
  | "deploying"
  | "running"
  | "redeploying"
  | "failed"
  | "superseded"
  | "rolled_back";

export type TriggerKind = "push" | "manual" | "ci" | "scheduled" | "rollback" | "promotion";

export type PreviewStatus = "building" | "running" | "failed" | "torn_down";

export type AstroliftAppEnvironment = GeneratedAppEnvironment;

// #419 — approval decision-context fields are added to the backend
// `AstroliftDeployment` type in the same commit; this facade pre-
// declares them so the FE typechecks before `make codegen` has been
// run. Once codegen regenerates `GeneratedDeployment` they collapse
// into a no-op merge.
export interface DeploymentApprovalContextFields {
  commitMessage: string;
  commitAuthor: string;
  repoUrl: string;
  abortedReason: string;
  triggeredByUserId: string | null;
  triggeredByMe: boolean;
}

export type AstroliftDeployment = Omit<GeneratedDeployment, "status" | "triggerKind"> &
  DeploymentApprovalContextFields & {
    status: DeploymentStatus;
    triggerKind: TriggerKind;
  };

export interface AstroliftDeploymentApprovalHistoryEntry {
  id: string;
  action: string;
  decision: string;
  actorKind: string;
  actorId: string;
  actorDisplay: string;
  occurredAt: string;
  reason: string;
}

export type AstroliftDeploymentLogEntry = GeneratedDeploymentLogEntry;

export type AstroliftPreviewEnvironment = Omit<GeneratedPreviewEnvironment, "status"> & {
  status: PreviewStatus;
};

export type AstroliftDeploymentMetrics = GeneratedDeploymentMetrics;

export type AstroliftAppHealthSummary = Omit<
  GeneratedAppHealthSummary,
  "latestDeploymentStatus"
> & {
  latestDeploymentStatus: DeploymentStatus | null;
};

export type ScheduledJobRunStatus = "running" | "succeeded" | "failed" | "superseded";

export type AstroliftScheduledJobRun = Omit<GeneratedScheduledJobRun, "status"> & {
  status: ScheduledJobRunStatus;
};

export type AstroliftCommandRun = GeneratedCommandRun;

// Observability — pod + log surface. The backend's status string is
// a rolled-up surface label so it can contain k8s container-waiting
// reasons (CrashLoopBackOff, ImagePullBackOff, …) in addition to
// the raw pod phase. The narrow PodSurfaceStatus union catches the
// common phases for switch-exhaustive UI styling; anything else
// falls through to the generic "unknown" badge.
export type PodSurfaceStatus =
  | "Running"
  | "Pending"
  | "Succeeded"
  | "Failed"
  | "Unknown"
  | "CrashLoopBackOff"
  | "ImagePullBackOff"
  | "ErrImagePull"
  | "CreateContainerConfigError"
  | "InvalidImageName"
  | "CreateContainerError";

export type LogStreamKind = "stdout" | "stderr";

// #429 — container kind classification for the workload-detail Init /
// Primary / Sidecar split. Anything outside the known set falls
// through to ``primary`` so the UI never has to handle an undefined
// case.
export type ContainerKind = "init" | "primary" | "sidecar";

export interface AstroliftContainerResources {
  cpuRequest: string;
  cpuLimit: string;
  memoryRequest: string;
  memoryLimit: string;
}

// Manual extension over the generated type: ``kind`` / restart
// history / per-container resources arrived with #429 and the
// codegen run is wired into the merge commit, so the manual type
// holds the contract until the schema gets refreshed.
export type AstroliftContainerStatus = GeneratedContainerStatus & {
  kind: ContainerKind;
  lastRestartReasons: string[];
  lastRestartAt: string | null;
  resources: AstroliftContainerResources;
};

export type AstroliftAppPod = Omit<GeneratedAppPod, "containerStatuses"> & {
  containerStatuses: AstroliftContainerStatus[];
};

// #429 — pod status grid on the workload detail page. Each bucket
// is one row of the grid; ``pods`` is the expander payload.
export interface AstroliftWorkloadPodSummary {
  name: string;
  age: string | null;
  ready: boolean;
}

export interface AstroliftWorkloadPodStatusBucket {
  status: string;
  count: number;
  percent: number;
  pods: AstroliftWorkloadPodSummary[];
}

export type AstroliftAppLogLine = Omit<GeneratedAppLogLine, "stream"> & {
  stream: LogStreamKind;
};

// #377 — observability cards (DNS / TLS / Workload identity).
//
// The narrow string-union types below mirror the SDK enums in
// `astrolift-providers/_sdk/{dns,tls,identity}.py` so switch-style
// renderers (status dots, chip colours) typecheck against valid
// values. The schema carries them as plain `String!` fields.

export type DnsPropagationStatus = "propagated" | "pending" | "unknown";

export type CertificateRenewalStatus = "auto" | "manual" | "failed" | "unknown";

export type IdentityBindingKind = "irsa" | "workload_identity" | "federated" | "unknown";

export type AstroliftAppDnsRecord = Omit<GeneratedAppDnsRecord, "propagationStatus"> & {
  propagationStatus: DnsPropagationStatus;
};

export type AstroliftAppCertificate = Omit<GeneratedAppCertificate, "renewalStatus"> & {
  renewalStatus: CertificateRenewalStatus;
};

export type AstroliftAppIdentityBinding = Omit<GeneratedAppIdentityBinding, "kind"> & {
  kind: IdentityBindingKind;
};
