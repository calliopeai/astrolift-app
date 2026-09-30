import { defaultListState, type ListStateController } from "@/components/list/list-state";
import type { ModelPage } from "./ModelSubscriptionsPanel";
import type { ModelSubscriptionsPanelProps } from "./ModelSubscriptionsPanel";

export const subscriptionProps: ModelSubscriptionsPanelProps = {
  deployment: {
    id: "shared-model",
    version: 5,
    organizationId: "org",
    name: "Qwen production",
    runtimeAdmission: "configured",
    clusterId: "cluster-one",
    providerId: "provider-one",
    subscriptionsEnabled: true,
  },
  targets: fakeModelPage({
    rows: [
      {
        id: "env-production",
        version: 3,
        appSlug: "storefront",
        environmentName: "production",
        admission: "allowed",
        clusterId: "cluster-one",
        reason: null,
      },
      {
        id: "env-staging",
        version: 4,
        appSlug: "storefront",
        environmentName: "staging",
        admission: "allowed",
        clusterId: "cluster-one",
        reason: null,
      },
    ],
  }),
  subscriptions: fakeModelPage({
    rows: [
      {
        id: "subscription-one",
        version: 2,
        alias: "chat",
        bindingPrefix: "MODEL_CHAT_",
        appSlug: "storefront",
        environmentName: "production",
        status: "active",
        desiredRevision: 2,
        appliedRevision: 2,
        reason: null,
        canRevoke: true,
      },
    ],
  }),
  onSubscribe: async () => ({ accepted: true }),
  onRevoke: async () => ({ accepted: true }),
};

export function fakeModelPage<T>(patch: Partial<ModelPage<T>> = {}): ModelPage<T> {
  const noop = () => {};
  const definition = {
    id: "models.subscription-test",
    fields: [],
    searchPlaceholder: "Search app environments",
    defaultSort: [],
    views: [{ key: "all", label: "All", filters: {} }],
    paging: "cursor" as const,
    pageSizes: [20],
  };
  const list: ListStateController = {
    definition,
    state: defaultListState(definition),
    filters: {},
    isFiltered: false,
    setSearch: noop,
    setFilter: noop,
    applySearch: noop,
    clearFilters: noop,
    toggleSort: noop,
    setSort: noop,
    setPage: noop,
    older: noop,
    newer: noop,
    hasNewer: false,
    setPageSize: noop,
    viewHref: () => "#",
    hiddenColumns: [],
    toggleColumn: noop,
    mode: "list",
    setMode: noop,
  };
  return {
    list,
    rows: [],
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    nextCursor: null,
    totalCount: null,
    ...patch,
  };
}

import type { HuggingFaceCataloguePanelProps } from "./HuggingFaceCataloguePanel";
export const hfCatalogueProps: HuggingFaceCataloguePanelProps = {
  page: fakeModelPage({
    rows: [
      {
        repoId: "Qwen/Qwen3-8B",
        revisionSha: null,
        author: "Qwen",
        pipelineTag: "text-generation",
        library: "transformers",
        license: "apache-2.0",
        architectures: ["Qwen3ForCausalLM"],
        gated: "NONE",
        downloads: 15234,
        likes: null,
        compatibility: "UNKNOWN",
      },
    ],
  }),
  state: "AVAILABLE",
  source: "https://huggingface.co/api/models",
  observedAt: "2026-09-30T15:30:00Z",
  retryAfterSeconds: null,
  selectedRepoId: null,
  revision: "",
  onSelect: () => {},
  onRevisionChange: () => {},
  resolving: false,
  resolvedModel: null,
  resolutionError: null,
  resolutionSource: null,
  resolutionObservedAt: null,
  onRetryResolution: () => {},
  onUseRevision: () => {},
};

import { sharedModelRequest } from "./shared-model-form";
import type { SharedModelDeploymentScreenProps } from "./SharedModelDeploymentScreen";
const placementDraft = {
  name: "qwen-shared",
  computeMode: "gpu" as const,
  cpuRequest: "2",
  memoryRequest: "8Gi",
  gpuCount: "1",
  cpuKvCacheGiB: "",
  allowSubscriptions: true,
};
const placementModel = { repoId: "Qwen/Qwen3-8B", revisionSha: "a".repeat(40) };
export const sharedDeploymentProps: SharedModelDeploymentScreenProps = {
  organizationId: "org",
  catalogue: null,
  model: placementModel,
  onClearModel: () => {},
  clusters: fakeModelPage({
    rows: [
      {
        id: "cluster-one",
        providerId: "provider-one",
        slug: "production",
        name: "Production",
        active: true,
        reason: null,
      },
    ],
  }),
  selectedClusterId: "cluster-one",
  onSelectCluster: () => {},
  draft: placementDraft,
  onDraftChange: () => {},
  admission: {
    requestKey: JSON.stringify(
      sharedModelRequest(
        "org",
        { id: "cluster-one", providerId: "provider-one" },
        placementModel,
        placementDraft
      )
    ),
    eligible: true,
    reason: null,
    runtimeVersion: null,
    architecture: "x86_64",
    hardwareAdmission: "operator_declared",
  },
  admissionLoading: false,
  admissionError: null,
  onRetryAdmission: () => {},
  onDeploy: async () => ({ accepted: true, deploymentId: "created-model" }),
};
