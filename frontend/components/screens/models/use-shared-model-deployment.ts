"use client";
import { useMemo, useLayoutEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useLocalListState, type ListDefinition } from "@/components/list/use-list-state";
import {
  LIST_MODEL_PLACEMENT_CLUSTERS,
  GET_CLUSTER_MODEL_RUNTIME_ADMISSION,
} from "@/graphql/models/shared-models.queries";
import { PROVISION_CLUSTER_MODEL } from "@/graphql/models/shared-models.mutations";
import type {
  ListModelPlacementClustersQuery,
  ListModelPlacementClustersQueryVariables,
  GetClusterModelRuntimeAdmissionQuery,
  GetClusterModelRuntimeAdmissionQueryVariables,
  ProvisionClusterModelMutation,
  ProvisionClusterModelMutationVariables,
} from "@/graphql/__generated__/operations";
import {
  sharedModelDraftSchema,
  sharedModelRequest,
  type SharedModelDraft,
  type SharedModelSource,
} from "./shared-model-form";
import { useHfCatalogue } from "./use-hf-catalogue";
import { useModelHostingSource } from "./use-model-hosting-source";
import { provisionModelResult } from "./shared-model-write-results";
import type { SharedModelDeploymentScreenProps } from "./SharedModelDeploymentScreen";
const initialDraft: SharedModelDraft = {
  name: "",
  computeMode: "",
  cpuRequest: "2",
  memoryRequest: "8Gi",
  gpuCount: "",
  cpuKvCacheGiB: "2",
  allowSubscriptions: false,
};
export function useSharedModelDeployment() {
  const t = useTranslations("models.shared.placement");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const organizationId = org?.id ?? "";
  const [model, setModel] = useState<SharedModelSource | null>(null),
    [selectedClusterId, setSelectedClusterId] = useState<string | null>(null);
  const form = useForm<SharedModelDraft>({
    resolver: zodResolver(sharedModelDraftSchema),
    defaultValues: initialDraft,
  });
  const suggestedName = useRef("");
  const presetRepo = useRef<string | null>(null);
  const selectModel = (source: SharedModelSource) => {
    const nextName =
      ("repoId" in source ? source.repoId.split("/").at(-1) : source.name)?.trim().slice(0, 128) ??
      "";
    const currentName = form.getValues("name");
    if (!currentName.trim() || currentName === suggestedName.current)
      form.setValue("name", nextName);
    suggestedName.current = nextName;
    if ("repoId" in source && source.repoId === presetRepo.current) {
      form.setValue("cpuRequest", "1");
      form.setValue("memoryRequest", "4Gi");
      form.setValue("cpuKvCacheGiB", "1");
    }
    presetRepo.current = null;
    setModel(source);
  };
  const watched = useWatch({ control: form.control });
  const draft: SharedModelDraft = { ...initialDraft, ...watched };
  const [sourceKind, setSourceKind] = useState<"huggingface" | "local">("huggingface");
  const hfModel = model && "repoId" in model ? model : null;
  const hosting = useModelHostingSource(hfModel, selectModel);
  const access =
    model && "localArtifactId" in model
      ? { confirmed: true, loading: false, reason: null, license: null, onRetry: () => {} }
      : hosting.access;
  const connection = sourceKind === "huggingface" ? hosting.connection : null;
  const catalogueProps = useHfCatalogue(selectModel, hosting.props.allowed);
  const licenseKey = JSON.stringify([
    organizationId,
    hosting.actorId,
    model,
    connection,
    selectedClusterId,
    access.license,
  ]);
  const [licenseReview, setLicenseReview] = useState({ key: licenseKey, reviewed: false });
  if (licenseReview.key !== licenseKey) setLicenseReview({ key: licenseKey, reviewed: false });
  const licenseReviewed = licenseReview.key === licenseKey && licenseReview.reviewed;
  const definition = useMemo<ListDefinition>(
    () => ({
      id: "models.shared.placement",
      fields: [],
      searchPlaceholder: t("clusterSearch"),
      defaultSort: [],
      views: [{ key: "all", label: t("cluster"), filters: {} }],
      paging: "numbered",
      pageSizes: [10, 25, 50],
      defaultPageSize: 10,
    }),
    [t]
  );
  const list = useLocalListState(definition);
  const skipped =
    !organizationId ||
    orgLoading ||
    Boolean(orgError) ||
    !model ||
    !hosting.identityReady ||
    hosting.props.allowed !== true;
  const clusters = useQuery<
    ListModelPlacementClustersQuery,
    ListModelPlacementClustersQueryVariables
  >(LIST_MODEL_PLACEMENT_CLUSTERS, {
    variables: {
      organizationId,
      search: list.state.q || null,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    skip: skipped,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const rows = skipped ? [] : (clusters.data?.clusterModelPlacementClustersPage.items ?? []);
  const cluster = rows.find((row) => row.id === selectedClusterId);
  const request = sharedModelRequest(organizationId, cluster ?? null, model, draft, connection),
    requestKey = JSON.stringify(request);
  const admission = useQuery<
    GetClusterModelRuntimeAdmissionQuery,
    GetClusterModelRuntimeAdmissionQueryVariables
  >(GET_CLUSTER_MODEL_RUNTIME_ADMISSION, {
    variables: {
      input: request ?? {
        organizationId: "",
        connectionId: null,
        expectedConnectionVersion: null,
        localArtifactId: null,
        expectedArtifactVersion: null,
        clusterId: "",
        expectedProviderId: "",
        name: "",
        modelRepo: "",
        revisionSha: "",
        computeMode: "",
        cpuRequest: "",
        memoryRequest: "",
        gpuCount: 0,
        cpuKvCacheGiB: null,
        allowSubscriptions: false,
      },
    },
    skip: skipped || !request,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const [provision] = useMutation<
    ProvisionClusterModelMutation,
    ProvisionClusterModelMutationVariables
  >(PROVISION_CLUSTER_MODEL, { fetchPolicy: "no-cache" });
  const contextKey = JSON.stringify([
    requestKey,
    organizationId,
    skipped,
    access.confirmed,
    licenseReviewed,
  ]);
  const [context, setContext] = useState({ key: contextKey, revision: 0 });
  if (context.key !== contextKey) setContext({ key: contextKey, revision: context.revision + 1 });
  const latest = useRef({ requestKey, organizationId, skipped, revision: context.revision }),
    mounted = useRef(true),
    lifecycle = useRef(0);
  useLayoutEffect(() => {
    latest.current = { requestKey, organizationId, skipped, revision: context.revision };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      lifecycle.current += 1;
    };
  }, []);
  const props: Omit<SharedModelDeploymentScreenProps, "catalogue"> = {
    organizationId,
    sourceControls: null,
    sourceConnection: connection,
    sourceAccess: access,
    hostingAllowed: hosting.props.allowed === true,
    licenseReviewed,
    onLicenseReviewed: (reviewed) => setLicenseReview({ key: licenseKey, reviewed }),
    model,
    onClearModel: () => setModel(null),
    selectedClusterId,
    onSelectCluster: setSelectedClusterId,
    draft,
    onDraftChange: (field, value) => {
      if (field === "allowSubscriptions" && typeof value === "boolean")
        form.setValue("allowSubscriptions", value);
      else if (field === "computeMode" && (value === "" || value === "cpu" || value === "gpu")) {
        form.setValue("computeMode", value);
        form.setValue("gpuCount", value === "cpu" ? "0" : "");
        if (value === "cpu" && !form.getValues("cpuKvCacheGiB"))
          form.setValue("cpuKvCacheGiB", "2");
      } else if (
        field !== "computeMode" &&
        field !== "allowSubscriptions" &&
        typeof value === "string"
      )
        switch (field) {
          case "name":
            form.setValue("name", value);
            break;
          case "cpuRequest":
            form.setValue("cpuRequest", value);
            break;
          case "memoryRequest":
            form.setValue("memoryRequest", value);
            break;
          case "gpuCount":
            form.setValue("gpuCount", value);
            break;
          case "cpuKvCacheGiB":
            form.setValue("cpuKvCacheGiB", value);
            break;
        }
    },
    clusters: {
      list,
      rows: rows.map((row) => ({ ...row, active: true, reason: null })),
      loading: orgLoading || (!skipped && clusters.loading && !clusters.data),
      stale: !skipped && clusters.loading && Boolean(clusters.data),
      error: orgError
        ? { message: orgError.message }
        : !skipped && clusters.error
          ? { message: clusters.error.message }
          : null,
      totalCount: skipped
        ? null
        : (clusters.data?.clusterModelPlacementClustersPage.totalCount ?? null),
      nextCursor: null,
      onRetry: () => {
        if (!skipped) void clusters.refetch().catch(() => {});
      },
    },
    admission:
      request && admission.data?.clusterModelRuntimeAdmission
        ? {
            ...admission.data.clusterModelRuntimeAdmission,
            reason: admission.data.clusterModelRuntimeAdmission.reason ?? null,
            runtimeVersion: admission.data.clusterModelRuntimeAdmission.runtimeVersion ?? null,
            architecture: admission.data.clusterModelRuntimeAdmission.architecture ?? null,
            requestKey,
          }
        : null,
    admissionLoading: !!request && !skipped && admission.loading,
    admissionError: orgError?.message ?? (!skipped ? (admission.error?.message ?? null) : null),
    onRetryAdmission: () => {
      if (request && !skipped) void admission.refetch().catch(() => {});
    },
    onDeploy: async (candidate) => {
      const epoch = lifecycle.current;
      if (
        !mounted.current ||
        latest.current.skipped ||
        latest.current.revision !== context.revision ||
        latest.current.organizationId !== candidate.organizationId ||
        latest.current.requestKey !== JSON.stringify(candidate) ||
        !access.confirmed ||
        !licenseReviewed
      )
        return { accepted: false, message: t("changed") };
      try {
        const result = await provision({ variables: { input: candidate } });
        const outcome = provisionModelResult(
          result.data?.provisionClusterModel,
          candidate,
          t("failed")
        );
        if (outcome.accepted) return outcome;
        if (
          !mounted.current ||
          lifecycle.current !== epoch ||
          latest.current.revision !== context.revision ||
          latest.current.requestKey !== JSON.stringify(candidate)
        )
          return { accepted: false, message: t("changed") };
        return outcome;
      } catch (error) {
        return { accepted: false, message: error instanceof Error ? error.message : t("failed") };
      }
    },
  };
  return {
    ...props,
    catalogueProps: {
      ...catalogueProps,
      onSelect: (repoId: string) => {
        presetRepo.current = null;
        catalogueProps.onSelect(repoId);
      },
      onSelectSmallModel: () => {
        presetRepo.current = "Qwen/Qwen2.5-0.5B-Instruct";
        catalogueProps.onSelect(presetRepo.current);
      },
    },
    hostingProps: hosting.props,
    sourceKind,
    onUseLocalArtifact: (artifact: {
      id: string;
      version: number;
      name: string;
      manifestSha256: string;
    }) =>
      selectModel({
        localArtifactId: artifact.id,
        expectedArtifactVersion: artifact.version,
        name: artifact.name,
        manifestSha256: artifact.manifestSha256,
      }),
    onSourceKind: (kind: "huggingface" | "local") => {
      if (kind !== sourceKind) {
        setSourceKind(kind);
        setModel(null);
        setSelectedClusterId(null);
        form.reset(initialDraft);
        suggestedName.current = "";
        presetRepo.current = null;
      }
    },
  };
}
