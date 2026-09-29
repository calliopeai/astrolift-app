/**
 * Agents › Models (spec 44 §4.4, §5.1): views All · Mine · Endpoints ·
 * Hosted (GPU), serving, status, owner and cluster filters, numbered pages.
 * Endpoints are the cloud-served models (Bedrock, Azure OpenAI, Foundry,
 * Vertex); Hosted (GPU) run on the org's own GPUs (vLLM, KServe).
 *
 * Why the step runs here and not on the server: `astroliftModelEndpoints`
 * returns the org's whole list with no argument at all, and an endpoint
 * records the app or project that owns it but not a person. So Mine lists
 * every model, and `selectModels` answers the rest; each view says so. The
 * formatting the list and the detail share lives here too. Pure.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import {
  clientNote,
  lower,
  selectPage,
  withAllNote,
} from "@/components/screens/agents/skills/catalog";
import type { ListModelEndpointsQuery } from "@/graphql/__generated__/operations";

export type ModelEndpoint = ListModelEndpointsQuery["astroliftModelEndpoints"][number];

/** Self-hosted variants run on the org's own GPUs; the rest are cloud-served. */
export const HOSTED = new Set(["vllm", "kserve"]);

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

const NOTE = clientNote("the org's model endpoints come in one read");

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
  searchPlaceholder: "Search models, ids, owners…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: withAllNote(
    standardViews(
      {},
      [
        { key: "endpoints", label: "Endpoints", filters: { hosted: "0" }, note: NOTE },
        { key: "hosted", label: "Hosted (GPU)", filters: { hosted: "1" }, note: NOTE },
      ],
      {
        mineNote: `Mine lists every model until endpoints record who deployed them. ${NOTE}`,
      }
    ),
    NOTE
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export function selectModels(
  models: ModelEndpoint[],
  q: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
) {
  return selectPage(
    models,
    {
      matches: (m, f) => {
        if (f.hosted === "1" && !isHosted(m)) return false;
        if (f.hosted === "0" && isHosted(m)) return false;
        if (f.serving && lower(m.variant) !== lower(f.serving)) return false;
        if (f.status && lower(m.status) !== lower(f.status)) return false;
        if (f.owner && m.ownerScope !== f.owner) return false;
        if (f.cluster && lower(m.clusterSlug) !== lower(f.cluster)) return false;
        return true;
      },
      text: (m) => [m.name, modelId(m), m.variant, m.registeredAppSlug, m.projectSlug],
      sortValue: {
        name: (m) => lower(m.name),
        serving: (m) => m.variant,
        status: (m) => m.status,
      },
      id: (m) => m.id,
    },
    q
  );
}
