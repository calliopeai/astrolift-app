"use client";

import { useLazyQuery, useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import {
  ATTACH_AGENT_SECRET_BUNDLE,
  DETACH_AGENT_SECRET_BUNDLE,
} from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_WORKLOADS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject, MutationResult } from "@/graphql/identity/identity.types";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import {
  ATTACH_PROJECT_MANAGED_SERVICE,
  ATTACH_SECRET_BUNDLE,
  CREATE_PROJECT_SECRET_BUNDLE,
  DELETE_PROJECT_BUNDLE_SECRET,
  DELETE_PROJECT_SECRET_BUNDLE,
  DEPROVISION_PROJECT_MANAGED_SERVICE,
  DETACH_PROJECT_MANAGED_SERVICE,
  DETACH_SECRET_BUNDLE,
  PROVISION_PROJECT_MANAGED_SERVICE,
  REPROVISION_PROJECT_MANAGED_SERVICE,
  REVEAL_PROJECT_BUNDLE_SECRET,
  SET_PROJECT_BUNDLE_SECRET,
} from "@/graphql/services/services.mutations";
import {
  LIST_PROJECT_MANAGED_SERVICE_CATALOG,
  LIST_PROJECT_RESOURCES,
  PREVIEW_MANAGED_SERVICE_COST,
} from "@/graphql/services/services.queries";
import type {
  AstroliftManagedService,
  AstroliftManagedServiceCatalogEntry,
  AstroliftProjectSecretBundle,
} from "@/graphql/services/services.types";
import { useConfirm } from "@/hooks/use-confirm";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface ProjectsData {
  astroliftProjects: AstroliftProject[];
}
interface AgentsData {
  agentWorkloads: AstroliftAgentListItem[];
}
interface AppsData {
  astroliftApps: AstroliftRegisteredApp[];
}
interface EnvironmentsData {
  astroliftEnvironments: AstroliftAppEnvironment[];
}
interface ResourcesData {
  astroliftProjectResourceClusters: AstroliftTenantCluster[];
  astroliftProjectManagedServices: AstroliftManagedService[];
  astroliftProjectSecretBundles: AstroliftProjectSecretBundle[];
}
interface CatalogData {
  astroliftProjectManagedServiceCatalog: AstroliftManagedServiceCatalogEntry[];
}
export interface ManagedServiceCostPreview {
  managedServiceId: string;
  available: boolean;
  reason: string;
  message: string;
  monthlyTotal: number | null;
  currency: string;
  pricingSourceUrl: string;
  pricingFetchedAt: string;
  notes: string[];
  approximate: boolean;
}
interface CostPreviewData {
  astroliftManagedServiceCostPreview: ManagedServiceCostPreview | null;
}

/** The project fields the resources screen reads. */
export type ProjectResourcesProject = Pick<AstroliftProject, "id" | "slug" | "name">;
/** The cluster fields the resources screen reads. */
export type ProjectResourceCluster = Pick<
  AstroliftTenantCluster,
  "id" | "slug" | "name" | "region"
>;
/** The agent fields the resources screen reads. */
export type ProjectResourceAgent = Pick<AstroliftAgentListItem, "id" | "slug" | "name">;
/** The app-environment fields the resources screen reads. */
export type ProjectAppEnvironment = Pick<
  AstroliftAppEnvironment,
  "id" | "name" | "registeredAppSlug" | "clusterSlug"
>;
export type BundleConsumer = AstroliftProjectSecretBundle["consumers"][number];

/** What the Add resource form submits. */
export interface ProvisionResourceInput {
  entry: AstroliftManagedServiceCatalogEntry | undefined;
  name: string;
  size: string;
  config: Record<string, unknown>;
  advancedConfig: string;
  agentSlugs: string[];
  appEnvironmentIds: string[];
}

function firstError(result?: MutationResult<unknown> | null): string {
  return result?.errors?.[0]?.message ?? "The operation failed";
}

/**
 * Project resources: managed infrastructure, shared secret bundles, the
 * cluster catalogue, and every mutation that provisions, attaches,
 * reveals, or removes them. Owns the consumer-dialog targets because
 * each attach/detach replaces them with the refetched row. The data half
 * of ProjectResourcesScreen.
 */
export function useProjectResources(slug: string) {
  const permissions = useMyPermissions();
  const confirm = useConfirm();
  const projects = useQuery<ProjectsData>(LIST_PROJECTS);
  const project = projects.data?.astroliftProjects.find((row) => row.slug === slug);
  const projectId = project?.id ?? "";
  const orgId = project?.organization.id ?? "";
  const resources = useQuery<ResourcesData>(LIST_PROJECT_RESOURCES, {
    variables: { projectId },
    skip: !projectId,
    fetchPolicy: "cache-and-network",
    pollInterval: 10_000,
  });
  const agents = useQuery<AgentsData>(LIST_AGENT_WORKLOADS, {
    variables: { orgId, projectSlug: slug },
    skip: !orgId,
  });
  const apps = useQuery<AppsData>(LIST_APPS, { skip: !projectId });
  const environments = useQuery<EnvironmentsData>(LIST_ENVIRONMENTS, {
    variables: { appSlug: null },
    skip: !projectId,
  });
  const refetchResources = () => resources.refetch({ projectId });
  const [clusterId, setClusterId] = React.useState("");
  const [bundleClusterId, setBundleClusterId] = React.useState("");
  const [consumerService, setConsumerService] = React.useState<AstroliftManagedService | null>(
    null
  );
  const [consumerBundle, setConsumerBundle] = React.useState<AstroliftProjectSecretBundle | null>(
    null
  );
  const [revealed, setRevealed] = React.useState<Record<string, string>>({});
  const [costPreviews, setCostPreviews] = React.useState<Record<string, ManagedServiceCostPreview>>(
    {}
  );
  const [loadCostPreview, costPreviewRequest] = useLazyQuery<CostPreviewData>(
    PREVIEW_MANAGED_SERVICE_COST,
    { fetchPolicy: "network-only" }
  );
  const refreshConsumerBundle = async (bundleId: string) => {
    const refreshed = await refetchResources();
    setConsumerBundle(
      refreshed.data?.astroliftProjectSecretBundles.find((row) => row.id === bundleId) ?? null
    );
  };
  const refreshConsumerService = async (serviceId: string) => {
    const refreshed = await refetchResources();
    setConsumerService(
      refreshed.data?.astroliftProjectManagedServices.find((row) => row.id === serviceId) ?? null
    );
  };
  const canUpdate = permissions.can("project.update");
  const canWriteSecrets = permissions.can("secret.write");
  const canReadSecrets = permissions.can("secret.read");

  const [provision, provisionState] = useMutation<{
    provisionProjectManagedService: MutationResult<AstroliftManagedService>;
  }>(PROVISION_PROJECT_MANAGED_SERVICE);
  const [reprovision] = useMutation<{
    reprovisionProjectManagedService: MutationResult<AstroliftManagedService>;
  }>(REPROVISION_PROJECT_MANAGED_SERVICE);
  const [attachConsumer, attachConsumerState] = useMutation<{
    attachProjectManagedService: MutationResult<AstroliftManagedService["attachments"][number]>;
  }>(ATTACH_PROJECT_MANAGED_SERVICE);
  const [detachConsumer, detachConsumerState] = useMutation<{
    detachProjectManagedService: MutationResult<AstroliftManagedService["attachments"][number]>;
  }>(DETACH_PROJECT_MANAGED_SERVICE);
  const [deprovision] = useMutation<{
    deprovisionProjectManagedService: MutationResult<{ id: string; deleted: boolean }>;
  }>(DEPROVISION_PROJECT_MANAGED_SERVICE);
  const [createBundle, createBundleState] = useMutation<{
    createProjectSecretBundle: MutationResult<AstroliftProjectSecretBundle>;
  }>(CREATE_PROJECT_SECRET_BUNDLE);
  const [setSecret] = useMutation<{
    setProjectBundleSecretValue: MutationResult<AstroliftProjectSecretBundle>;
  }>(SET_PROJECT_BUNDLE_SECRET);
  const [deleteSecret] = useMutation<{
    deleteProjectBundleSecretValue: MutationResult<AstroliftProjectSecretBundle>;
  }>(DELETE_PROJECT_BUNDLE_SECRET);
  const [revealSecret] = useMutation<{
    revealProjectBundleSecretValue: MutationResult<{
      key: string;
      value: string;
      provider: string;
      revealedAt: string;
    }>;
  }>(REVEAL_PROJECT_BUNDLE_SECRET);
  const [deleteBundle] = useMutation<{
    deleteProjectSecretBundle: MutationResult<AstroliftProjectSecretBundle>;
  }>(DELETE_PROJECT_SECRET_BUNDLE);
  const [attachAgentBundle, attachAgentBundleState] = useMutation<{
    attachAgentSecretBundle: MutationResult<unknown>;
  }>(ATTACH_AGENT_SECRET_BUNDLE);
  const [detachAgentBundle, detachAgentBundleState] = useMutation<{
    detachAgentSecretBundle: MutationResult<unknown>;
  }>(DETACH_AGENT_SECRET_BUNDLE);
  const [attachAppBundle, attachAppBundleState] = useMutation<{
    attachSecretBundle: MutationResult<unknown>;
  }>(ATTACH_SECRET_BUNDLE);
  const [detachAppBundle, detachAppBundleState] = useMutation<{
    detachSecretBundle: MutationResult<unknown>;
  }>(DETACH_SECRET_BUNDLE);

  const clusters: ProjectResourceCluster[] = resources.data?.astroliftProjectResourceClusters ?? [];
  const effectiveClusterId = clusterId || clusters[0]?.id || "";
  const effectiveBundleClusterId = bundleClusterId || clusters[0]?.id || "";
  const effectiveClusterSlug = clusters.find((row) => row.id === effectiveClusterId)?.slug;
  const catalog = useQuery<CatalogData>(LIST_PROJECT_MANAGED_SERVICE_CATALOG, {
    variables: { projectId, clusterId: effectiveClusterId },
    skip: !projectId || !effectiveClusterId,
  });

  const services = resources.data?.astroliftProjectManagedServices ?? [];
  const bundles = resources.data?.astroliftProjectSecretBundles ?? [];
  const projectAppSlugs = new Set(
    (apps.data?.astroliftApps ?? [])
      .filter((app) => app.projectId === projectId)
      .map((app) => app.slug)
  );
  const projectAppEnvironments: ProjectAppEnvironment[] = (
    environments.data?.astroliftEnvironments ?? []
  ).filter((env) => projectAppSlugs.has(env.registeredAppSlug));
  const projectAgents: ProjectResourceAgent[] = agents.data?.agentWorkloads ?? [];

  /** Resolves true when provisioning started (close and reset the form). */
  async function onProvision(input: ProvisionResourceInput): Promise<boolean> {
    const entry = input.entry;
    if (!entry?.available) {
      toast.error("Choose an available resource variant");
      return false;
    }
    let advancedConfig: Record<string, unknown>;
    try {
      advancedConfig = JSON.parse(input.advancedConfig || "{}") as Record<string, unknown>;
    } catch {
      toast.error("Advanced provider config must be valid JSON");
      return false;
    }
    const config = {
      ...advancedConfig,
      ...input.config,
      ...(entry.sizeOptions.length ? { size: input.size } : {}),
    };
    const { data } = await provision({
      variables: {
        input: {
          projectId,
          clusterId: effectiveClusterId,
          environmentName: "production",
          kind: entry.kind,
          variant: entry.variant,
          name: input.name || entry.kind,
          config,
          agentEnvironmentSpecSlugs: input.agentSlugs,
          appEnvironmentIds: input.appEnvironmentIds,
        },
      },
    });
    const result = data?.provisionProjectManagedService as MutationResult<unknown> | undefined;
    if (!result?.ok) {
      toast.error(firstError(result));
      return false;
    }
    toast.success("Project resource provisioning started");
    void refetchResources();
    return true;
  }

  /** Resolves true when the bundle was created (close and reset the form). */
  async function onCreateBundle(name: string, bundleSlug: string): Promise<boolean> {
    const { data } = await createBundle({
      variables: {
        input: { projectId, clusterId: effectiveBundleClusterId, name, slug: bundleSlug },
      },
    });
    const result = data?.createProjectSecretBundle as MutationResult<unknown> | undefined;
    if (!result?.ok) {
      toast.error(firstError(result));
      return false;
    }
    toast.success("Shared secret bundle created");
    void refetchResources();
    return true;
  }

  /** Resolves true when the key was saved (clear the inputs). */
  async function onSetBundleKey(bundleId: string, key: string, value: string): Promise<boolean> {
    if (!key || !value) return false;
    const { data } = await setSecret({
      variables: { input: { bundleId, key, value } },
    });
    const result = data?.setProjectBundleSecretValue as MutationResult<unknown> | undefined;
    if (!result?.ok) {
      toast.error(firstError(result));
      return false;
    }
    toast.success(`${key} saved`);
    void refetchResources();
    return true;
  }

  async function onCostPreview(serviceId: string) {
    const result = await loadCostPreview({
      variables: { managedServiceId: serviceId },
    });
    const preview = result.data?.astroliftManagedServiceCostPreview;
    if (preview) setCostPreviews((current) => ({ ...current, [serviceId]: preview }));
  }

  async function onReprovision(serviceId: string) {
    const { data } = await reprovision({
      variables: { input: { managedServiceId: serviceId } },
    });
    const result = data?.reprovisionProjectManagedService as MutationResult<unknown> | undefined;
    if (result?.ok) toast.success("Reprovision started");
    else toast.error(firstError(result));
    await refetchResources();
  }

  async function onDeprovision(service: AstroliftManagedService) {
    const approved = await confirm({
      title: `Deprovision ${service.name}?`,
      description:
        "The cloud resource will be removed using the provider's safe-delete behavior; persistent data is retained by default.",
      confirmLabel: "Deprovision",
    });
    if (!approved) return;
    const { data } = await deprovision({
      variables: {
        input: { id: service.id, deleteData: false, forceDestroy: false },
      },
    });
    const result = data?.deprovisionProjectManagedService as MutationResult<unknown> | undefined;
    if (result?.ok) toast.success("Deprovision started");
    else toast.error(firstError(result));
    await refetchResources();
  }

  async function onDeleteBundle(bundle: AstroliftProjectSecretBundle) {
    const approved = await confirm({
      title: `Delete ${bundle.name}?`,
      description:
        "This permanently deletes the provider-side bundle values after Astrolift verifies it has no consumers.",
      confirmLabel: "Delete bundle",
    });
    if (!approved) return;
    const { data } = await deleteBundle({
      variables: { bundleId: bundle.id },
    });
    const result = data?.deleteProjectSecretBundle as MutationResult<unknown> | undefined;
    if (result?.ok) toast.success("Bundle deleted");
    else toast.error(firstError(result));
    await refetchResources();
  }

  async function onToggleReveal(bundleId: string, key: string) {
    const revealKey = `${bundleId}:${key}`;
    if (revealed[revealKey]) {
      setRevealed((current) => {
        const next = { ...current };
        delete next[revealKey];
        return next;
      });
      return;
    }
    const { data } = await revealSecret({
      variables: { input: { bundleId, key } },
    });
    const result = data?.revealProjectBundleSecretValue as
      | MutationResult<{ value: string }>
      | undefined;
    if (!result?.ok || !result.data) {
      toast.error(firstError(result));
      return;
    }
    setRevealed((current) => ({
      ...current,
      [revealKey]: result.data!.value,
    }));
  }

  async function onDeleteBundleKey(bundleId: string, key: string) {
    const { data } = await deleteSecret({
      variables: { input: { bundleId, key } },
    });
    const result = data?.deleteProjectBundleSecretValue as MutationResult<unknown> | undefined;
    if (result?.ok) toast.success(`${key} deleted`);
    else toast.error(firstError(result));
    await refetchResources();
  }

  async function onDetachServiceConsumer(serviceId: string, attachmentId: string) {
    const { data } = await detachConsumer({
      variables: { input: { attachmentId } },
    });
    const result = data?.detachProjectManagedService as MutationResult<unknown> | undefined;
    if (!result?.ok) {
      toast.error(firstError(result));
      return;
    }
    toast.success("Consumer detached");
    await refreshConsumerService(serviceId);
  }

  async function onAttachServiceConsumer(
    serviceId: string,
    target: { agentEnvironmentSpecSlug: string | null; appEnvironmentId: string | null }
  ) {
    const { data } = await attachConsumer({
      variables: { input: { managedServiceId: serviceId, ...target } },
    });
    const result = data?.attachProjectManagedService as MutationResult<unknown> | undefined;
    if (!result?.ok) {
      toast.error(firstError(result));
      return;
    }
    await refreshConsumerService(serviceId);
  }

  async function onDetachBundleConsumer(bundleId: string, consumer: BundleConsumer) {
    let result: MutationResult<unknown> | undefined;
    if (consumer.consumerKind === "agent") {
      const { data } = await detachAgentBundle({
        variables: {
          slug: consumer.consumerSlug,
          attachmentId: consumer.id,
        },
      });
      result = data?.detachAgentSecretBundle;
    } else {
      const { data } = await detachAppBundle({
        variables: { input: { attachmentId: consumer.id } },
      });
      result = data?.detachSecretBundle;
    }
    if (!result?.ok) {
      toast.error(firstError(result));
      return;
    }
    toast.success("Bundle consumer detached");
    await refreshConsumerBundle(bundleId);
  }

  async function onAttachBundleToAgent(bundle: AstroliftProjectSecretBundle, agentSlug: string) {
    const { data } = await attachAgentBundle({
      variables: {
        slug: agentSlug,
        bundleId: bundle.id,
        prefix: "",
        position: bundle.consumers.length,
      },
    });
    const result = data?.attachAgentSecretBundle as MutationResult<unknown> | undefined;
    if (!result?.ok) {
      toast.error(firstError(result));
      return;
    }
    toast.success("Bundle attached to agent");
    await refreshConsumerBundle(bundle.id);
  }

  async function onAttachBundleToApp(
    bundle: AstroliftProjectSecretBundle,
    env: ProjectAppEnvironment
  ) {
    const { data } = await attachAppBundle({
      variables: {
        input: {
          appSlug: env.registeredAppSlug,
          environmentName: env.name,
          bundleSlug: bundle.slug,
          prefix: "",
        },
      },
    });
    const result = data?.attachSecretBundle as MutationResult<unknown> | undefined;
    if (!result?.ok) {
      toast.error(firstError(result));
      return;
    }
    toast.success("Bundle attached to app environment");
    await refreshConsumerBundle(bundle.id);
  }

  return {
    slug,
    project: (project ?? null) as ProjectResourcesProject | null,
    loading: projects.loading && !project,
    canUpdate,
    canWriteSecrets,
    canReadSecrets,
    clusters,
    services,
    bundles,
    resourcesLoading: resources.loading && !resources.data,
    agents: projectAgents,
    projectAppEnvironments,
    effectiveClusterId,
    effectiveClusterSlug,
    onClusterChange: setClusterId,
    effectiveBundleClusterId,
    onBundleClusterChange: setBundleClusterId,
    catalogEntries: catalog.data?.astroliftProjectManagedServiceCatalog ?? [],
    catalogLoading: catalog.loading,
    catalogError: Boolean(catalog.error),
    costPreviews,
    costPreviewLoading: costPreviewRequest.loading,
    onCostPreview,
    revealed,
    onToggleReveal,
    consumerService,
    setConsumerService,
    consumerBundle,
    setConsumerBundle,
    provisioning: provisionState.loading,
    onProvision,
    onReprovision,
    onDeprovision,
    attachingConsumer: attachConsumerState.loading,
    detachingConsumer: detachConsumerState.loading,
    onAttachServiceConsumer,
    onDetachServiceConsumer,
    creatingBundle: createBundleState.loading,
    onCreateBundle,
    onSetBundleKey,
    onDeleteBundleKey,
    onDeleteBundle,
    attachingAgentBundle: attachAgentBundleState.loading,
    attachingAppBundle: attachAppBundleState.loading,
    detachingBundleConsumer: detachAgentBundleState.loading || detachAppBundleState.loading,
    onAttachBundleToAgent,
    onAttachBundleToApp,
    onDetachBundleConsumer,
  };
}
