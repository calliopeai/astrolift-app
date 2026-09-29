/**
 * Agents › Models (spec 44 §4.4, §5.1): views All · Mine · Endpoints ·
 * Hosted (GPU), serving, status, owner and cluster filters, numbered pages.
 * Endpoints are the cloud-served models (Bedrock, Azure OpenAI, Foundry,
 * Vertex); Hosted (GPU) run on the org's own GPUs (vLLM, KServe).
 *
 * The server answers every view, chip, search, sort and page
 * (`astroliftModelEndpointsPage`, #2155): Mine is the endpoints the viewer
 * deployed (`deployedBy: "me"`), and Endpoints and Hosted are variant lists.
 * The formatting the list and the detail share lives here too. Pure.
 */
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import {
  type NumberedListQuery,
  numberedPageVariables,
} from "@/components/screens/agents/skills/catalog";
import type { AstroliftManagedService } from "@/graphql/__generated__/schema";

/** One model endpoint, as the Models list and the detail select it. */
export type ModelEndpoint = Pick<
  AstroliftManagedService,
  | "id"
  | "name"
  | "variant"
  | "status"
  | "statusError"
  | "config"
  | "registeredAppSlug"
  | "projectSlug"
  | "ownerScope"
  | "clusterSlug"
  | "environmentName"
  | "deployedByEmail"
  | "deployedByMe"
>;

/** Self-hosted variants run on the org's own GPUs. */
export const HOSTED_VARIANTS = ["vllm", "kserve"];

/** The cloud-served variants, one per provider driver. */
export const CLOUD_VARIANTS = ["bedrock", "vertex_ai", "azure_openai", "azure_foundry"];

export const HOSTED = new Set(HOSTED_VARIANTS);

export const isHosted = (m: Pick<ModelEndpoint, "variant">) => HOSTED.has(m.variant);

const configOf = (m: Pick<ModelEndpoint, "config">) => (m.config ?? {}) as Record<string, unknown>;

/** The model id the endpoint serves, from whichever key its variant uses. */
export function modelId(m: Pick<ModelEndpoint, "config">): string {
  const config = configOf(m);
  const value = config.model ?? config.model_name ?? config.model_id ?? config.storage_uri;
  return typeof value === "string" ? value : "";
}

export function gpuLabel(m: Pick<ModelEndpoint, "variant" | "config">): string {
  if (!isHosted(m)) return "cloud";
  const config = configOf(m);
  const gpu = Number(config.gpu ?? (m.variant === "vllm" ? 1 : 0));
  if (!gpu) return "CPU";
  const mig = typeof config.mig_profile === "string" ? ` × ${config.mig_profile}` : "";
  return `${gpu} GPU${gpu === 1 ? "" : "s"}${mig}`;
}

/** Where the endpoint is declared: the project's resources, or the app's managed services. */
export function ownerHref(m: ModelEndpoint): string {
  return m.ownerScope === "project"
    ? `/projects/${m.projectSlug}/resources`
    : `/apps/${m.registeredAppSlug}/managed-services`;
}

export function ownerLabel(m: ModelEndpoint): string {
  return m.ownerScope === "project"
    ? m.projectSlug
    : `${m.registeredAppSlug} · ${m.environmentName}`;
}

/** The endpoint's status as a dot: live, in flight, failed, or anything else. */
export function modelStatusDot(status: string): "ok" | "warn" | "error" | "muted" | "pending" {
  if (status === "active" || status === "ready") return "ok";
  if (status === "failed" || status === "error") return "error";
  if (status === "provisioning" || status === "pending") return "pending";
  return "muted";
}

export const MODELS_LIST: ListDefinition = {
  id: "agents.models",
  fields: [
    // Free text: vllm, kserve, bedrock, azure_openai, …
    { key: "serving", label: "Serving" },
    // Free text: active, provisioning, failed, …
    { key: "status", label: "Status" },
    {
      key: "owner",
      label: "Owner",
      options: [
        { value: "app", label: "App" },
        { value: "project", label: "Project" },
      ],
    },
    // Free text: matched on the cluster slug.
    { key: "cluster", label: "Cluster" },
  ],
  // The server matches name, variant, owning app and project, and cluster.
  searchPlaceholder: "Search models, owners, clusters…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews(
    { deployedBy: "me" },
    [
      { key: "endpoints", label: "Endpoints", filters: { hosted: "0" } },
      { key: "hosted", label: "Hosted (GPU)", filters: { hosted: "1" } },
    ],
    {
      mineNote:
        "Mine means models you deployed. Endpoints declared in a manifest, or deployed before Astrolift recorded who deployed them, show only in All.",
    }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export interface ModelEndpointsFilter {
  status?: string[];
  variant?: string[];
  cluster?: string[];
  ownerScope?: string[];
  deployedBy?: string[];
}

/**
 * The list state as `astroliftModelEndpointsPage` variables. The Endpoints
 * and Hosted views are variant lists and a Serving chip narrows within them.
 * A chip outside its view (vllm on Endpoints) can match nothing, and an
 * empty variant list would read as no filter at all, so that state is
 * `null`: the hook asks nothing and shows the filtered-empty state.
 */
export function modelEndpointsPageVariables(q: NumberedListQuery) {
  const f = q.filters;
  const filter: ModelEndpointsFilter = {};
  if (f.status) filter.status = [f.status];
  if (f.cluster) filter.cluster = [f.cluster];
  if (f.owner) filter.ownerScope = [f.owner];
  if (f.deployedBy) filter.deployedBy = [f.deployedBy];
  const view = f.hosted === "1" ? HOSTED_VARIANTS : f.hosted === "0" ? CLOUD_VARIANTS : null;
  if (f.serving) {
    const serving = f.serving.toLowerCase();
    if (view && !view.includes(serving)) return null;
    filter.variant = [serving];
  } else if (view) {
    filter.variant = view;
  }
  return numberedPageVariables(q, filter, MODELS_LIST.defaultSort);
}
