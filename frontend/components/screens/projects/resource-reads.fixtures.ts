import { fakeController } from "@/components/data-table/fixtures";
import type {
  ManagedResourceDetailProps,
  ManagedResourceAttachment,
} from "./ManagedResourceDetail";
import type { ProjectManagedResource } from "./ProjectManagedResourceList";

export const RESOURCE: ProjectManagedResource = {
  id: "01930000-0000-7000-8000-000000000001",
  contextRevision: "reviewed-resource-context",
  version: 4,
  name: "shared-cache",
  kind: "redis",
  variant: "elasticache",
  status: "active",
  ownerScope: "project",
  organizationId: "01930000-0000-7000-8000-000000000002",
  projectId: "01930000-0000-7000-8000-000000000003",
  projectSlug: "example-platform",
  registeredAppId: null,
  registeredAppSlug: "",
  clusterId: "01930000-0000-7000-8000-000000000004",
  clusterSlug: "production",
  clusterVersion: 2,
  environmentId: null,
  environmentName: "production",
  environmentVersion: null,
  createdAt: "2026-10-01T00:00:00Z",
  updatedAt: "2026-10-02T00:00:00Z",
  operationKind: "provision",
  operationWorkflowId: "service-provision-reviewed",
  operationRunId: "run-reviewed",
  operationStartedAt: "2026-10-01T00:00:00Z",
  operationCompletedAt: "2026-10-01T00:00:20Z",
};

export const ATTACHMENT: ManagedResourceAttachment = {
  id: "01930000-0000-7000-8000-000000000005",
  version: 1,
  managedServiceId: RESOURCE.id,
  consumerKind: "app_environment",
  consumerId: "01930000-0000-7000-8000-000000000006",
  consumerSlug: "example-api",
  registeredAppId: "01930000-0000-7000-8000-000000000007",
  environmentId: "01930000-0000-7000-8000-000000000006",
  environmentName: "production",
  clusterId: RESOURCE.clusterId,
  createdAt: "2026-10-01T00:00:00Z",
};

const noop = () => {};
export const RESOURCE_DETAIL: ManagedResourceDetailProps = {
  target: RESOURCE,
  current: RESOURCE,
  loading: false,
  refused: false,
  onClose: noop,
  onRefresh: noop,
  attachments: fakeController({ rows: [ATTACHMENT], totalCount: 251, hasNext: true }),
  canUpdate: true,
  canPreview: true,
  costLoading: false,
  costError: false,
  onCost: noop,
  mutationLoading: false,
  onReprovision: noop,
  onDeprovision: noop,
  onDetach: noop,
  onAttach: async () => {},
};
