"use client";
import { useLayoutEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { GET_ME } from "@/graphql/user/user.queries";
import type { MeQueryData, MeQueryVariables } from "@/graphql/user/user.types";
import { GET_CLUSTER_MODEL_RUNTIME_ADMISSION } from "@/graphql/models/shared-models.queries";
import {
  UPDATE_CLUSTER_MODEL,
  DEPROVISION_CLUSTER_MODEL,
} from "@/graphql/models/shared-models.mutations";
import type {
  ClusterModelFieldsFragment,
  GetClusterModelRuntimeAdmissionQuery,
  GetClusterModelRuntimeAdmissionQueryVariables,
  UpdateClusterModelMutation,
  UpdateClusterModelMutationVariables,
  DeprovisionClusterModelMutation,
  DeprovisionClusterModelMutationVariables,
  UpdateClusterModelInput,
  DeprovisionClusterModelInput,
} from "@/graphql/__generated__/operations";
import {
  sharedModelDraftSchema,
  sharedModelRequest,
  type SharedModelDraft,
} from "./shared-model-form";
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
  const skipped = orgLoading || !!orgError || !org?.id || org.id !== model.organizationId;
  // This implicit-tenant advisory manifest is read freshly within the keyed model context.
  const capabilities = useQuery<MeQueryData, MeQueryVariables>(GET_ME, {
    skip: skipped,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const canManage =
    capabilities.data?.me?.modules.some(
      (row) => row.key === "models" && row.enabled && row.canManage
    ) === true;
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
  const request = sharedModelRequest(
      model.organizationId,
      { id: model.clusterId, providerId: model.providerId },
      model.revisionSha ? { repoId: model.modelRepo, revisionSha: model.revisionSha } : null,
      draft
    ),
    requestKey = JSON.stringify(request);
  const admission = useQuery<
    GetClusterModelRuntimeAdmissionQuery,
    GetClusterModelRuntimeAdmissionQueryVariables
  >(GET_CLUSTER_MODEL_RUNTIME_ADMISSION, {
    variables: {
      input: request ?? {
        organizationId: "",
        clusterId: "",
        expectedProviderId: "",
        name: "",
        modelRepo: "",
        revisionSha: "",
        computeMode: "",
        cpuRequest: "",
        memoryRequest: "",
        gpuCount: 0,
        allowSubscriptions: false,
        cpuKvCacheGiB: null,
      },
    },
    skip: skipped || !canManage || !request || blocked,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const scopeKey = JSON.stringify([
    org?.id,
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
    requestKey,
    admission.loading,
    admission.error?.message,
    admission.data,
  ]);
  const [scope, setScope] = useState({ key: scopeKey, revision: 0 });
  if (scope.key !== scopeKey) setScope({ key: scopeKey, revision: scope.revision + 1 });
  const latest = useRef({ key: scopeKey, revision: scope.revision }),
    mounted = useRef(true),
    lifecycle = useRef(0),
    busy = useRef(false);
  useLayoutEffect(() => {
    latest.current = { key: scopeKey, revision: scope.revision };
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
      revision = scope.revision,
      isUpdate = "cpuRequest" in input;
    const current = () =>
      mounted.current &&
      lifecycle.current === epoch &&
      latest.current.key === scopeKey &&
      latest.current.revision === revision;
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
          admission.data?.clusterModelRuntimeAdmission.eligible !== true ||
          input.cpuRequest !== request.cpuRequest ||
          input.memoryRequest !== request.memoryRequest ||
          input.gpuCount !== request.gpuCount ||
          (input.cpuKvCacheGiB ?? null) !== request.cpuKvCacheGiB ||
          input.allowSubscriptions !== request.allowSubscriptions
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
    capabilityLoading: orgLoading || capabilities.loading,
    capabilityError:
      orgError?.message ?? capabilities.error?.message ?? (skipped ? t("readOnly") : null),
    onRetryCapabilities: () => {
      if (!skipped) void capabilities.refetch().catch(() => {});
    },
    draft,
    onDraftChange: (field, value) => {
      if (field === "allowSubscriptions" && typeof value === "boolean")
        form.setValue("allowSubscriptions", value);
      else if (typeof value === "string")
        switch (field) {
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
      request && admission.data
        ? {
            ...admission.data.clusterModelRuntimeAdmission,
            reason: admission.data.clusterModelRuntimeAdmission.reason ?? null,
            runtimeVersion: admission.data.clusterModelRuntimeAdmission.runtimeVersion ?? null,
            architecture: admission.data.clusterModelRuntimeAdmission.architecture ?? null,
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
