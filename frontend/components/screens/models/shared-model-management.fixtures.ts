import type { SharedModelManagementPanelProps } from "./SharedModelManagementPanel";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import { modelSettingsRequest } from "./shared-model-settings";
const model = sharedModelDetailProps.model!;
const draft: SharedModelManagementPanelProps["draft"] = {
  name: model.name,
  computeMode: "gpu",
  cpuRequest: "4",
  memoryRequest: "16Gi",
  gpuCount: "1",
  cpuKvCacheGiB: "",
  allowSubscriptions: true,
};
export const sharedModelManagementProps: SharedModelManagementPanelProps = {
  model,
  draft,
  blocked: false,
  canManage: true,
  capabilityLoading: false,
  capabilityError: null,
  onRetryCapabilities: () => {},
  onDraftChange: () => {},
  admissionLoading: false,
  admissionError: null,
  onRetryAdmission: () => {},
  admission: {
    requestKey: JSON.stringify(modelSettingsRequest(model, draft)),
    eligible: true,
    reason: null,
    runtimeVersion: "0.15.1",
    architecture: "amd64",
    hardwareAdmission: "operator_declared",
  },
  onUpdate: async () => ({ accepted: true, operationId: "shared-model-reconcile" }),
  onDelete: async () => ({ accepted: true, operationId: "shared-model-delete" }),
};
