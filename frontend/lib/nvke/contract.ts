import type { NvkeActionView } from "@/components/screens/nvke/types";

export interface NvkeContractView {
  revision: string;
  actions: NvkeActionView[];
  controllerOpenapi: unknown;
  resources: { kind: string; namespaced: boolean; schema: unknown }[];
}
const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
export function nativeSchemaText(contract: NvkeContractView, action: NvkeActionView): string {
  if (action.surface === "CONTROLLER") {
    const spec = object(contract.controllerOpenapi),
      paths = object(spec.paths);
    return JSON.stringify(
      {
        operation: object(paths[action.path])[action.verb.toLowerCase()],
        components: spec.components,
      },
      null,
      2
    );
  }
  return JSON.stringify(
    contract.resources.find((row) => row.kind === action.resourceKind)?.schema ?? {
      operation: action.key,
    },
    null,
    2
  );
}
export function nativeTemplateText(action: NvkeActionView): string {
  const request: Record<string, unknown> = {};
  const names = [...action.path.matchAll(/\{([^}]+)\}/g)].map((match) => match[1]);
  if (names.length) request.path = Object.fromEntries(names.map((name) => [name, ""]));
  if (action.surface === "DATASTORE") {
    if (["get", "replace", "delete", "deprecate"].includes(action.verb)) request.name = "";
    if (["replace", "delete", "deprecate"].includes(action.verb)) request.resource_version = "";
    if (["delete", "deprecate"].includes(action.verb)) request.uid = "";
    if (["create", "replace"].includes(action.verb))
      request.document = { metadata: { name: "" }, spec: {} };
    if (action.verb === "deprecate") request.deprecated = true;
    if (action.verb === "kubeconfig") {
      request.cluster_name = "";
      request.management_namespace = "";
    }
  } else if (["POST", "PUT", "PATCH"].includes(action.verb)) {
    request.body = action.surface === "CONTROLLER" ? {} : { metadata: { name: "" }, spec: {} };
    if (action.surface !== "CONTROLLER" && action.verb === "PUT") request.resource_version = "";
  } else if (action.surface !== "CONTROLLER" && action.verb === "DELETE")
    request.resource_version = "";
  return JSON.stringify(request, null, 2);
}

export const nativeMaintenancePermission = (key: string | undefined) =>
  key &&
  [
    "temporal.RenewK8sCertsV2",
    "temporal.RotateEtcdEncryptionKey",
    "temporal.RetireLostEtcdRotationNode",
    "temporal.reset.RotateEtcdEncryptionKey",
  ].includes(key)
    ? "nvke.admin"
    : null;
