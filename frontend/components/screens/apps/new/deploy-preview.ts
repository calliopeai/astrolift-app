import { parseToml, type TomlTable, type TomlValue } from "@/lib/manifest/toml";
import type { WorkloadKind } from "@/lib/manifest/model";
import { classifyTopology, type TopologyKind } from "@/lib/topology";

/** What the review step says will deploy (spec 44 §5.4: review anything that deploys). */
export interface DeployPreview {
  workloads: { name: string; kind: string; replicas: number | null }[];
  /** Managed service kinds (postgres, redis, ...). */
  services: string[];
  topology: TopologyKind;
}

const isTable = (v: TomlValue | undefined): v is TomlTable =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: TomlValue | undefined) => (typeof v === "string" ? v : "");

/**
 * Read the workloads and managed services out of a manifest. Lenient on
 * purpose (a workload is named by `name` or `slug`), since the backend
 * validator is authoritative; null when the text does not parse.
 */
export function deployPreview(manifestRaw: string): DeployPreview | null {
  if (!manifestRaw.trim()) return null;
  let doc: TomlTable;
  try {
    doc = parseToml(manifestRaw);
  } catch {
    return null;
  }
  const workloads = (Array.isArray(doc.workloads) ? doc.workloads : [])
    .filter(isTable)
    .map((w) => ({
      name: str(w.name) || str(w.slug) || "unnamed",
      kind: str(w.kind) || "deployment",
      replicas: typeof w.replicas === "number" ? w.replicas : null,
    }));
  const services = (Array.isArray(doc.managed_services) ? doc.managed_services : [])
    .filter(isTable)
    .map((s) => str(s.kind))
    .filter(Boolean);
  return {
    workloads,
    services,
    topology: classifyTopology({
      workloads: workloads.map((w) => ({ kind: w.kind as WorkloadKind, name: w.name })),
      managedServices: services,
    }),
  };
}
