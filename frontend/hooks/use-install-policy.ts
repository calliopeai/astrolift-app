"use client";

import { useQuery } from "@apollo/client/react";

import type {
  InstallManagedModelQuery,
  WithheldCapabilitiesQuery,
} from "@/graphql/__generated__/operations";
import {
  INSTALL_MANAGED_MODEL,
  WITHHELD_CAPABILITIES,
} from "@/graphql/install/install-policy.queries";

/**
 * What the installer may withhold (calliope-installer#447). `controllers`
 * holds the control plane to the minimal Kubernetes RBAC contract.
 */
export type WithheldCapability =
  | "dns"
  | "databases"
  | "load_balancers"
  | "clusters"
  | "controllers";

/**
 * Why each withheld capability is refused, keyed by capability. Empty while
 * loading, on error and on an install that withholds nothing: the server
 * refuses a withheld action with the same reason either way.
 */
export function useWithheldCapabilities({ skip = false } = {}): Partial<
  Record<WithheldCapability, string>
> {
  const { data } = useQuery<WithheldCapabilitiesQuery>(WITHHELD_CAPABILITIES, {
    fetchPolicy: "cache-first",
    skip,
  });
  return Object.fromEntries(
    (data?.astroliftWithheldCapabilities ?? []).map((row) => [row.capability, row.reason])
  );
}

/** The model the install serves on its own GPUs, or null (calliope-installer#446). */
export function useInstallManagedModel() {
  const { data } = useQuery<InstallManagedModelQuery>(INSTALL_MANAGED_MODEL, {
    fetchPolicy: "cache-first",
  });
  const model = data?.astroliftInstallManagedModel;
  return model ? { modelId: model.modelId, replicas: model.replicas ?? null } : null;
}
