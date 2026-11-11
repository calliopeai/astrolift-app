"use client";

import { useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { mayExecute, type NvkeOperationScreenProps } from "@/components/screens/nvke/types";
import type {
  CancelNvkeOperationMutation,
  CancelNvkeOperationMutationVariables,
  GetNvkeContractQuery,
  GetNvkeOperationQuery,
  GetNvkeOperationResultTextQuery,
  GetNvkeSiteQuery,
  ResumeNvkeOperationMutation,
  ResumeNvkeOperationMutationVariables,
} from "@/graphql/__generated__/operations";
import { nativeMaintenancePermission } from "@/lib/nvke/contract";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { useNvkeContext, useNvkeEpoch } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import {
  GET_NVKE_CONTRACT,
  GET_NVKE_OPERATION,
  GET_NVKE_OPERATION_RESULT_TEXT,
  GET_NVKE_SITE,
} from "./nvke.queries";
import { useNvkeDiagnostics } from "./use-nvke-diagnostics";
import { useNvkeWorkflows } from "./use-nvke-workflows";
import { useNvkeMaintenance } from "./use-nvke-maintenance";
import { useNvkeProviders } from "./use-nvke-providers";
import { useNvkeCredentials } from "./use-nvke-credentials";
import { useNvkeCredentialRequest } from "./use-nvke-credential-request";
import { useNvkeInstallPlan } from "./use-nvke-install-plan";
import { useNvkeInstallPlanContext } from "./use-nvke-install-plan-context";
import { useNvkeInstallPlanLocalImage } from "./use-nvke-install-plan-local-image";
import { useNvkeInstallPlanImmutable } from "./use-nvke-install-plan-immutable";
import { useNvkeInstallPlanNodePlatforms } from "./use-nvke-install-plan-node-platforms";
import { useNvkeInstallPlanImageBinding } from "./use-nvke-install-plan-image-binding";
import { useNvkeInstallPlanWorkload } from "./use-nvke-install-plan-workload";
import { useNvkeInstallPlanImage } from "./use-nvke-install-plan-image";
import { useNvkeInstallPlanVerification } from "./use-nvke-install-plan-verification";
import { useNvkeInstallPlanTree } from "./use-nvke-install-plan-tree";
import { useNvkeInstallPlanCharts } from "./use-nvke-install-plan-charts";
import { useNvkeOperatorInstallReview } from "./use-nvke-operator-install-review";
import { useNvkeOperatorJob } from "./use-nvke-operator-job";
import { useNvkeWorkloadAccess } from "./use-nvke-workload-access";
import { CANCEL_NVKE_OPERATION, RESUME_NVKE_OPERATION } from "./nvke.mutations";

export const useNvkeOperation = (
  siteId: string,
  operationId: string
): NvkeOperationScreenProps & { scopeKey: string } => {
  const context = useNvkeContext(),
    client = useApolloClient(),
    t = useTranslations("nvke");
  const key =
    context.scopeKey && context.access.read && validNvkeGuid(siteId) && validNvkeGuid(operationId)
      ? JSON.stringify([
          context.scopeKey,
          siteId,
          operationId,
          context.access.read,
          context.access.manage,
          context.access.admin,
          context.access.loading,
        ])
      : "";
  const { isCurrent: current, key: visitKey } = useNvkeEpoch(key);
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  const detail = useNvkeRead<GetNvkeOperationQuery>(
    GET_NVKE_OPERATION,
    { operationId },
    key,
    0,
    context.scope?.organizationId
  );
  const site = useNvkeRead<GetNvkeSiteQuery>(
    GET_NVKE_SITE,
    { siteId },
    key,
    0,
    context.scope?.organizationId
  );
  const catalog = useNvkeRead<GetNvkeContractQuery>(
    GET_NVKE_CONTRACT,
    {},
    key,
    0,
    context.scope?.organizationId
  );
  const row = detail.data?.astroliftNvkeOperation;
  const operation =
    row?.id === operationId && row.siteId === siteId && site.data?.astroliftNvkeSite?.id === siteId
      ? row
      : null;
  const action = catalog.data?.astroliftNvkeContract.actions.find(
    (entry) => entry.key === operation?.operationKey
  );
  const loading = context.loading || detail.loading || site.loading || catalog.loading;
  const failed =
    context.failed || detail.failed || site.failed || catalog.failed || (!context.loading && !key);
  const isTooling = operation?.operationKey.startsWith("tooling.") === true;
  const permission =
    isTooling ||
    ["metrics.credentials", "infrastructure.providers"].includes(operation?.operationKey ?? "")
      ? "nvke.admin"
      : (nativeMaintenancePermission(operation?.operationKey) ?? action?.permission);
  const canAct =
    !!key &&
    !loading &&
    !failed &&
    !!operation &&
    !!permission &&
    mayExecute(context.access, permission);
  const diagnostics = useNvkeDiagnostics({
    context,
    scopeKey: visitKey,
    siteId,
    operation,
    enabled: canAct,
  });
  const maintenance = useNvkeMaintenance({
    context,
    scopeKey: visitKey,
    siteId,
    operation,
    enabled: canAct,
  });
  const providers = useNvkeProviders({
    context,
    scopeKey: visitKey,
    siteId,
    operation,
    enabled: !loading && !failed,
  });
  const credentials = useNvkeCredentials({
    context,
    scopeKey: visitKey,
    siteId,
    operation,
    enabled: !loading && !failed,
  });
  const workloadAccess = useNvkeWorkloadAccess({
    context,
    scopeKey: visitKey,
    site: site.data?.astroliftNvkeSite?.id === siteId ? site.data.astroliftNvkeSite : null,
    operation,
    enabled: canAct,
  });
  const [mutation, setMutation] = useState<{
    key: string;
    pending: boolean;
    error: string | null;
  } | null>(null);
  const busy = useRef(false);
  const refresh = () => {
    setMutation(null);
    detail.refresh();
    site.refresh();
    catalog.refresh();
  };
  const workflows = useNvkeWorkflows({
    context,
    scopeKey: visitKey,
    site: site.data?.astroliftNvkeSite?.id === siteId ? site.data.astroliftNvkeSite : null,
    operation,
    contract: catalog.data?.astroliftNvkeContract,
    maintenance,
    enabled: canAct,
    onRefresh: refresh,
  });
  const credentialRequest = useNvkeCredentialRequest({
    context,
    scopeKey: visitKey,
    site: site.data?.astroliftNvkeSite?.id === siteId ? site.data.astroliftNvkeSite : null,
    operation,
    maintenance,
    enabled: !loading && !failed,
    onRefresh: refresh,
  });
  const operatorJob = useNvkeOperatorJob({
    context,
    scopeKey: visitKey,
    site: site.data?.astroliftNvkeSite?.id === siteId ? site.data.astroliftNvkeSite : null,
    operation,
    enabled: !loading && !failed && !(mutation?.key === visitKey && mutation.pending),
    onRefresh: refresh,
  });
  const operatorInstallReview = useNvkeOperatorInstallReview({
    context,
    source: operatorJob,
    pending: Boolean((mutation?.key === visitKey && mutation.pending) || operatorJob?.pending),
    onRefresh: refresh,
  });
  const operatorInstallPlan = useNvkeInstallPlan({
    context,
    source: operatorJob,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending
    ),
  });
  const operatorInstallPlanCharts = useNvkeInstallPlanCharts({
    context,
    source: operatorInstallPlan,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending
    ),
  });
  const operatorInstallPlanTree = useNvkeInstallPlanTree({
    context,
    source: operatorInstallPlanCharts,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending ||
      operatorInstallPlan?.pending
    ),
  });
  const operatorInstallPlanContext = useNvkeInstallPlanContext({
    context,
    source: operatorInstallPlanTree,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending ||
      operatorInstallPlan?.pending ||
      operatorInstallPlanCharts?.pending
    ),
  });
  const operatorInstallPlanVerification = useNvkeInstallPlanVerification({
    context,
    source: operatorInstallPlanContext,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending ||
      operatorInstallPlan?.pending ||
      operatorInstallPlanCharts?.pending ||
      operatorInstallPlanTree?.pending
    ),
  });
  const operatorInstallPlanImage = useNvkeInstallPlanImage({
    context,
    source: operatorInstallPlanTree,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending ||
      operatorInstallPlan?.pending ||
      operatorInstallPlanCharts?.pending ||
      operatorInstallPlanTree?.pending ||
      operatorInstallPlanContext?.pending ||
      operatorInstallPlanVerification?.pending
    ),
  });
  const operatorInstallPlanLocalImage = useNvkeInstallPlanLocalImage({
    context,
    source: operatorInstallPlanTree,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending ||
      operatorInstallPlan?.pending ||
      operatorInstallPlanCharts?.pending ||
      operatorInstallPlanTree?.pending ||
      operatorInstallPlanContext?.pending ||
      operatorInstallPlanVerification?.pending ||
      operatorInstallPlanImage?.pending
    ),
  });
  const operatorInstallPlanWorkload = useNvkeInstallPlanWorkload({
    context,
    source: operatorInstallPlanTree,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending ||
      operatorInstallPlan?.pending ||
      operatorInstallPlanCharts?.pending ||
      operatorInstallPlanTree?.pending ||
      operatorInstallPlanContext?.pending ||
      operatorInstallPlanVerification?.pending ||
      operatorInstallPlanImage?.pending ||
      operatorInstallPlanLocalImage?.pending
    ),
  });
  const operatorInstallPlanWorkloadImage = useNvkeInstallPlanImage({
    context,
    source: operatorInstallPlanTree,
    workload: operatorInstallPlanWorkload?.artifacts ?? null,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending ||
      operatorInstallPlan?.pending ||
      operatorInstallPlanCharts?.pending ||
      operatorInstallPlanTree?.pending ||
      operatorInstallPlanContext?.pending ||
      operatorInstallPlanVerification?.pending ||
      operatorInstallPlanImage?.pending ||
      operatorInstallPlanLocalImage?.pending ||
      operatorInstallPlanWorkload?.pending
    ),
  });
  const operatorInstallPlanImageBinding = useNvkeInstallPlanImageBinding({
    context,
    source: operatorInstallPlanTree,
    workload: operatorInstallPlanWorkload,
    pending: Boolean(
      (mutation?.key === visitKey && mutation.pending) ||
      operatorJob?.pending ||
      operatorInstallReview?.pending ||
      operatorInstallPlan?.pending ||
      operatorInstallPlanCharts?.pending ||
      operatorInstallPlanTree?.pending ||
      operatorInstallPlanContext?.pending ||
      operatorInstallPlanVerification?.pending ||
      operatorInstallPlanImage?.pending ||
      operatorInstallPlanLocalImage?.pending ||
      operatorInstallPlanWorkload?.pending ||
      operatorInstallPlanWorkloadImage?.pending
    ),
  });
  const immutableParentPending = Boolean(
    (mutation?.key === visitKey && mutation.pending) ||
    operatorJob?.pending ||
    operatorInstallReview?.pending ||
    operatorInstallPlan?.pending ||
    operatorInstallPlanCharts?.pending ||
    operatorInstallPlanTree?.pending ||
    operatorInstallPlanContext?.pending ||
    operatorInstallPlanVerification?.pending ||
    operatorInstallPlanImage?.pending ||
    operatorInstallPlanLocalImage?.pending ||
    operatorInstallPlanWorkload?.pending ||
    operatorInstallPlanWorkloadImage?.pending ||
    operatorInstallPlanImageBinding?.pending
  );
  const operatorInstallPlanImagePayload = useNvkeInstallPlanImmutable({
    context,
    source: operatorInstallPlanTree,
    workload: operatorInstallPlanWorkload,
    imageBinding: operatorInstallPlanImageBinding,
    mode: "payload",
    pending: immutableParentPending,
  });
  const operatorInstallPlanImmutableExecution = useNvkeInstallPlanImmutable({
    context,
    source: operatorInstallPlanTree,
    workload: operatorInstallPlanWorkload,
    imageBinding: operatorInstallPlanImageBinding,
    mode: "execution",
    pending: immutableParentPending || Boolean(operatorInstallPlanImagePayload?.pending),
  });
  const operatorInstallPlanNodePlatforms = useNvkeInstallPlanNodePlatforms({
    context,
    source: operatorInstallPlanTree,
    workload: operatorInstallPlanWorkload,
    imageBinding: operatorInstallPlanImageBinding,
    pending:
      immutableParentPending ||
      Boolean(
        operatorInstallPlanImagePayload?.pending || operatorInstallPlanImmutableExecution?.pending
      ),
  });
  const control = async (resume: boolean) => {
    if (
      !current() ||
      !canAct ||
      !operation ||
      busy.current ||
      operatorJob?.pending ||
      operatorInstallReview?.pending ||
      operatorInstallPlan?.pending ||
      operatorInstallPlanCharts?.pending ||
      operatorInstallPlanTree?.pending ||
      operatorInstallPlanContext?.pending ||
      operatorInstallPlanVerification?.pending ||
      operatorInstallPlanImage?.pending ||
      operatorInstallPlanLocalImage?.pending ||
      operatorInstallPlanWorkload?.pending ||
      operatorInstallPlanWorkloadImage?.pending ||
      operatorInstallPlanImageBinding?.pending ||
      operatorInstallPlanImagePayload?.pending ||
      operatorInstallPlanImmutableExecution?.pending ||
      operatorInstallPlanNodePlatforms?.pending ||
      (resume && (!context.access.admin || isTooling))
    )
      return false;
    busy.current = true;
    setMutation({ key: visitKey, pending: true, error: null });
    try {
      const result = resume
        ? (
            await client.mutate<ResumeNvkeOperationMutation, ResumeNvkeOperationMutationVariables>({
              mutation: RESUME_NVKE_OPERATION,
              variables: {
                input: { operationId, expectedVersion: operation.version, timeoutSeconds: 3600 },
              },
              fetchPolicy: "no-cache",
              context: requestContext,
            })
          ).data?.resumeNvkeOperation
        : (
            await client.mutate<CancelNvkeOperationMutation, CancelNvkeOperationMutationVariables>({
              mutation: CANCEL_NVKE_OPERATION,
              variables: { input: { operationId } },
              fetchPolicy: "no-cache",
              context: requestContext,
            })
          ).data?.cancelNvkeOperation;
      if (!current()) return false;
      if (
        !result?.ok ||
        result.data?.id !== operationId ||
        result.data.siteId !== siteId ||
        result.data.operationKey !== operation.operationKey
      )
        throw new Error("REQUEST_FAILED");
      refresh();
      return true;
    } catch {
      if (current()) setMutation({ key: visitKey, pending: false, error: t("requestFailed") });
      return false;
    } finally {
      busy.current = false;
    }
  };
  return {
    scopeKey: visitKey,
    operation,
    operatorJob,
    operatorInstallReview,
    operatorInstallPlan,
    operatorInstallPlanCharts,
    operatorInstallPlanTree,
    operatorInstallPlanContext,
    operatorInstallPlanVerification,
    operatorInstallPlanImage,
    operatorInstallPlanLocalImage,
    operatorInstallPlanWorkload,
    operatorInstallPlanWorkloadImage,
    operatorInstallPlanImageBinding,
    operatorInstallPlanImagePayload,
    operatorInstallPlanImmutableExecution,
    operatorInstallPlanNodePlatforms,
    diagnostics,
    maintenance,
    credentials,
    providers,
    credentialRequest,
    workflows,
    workloadAccess,
    siteName: site.data?.astroliftNvkeSite?.name ?? "",
    loading,
    error: failed ? t("readFailed") : mutation?.key === visitKey ? mutation.error : null,
    pending:
      (mutation?.key === visitKey && mutation.pending) ||
      Boolean(
        operatorJob?.pending ||
        operatorInstallReview?.pending ||
        operatorInstallPlan?.pending ||
        operatorInstallPlanCharts?.pending ||
        operatorInstallPlanTree?.pending ||
        operatorInstallPlanContext?.pending ||
        operatorInstallPlanVerification?.pending ||
        operatorInstallPlanImage?.pending ||
        operatorInstallPlanLocalImage?.pending ||
        operatorInstallPlanWorkload?.pending ||
        operatorInstallPlanWorkloadImage?.pending ||
        operatorInstallPlanImageBinding?.pending ||
        operatorInstallPlanImagePayload?.pending ||
        operatorInstallPlanImmutableExecution?.pending ||
        operatorInstallPlanNodePlatforms?.pending
      ),
    canAct,
    canResume: canAct && context.access.admin && !isTooling,
    onRefresh: refresh,
    onCancel: () => control(false),
    onResume: () => control(true),
    onDownload: async () => {
      if (
        !current() ||
        !canAct ||
        busy.current ||
        operatorJob?.pending ||
        operatorInstallReview?.pending ||
        operatorInstallPlan?.pending ||
        operatorInstallPlanCharts?.pending ||
        operatorInstallPlanTree?.pending ||
        operatorInstallPlanContext?.pending ||
        operatorInstallPlanVerification?.pending ||
        operatorInstallPlanImage?.pending ||
        operatorInstallPlanLocalImage?.pending ||
        operatorInstallPlanWorkload?.pending ||
        operatorInstallPlanWorkloadImage?.pending ||
        operatorInstallPlanImageBinding?.pending ||
        operatorInstallPlanImagePayload?.pending ||
        operatorInstallPlanImmutableExecution?.pending ||
        operatorInstallPlanNodePlatforms?.pending ||
        !operation
      )
        return;
      busy.current = true;
      setMutation({ key: visitKey, pending: true, error: null });
      try {
        const response = await client.query<GetNvkeOperationResultTextQuery>({
          query: GET_NVKE_OPERATION_RESULT_TEXT,
          variables: { operationId },
          fetchPolicy: "no-cache",
          context: requestContext,
        });
        if (!current()) return;
        const result = response.data?.astroliftNvkeOperationResultText;
        if (!result?.ok || typeof result.data !== "string") throw new Error("DOWNLOAD_FAILED");
        const url = URL.createObjectURL(
          new Blob([result.data], { type: "application/json;charset=utf-8" })
        );
        try {
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = `nvke-${operationId}.json`;
          anchor.click();
        } finally {
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
        setMutation({ key: visitKey, pending: false, error: null });
      } catch {
        if (current()) setMutation({ key: visitKey, pending: false, error: t("downloadFailed") });
      } finally {
        busy.current = false;
      }
    },
  };
};
