import type { AstroliftProviderPlugin } from "@/graphql/clusters/clusters.types";

import type { CloudProvidersPanelViewProps } from "../providers/CloudProvidersPanel";

import type { DeployModelSheetViewProps } from "./DeployModelSheet";
import type { ModelReplicasViewProps } from "./ModelReplicas";
import type { ModelEndpoint, ModelsScreenProps } from "./ModelsScreen";
import type { ModelTestDialogViewProps } from "./ModelTestDialog";
import type { ModelTestOutcome } from "./use-model-test";

/** Hand-typed fixtures for the Models and Providers screens. */

const noop = () => {};
const yes = async () => true;

/** The JSON scalar is typed as an object but carries any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

export const LONG =
  "platform-team-shared-production-inference-endpoint-us-west-2-with-a-deliberately-long-name-that-keeps-going";

// ---- Models ---------------------------------------------------------------

export const MODELS: ModelEndpoint[] = [
  {
    id: "m1",
    name: "qwen",
    variant: "vllm",
    status: "active",
    statusError: "",
    config: json({ model: "Qwen/Qwen3-8B", gpu: 2, replicas: 1 }),
    registeredAppSlug: "chat",
    projectSlug: "",
    ownerScope: "app",
    clusterSlug: "gpu-east",
    environmentName: "prod",
  },
  {
    id: "m2",
    name: "llama-70b",
    variant: "vllm",
    status: "provisioning",
    statusError: "",
    config: json({ model: "meta-llama/Llama-3.3-70B-Instruct", gpu: 4, replicas: 0 }),
    registeredAppSlug: "research",
    projectSlug: "",
    ownerScope: "app",
    clusterSlug: "gpu-east",
    environmentName: "staging",
  },
  {
    id: "m3",
    name: "embeddings",
    variant: "kserve",
    status: "active",
    statusError: "",
    config: json({ storage_uri: "s3://models/bge-large", gpu: 1, mig_profile: "1g.10gb" }),
    registeredAppSlug: "",
    projectSlug: "search",
    ownerScope: "project",
    clusterSlug: "gpu-east",
    environmentName: "",
  },
  {
    id: "m4",
    name: "claude",
    variant: "bedrock",
    status: "failed",
    statusError: "AccessDeniedException: model access not granted in us-east-1",
    config: json({ model_id: "anthropic.claude-sonnet" }),
    registeredAppSlug: "",
    projectSlug: "shared",
    ownerScope: "project",
    clusterSlug: "aws-main",
    environmentName: "",
  },
];

export const LONG_MODELS: ModelEndpoint[] = [
  {
    ...MODELS[0],
    id: "l1",
    name: LONG,
    config: json({ model: `acme-research/${LONG}`, gpu: 16, replicas: 8 }),
    registeredAppSlug: LONG,
    environmentName: LONG,
  },
  {
    ...MODELS[3],
    id: "l2",
    name: LONG,
    variant: "azure_ai_foundry",
    statusError: LONG,
    projectSlug: LONG,
  },
];

export const MODELS_SCREEN: Omit<
  ModelsScreenProps,
  "renderReplicas" | "renderTest" | "renderDeploySheet"
> = {
  models: MODELS,
  loading: false,
  error: undefined,
  refetch: noop,
};

export const REPLICAS: ModelReplicasViewProps = {
  name: "qwen",
  replicas: 1,
  loading: false,
  setReplicas: async () => {},
};

export const TEST_DIALOG: ModelTestDialogViewProps = {
  name: "qwen",
  loading: false,
  outcome: null,
  send: async () => {},
  reset: noop,
};

export const TEST_REPLY: Extract<ModelTestOutcome, { kind: "reply" }> = {
  kind: "reply",
  status: "succeeded",
  reply: "Hello! How can I help?",
  latencyMs: 842,
  promptTokens: 5,
  completionTokens: 7,
  totalTokens: 12,
  error: "",
};

// 2 nodes: 4 x 80 GB and 1 x 24 GB.
const GPU_CAPS = json({
  gpu: {
    nodes: [
      { gpus: { "nvidia.com/gpu": 4 }, labels: { "nvidia.com/gpu.memory": "81920" } },
      { gpus: { "nvidia.com/gpu": 1 }, labels: { "nvidia.com/gpu.memory": "24576" } },
    ],
  },
});

export const DEPLOY: DeployModelSheetViewProps = {
  open: true,
  onOpenChange: noop,
  onDeployed: noop,
  envs: [
    {
      id: "e1",
      name: "prod",
      registeredAppSlug: "chat",
      clusterId: "c1",
      clusterSlug: "gpu-east",
    },
    {
      id: "e2",
      name: "staging",
      registeredAppSlug: "research",
      clusterId: "c2",
      clusterSlug: "cpu-west",
    },
    { id: "e3", name: "dev", registeredAppSlug: "sandbox", clusterId: null, clusterSlug: null },
  ],
  clusters: [
    { id: "c1", capabilities: GPU_CAPS },
    { id: "c2", capabilities: json({}) },
  ],
  loading: false,
  deploy: yes,
};

// ---- Providers ------------------------------------------------------------

const DRIVERS = json({
  ClusterDriver: {},
  IngressDriver: {},
  DnsDriver: {},
  CertDriver: {},
  SecretsDriver: {},
  RegistryDriver: {},
  DatabaseDriver: {},
  CacheDriver: {},
  QueueDriver: {},
  ObjectStoreDriver: {},
});

export const PLUGINS: AstroliftProviderPlugin[] = [
  {
    id: "p1",
    slug: "aws",
    name: "Amazon Web Services",
    version: "1.8.2",
    capabilitiesManifest: DRIVERS,
    isEnabled: true,
  },
  {
    id: "p2",
    slug: "k8s_native",
    name: "Self-managed Kubernetes",
    version: "0.9.0",
    capabilitiesManifest: json({ ClusterDriver: {}, IngressDriver: {} }),
    isEnabled: true,
  },
  {
    id: "p3",
    slug: "gcp",
    name: "Google Cloud",
    version: "1.2.0",
    capabilitiesManifest: json({ ClusterDriver: {}, IngressDriver: {}, DnsDriver: {} }),
    isEnabled: true,
  },
  {
    id: "p4",
    slug: "azure",
    name: "Microsoft Azure",
    version: "1.1.4",
    capabilitiesManifest: json({ ClusterDriver: {} }),
    isEnabled: true,
  },
];

export const CLOUD_PROVIDERS: CloudProvidersPanelViewProps = {
  loading: false,
  pluginCount: PLUGINS.length,
  configured: [
    { plugin: PLUGINS[0], clusters: 3 },
    { plugin: PLUGINS[1], clusters: 1 },
  ],
  available: [PLUGINS[2], PLUGINS[3]],
  viewMode: "card",
  setViewMode: noop,
};

export const LONG_PLUGIN: AstroliftProviderPlugin = {
  id: "pl",
  slug: LONG,
  name: LONG,
  version: "12.345.6789-rc.1+build.2026092801",
  capabilitiesManifest: DRIVERS,
  isEnabled: true,
};
