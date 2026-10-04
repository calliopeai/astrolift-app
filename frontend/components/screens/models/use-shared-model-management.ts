"use client";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { GET_MODEL_HOSTING_ACTION } from "@/graphql/models/hosting.queries";
import { useLocalListState } from "@/components/list/use-list-state";
import type { ListDefinition } from "@/components/list/list-state";
import { modelAccessDraft, modelSettingsRequest } from "./shared-model-settings";
import {
  GET_CLUSTER_MODEL_UPDATE_ADMISSION,
  LIST_MODEL_DEDICATED_APPS,
} from "@/graphql/models/shared-models.queries";
import {
  UPDATE_CLUSTER_MODEL,
  DEPROVISION_CLUSTER_MODEL,
} from "@/graphql/models/shared-models.mutations";
import type {
  ClusterModelFieldsFragment,
  GetClusterModelUpdateAdmissionQuery,
  GetClusterModelUpdateAdmissionQueryVariables,
  GetModelHostingActionQuery,
  GetModelHostingActionQueryVariables,
  ListModelDedicatedAppsQuery,
  ListModelDedicatedAppsQueryVariables,
  UpdateClusterModelMutation,
  UpdateClusterModelMutationVariables,
  DeprovisionClusterModelMutation,
  DeprovisionClusterModelMutationVariables,
  UpdateClusterModelInput,
  DeprovisionClusterModelInput,
} from "@/graphql/__generated__/operations";
import { sharedModelDraftSchema, type SharedModelDraft } from "./shared-model-form";
import type {
  SharedModelManagementPanelProps,
  ManagementResult,
} from "./SharedModelManagementPanel";
import { managementModelResult } from "./shared-model-write-results";
function draftFor(model: ClusterModelFieldsFragment): SharedModelDraft {
  return {
    name: model.name,
    computeMode:
      model.computeMode === "cpu" || model.computeMode === "gpu" ? model.computeMode : "",
    cpuRequest: model.desiredResources.cpuRequest ?? "",
    memoryRequest: model.desiredResources.memoryRequest ?? "",
    gpuCount:
      model.desiredResources.gpuCount == null ? "" : String(model.desiredResources.gpuCount),
    cpuKvCacheGiB:
      model.desiredResources.cpuKvCacheGiB == null
        ? ""
        : String(model.desiredResources.cpuKvCacheGiB),
    allowSubscriptions: model.subscriptionsEnabled,
  };
}
export function useSharedModelManagement(
  model: ClusterModelFieldsFragment,
  blocked: boolean,
  onRefresh: () => void
): SharedModelManagementPanelProps {
  const t = useTranslations("models.shared.management"),
    { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const inventory = useTranslations("models.shared.inventory");
  const { user, loading: actorLoading, error: actorError } = useMe();
  const skipped =
    orgLoading ||
    !!orgError ||
    actorLoading ||
    !!actorError ||
    !user?.id ||
    !org?.id ||
    org.id !== model.organizationId;
  const capabilities = useQuery<GetModelHostingActionQuery, GetModelHostingActionQueryVariables>(
    GET_MODEL_HOSTING_ACTION,
    {
      variables: { organizationId: model.organizationId },
      skip: skipped,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const canManage = capabilities.data?.modelHostingAction?.allowed === true;
  const [accessState, setAccessState] = useState({
    version: model.version,
    access: modelAccessDraft(model),
  });
  if (accessState.version !== model.version)
    setAccessState({ version: model.version, access: modelAccessDraft(model) });
  const access =
    accessState.version === model.version ? accessState.access : modelAccessDraft(model);
  const definition = useMemo<ListDefinition>(
    () => ({
      id: "model-dedicated-apps",
      searchPlaceholder: inventory("appSearch"),
      fields: [],
      views: [{ key: "all", label: inventory("selectApp"), filters: {} }],
      defaultSort: [],
      paging: "numbered",
      pageSizes: [10, 25, 50],
      defaultPageSize: 25,
    }),
    [inventory]
  );
  const appsList = useLocalListState(definition);
  const apps = useQuery<ListModelDedicatedAppsQuery, ListModelDedicatedAppsQueryVariables>(
    LIST_MODEL_DEDICATED_APPS,
    {
      variables: {
        organizationId: model.organizationId,
        clusterId: model.clusterId,
        expectedProviderId: model.providerId,
        search: appsList.state.q,
        page: appsList.state.page,
        pageSize: appsList.state.pageSize,
      },
      skip: skipped || !canManage || blocked || access.mode !== "DEDICATED",
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const form = useForm<SharedModelDraft>({
    resolver: zodResolver(sharedModelDraftSchema),
    defaultValues: draftFor(model),
  });
  const values = useWatch({ control: form.control }),
    draft: SharedModelDraft = { ...draftFor(model), ...values };
  const formVersion = useRef(model.version);
  useLayoutEffect(() => {
    if (formVersion.current !== model.version) {
      formVersion.current = model.version;
      form.reset(draftFor(model));
    }
  }, [model, form]);
  const request = modelSettingsRequest(model, draft, access),
    requestKey = JSON.stringify(request);
  const admission = useQuery<
    GetClusterModelUpdateAdmissionQuery,
    GetClusterModelUpdateAdmissionQueryVariables
  >(GET_CLUSTER_MODEL_UPDATE_ADMISSION, {
    variables: {
      input: request ?? {
        organizationId: "",
        id: "",
        expectedClusterId: "",
        expectedProviderId: "",
        ifMatchVersion: 0,
        allowSubscriptions: false,
        dtype: null,
        maxModelLen: null,
        maxNumSeqs: null,
        cpuRequest: "",
        memoryRequest: "",
        gpuCount: 0,
        cpuKvCacheGiB: null,
        name: null,
        sharingMode: null,
        dedicatedAppId: null,
        ifMatchDedicatedAppVersion: null,
      },
    },
    skip: skipped || !canManage || !request || blocked,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const scopeKey = JSON.stringify([
    org?.id,
    user?.id,
    model.organizationId,
    model.id,
    model.version,
    model.clusterId,
    model.providerId,
    model.status,
    skipped,
    blocked,
    canManage,
    capabilities.loading,
    capabilities.error?.message,
  ]);
  const updateScopeKey = JSON.stringify([
    scopeKey,
    requestKey,
    admission.loading,
    admission.error?.message,
    admission.data,
  ]);
  const [scope, setScope] = useState({
    key: scopeKey,
    revision: 0,
    updateKey: updateScopeKey,
    updateRevision: 0,
  });
  if (scope.key !== scopeKey || scope.updateKey !== updateScopeKey)
    setScope({
      key: scopeKey,
      revision: scope.revision + Number(scope.key !== scopeKey),
      updateKey: updateScopeKey,
      updateRevision: scope.updateRevision + Number(scope.updateKey !== updateScopeKey),
    });
  const latest = useRef({ ...scope }),
    mounted = useRef(true),
    lifecycle = useRef(0),
    busy = useRef(false);
  useLayoutEffect(() => {
    latest.current = { ...scope };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      lifecycle.current += 1;
    };
  }, []);
  const [update] = useMutation<UpdateClusterModelMutation, UpdateClusterModelMutationVariables>(
      UPDATE_CLUSTER_MODEL,
      { fetchPolicy: "no-cache" }
    ),
    [deprovision] = useMutation<
      DeprovisionClusterModelMutation,
      DeprovisionClusterModelMutationVariables
    >(DEPROVISION_CLUSTER_MODEL, { fetchPolicy: "no-cache" });
  async function write(
    input: UpdateClusterModelInput | DeprovisionClusterModelInput
  ): Promise<ManagementResult> {
    const epoch = lifecycle.current,
      isUpdate = "cpuRequest" in input,
      revision = isUpdate ? scope.updateRevision : scope.revision;
    const current = () =>
      mounted.current &&
      lifecycle.current === epoch &&
      (isUpdate ? latest.current.updateKey : latest.current.key) ===
        (isUpdate ? updateScopeKey : scopeKey) &&
      (isUpdate ? latest.current.updateRevision : latest.current.revision) === revision;
    if (
      !current() ||
      busy.current ||
      skipped ||
      blocked ||
      capabilities.loading ||
      capabilities.error ||
      !canManage ||
      !["active", "failed"].includes(model.status) ||
      input.organizationId !== model.organizationId ||
      input.id !== model.id ||
      input.expectedClusterId !== model.clusterId ||
      input.expectedProviderId !== model.providerId ||
      input.ifMatchVersion !== model.version
    )
      return { accepted: false, message: t("changed") };
    if (
      isUpdate
        ? !request ||
          admission.loading ||
          admission.error ||
          admission.data?.clusterModelUpdateAdmission?.eligible !== true ||
          JSON.stringify(input) !== requestKey
        : input.deleteData !== false
    )
      return { accepted: false, message: t("changed") };
    busy.current = true;
    try {
      const envelope = isUpdate
        ? (await update({ variables: { input } })).data?.updateClusterModel
        : (await deprovision({ variables: { input } })).data?.deprovisionClusterModel;
      if (!current()) return { accepted: false, message: t("changed") };
      return managementModelResult(envelope, model, input, t("failed"));
    } catch (error) {
      return {
        accepted: false,
        message: current() && error instanceof Error ? error.message : t("changed"),
      };
    } finally {
      busy.current = false;
    }
  }
  return {
    model,
    blocked: blocked || skipped,
    canManage,
    capabilityLoading: orgLoading || actorLoading || capabilities.loading,
    capabilityError:
      orgError?.message ??
      actorError?.message ??
      capabilities.error?.message ??
      (!canManage ? capabilities.data?.modelHostingAction?.reason : null) ??
      (skipped ? t("readOnly") : null),
    onRetryCapabilities: () => {
      if (!skipped) void capabilities.refetch().catch(() => {});
    },
    draft,
    access,
    onAccessChange: (mode) =>
      setAccessState({
        version: model.version,
        access: { mode, app: mode === "SHARED" ? null : access.app },
      }),
    onSelectDedicatedApp: (app) =>
      setAccessState({ version: model.version, access: { mode: "DEDICATED", app } }),
    dedicatedApps: {
      list: appsList,
      rows: apps.data?.clusterModelDedicatedAppsPage.items ?? [],
      totalCount: apps.data?.clusterModelDedicatedAppsPage.totalCount ?? null,
      nextCursor: null,
      loading: apps.loading && !apps.data,
      stale: apps.loading && !!apps.data,
      error: apps.error ? new Error(apps.error.message) : null,
      onRetry: () => {
        if (!skipped && canManage && !blocked && access.mode === "DEDICATED")
          void apps.refetch().catch(() => {});
      },
    },
    onDraftChange: (field, value) => {
      if (field === "allowSubscriptions" && typeof value === "boolean")
        form.setValue("allowSubscriptions", value);
      else if (typeof value === "string")
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
    admission:
      request && admission.data?.clusterModelUpdateAdmission
        ? {
            ...admission.data.clusterModelUpdateAdmission,
            eligible: admission.data.clusterModelUpdateAdmission.eligible === true,
            hardwareAdmission:
              admission.data.clusterModelUpdateAdmission.hardwareAdmission ?? "unknown",
            reason: admission.data.clusterModelUpdateAdmission.reason ?? null,
            runtimeVersion: admission.data.clusterModelUpdateAdmission.runtimeVersion ?? null,
            architecture: admission.data.clusterModelUpdateAdmission.architecture ?? null,
            requestKey,
          }
        : null,
    admissionLoading: admission.loading && !!request && canManage && !skipped,
    admissionError: admission.error?.message ?? null,
    onRetryAdmission: () => {
      if (!skipped && canManage && request) void admission.refetch().catch(() => {});
    },
    onUpdate: write,
    onDelete: write,
    onAccepted: onRefresh,
  };
}
