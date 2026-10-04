import type { SharedModelsScreenProps } from "./SharedModelsScreen";
import { fakeModelPage } from "./shared-model.fixtures";
import { sharedModelsList } from "./shared-models-list";
import { defaultListState } from "@/components/list/list-state";
import en from "@/messages/en.json";
const copy = en.models.shared.deployments;
const definition = sharedModelsList(copy);
const base = fakeModelPage();
export const sharedModelsProps: SharedModelsScreenProps = {
  page: fakeModelPage({
    list: { ...base.list, definition, state: defaultListState(definition) },
    totalCount: 42,
    rows: [
      {
        id: "shared-qwen",
        name: "Qwen production",
        modelRepo: "Qwen/Qwen3-8B",
        revisionSha: "a".repeat(40),
        sourceKind: "huggingface",
        localManifestSha256: null,
        desiredResources: { cpuRequest: "2", memoryRequest: "8Gi", gpuCount: 1 },
        clusterId: "cluster-one",
        providerId: "provider-one",
        clusterName: "Production",
        clusterSlug: "production",
        computeMode: "gpu",
        status: "active",
        reason: null,
        ready: true,
        readinessObservedAt: "2026-09-30T15:30:00Z",
        subscriptionsEnabled: true,
        sharingMode: "SHARED",
      },
      {
        id: "shared-cpu",
        name: "CPU generation",
        modelRepo: "publisher/generation-small",
        revisionSha: "b".repeat(40),
        sourceKind: "huggingface",
        localManifestSha256: null,
        desiredResources: { cpuRequest: "2", memoryRequest: "8Gi", gpuCount: 0 },
        clusterId: "cluster-two",
        providerId: "provider-two",
        clusterName: "Development",
        clusterSlug: "development",
        computeMode: "cpu",
        status: "provisioning",
        reason: null,
        ready: false,
        readinessObservedAt: null,
        subscriptionsEnabled: false,
        sharingMode: "SHARED",
      },
    ],
  }),
};
export function localizedSharedModelsProps(
  copy: typeof en.models.shared.deployments
): SharedModelsScreenProps {
  const definition = sharedModelsList(copy);
  return {
    page: {
      ...sharedModelsProps.page,
      list: { ...sharedModelsProps.page.list, definition, state: defaultListState(definition) },
    },
  };
}
