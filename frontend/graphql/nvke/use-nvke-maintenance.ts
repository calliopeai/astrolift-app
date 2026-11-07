"use client";
import { useTranslations } from "next-intl";
import type { GetNvkeMaintenanceQuery } from "@/graphql/__generated__/operations";
import type { NvkeMaintenancePanelProps } from "@/components/screens/nvke/NvkeMaintenancePanel";
import type { NvkeOperationView } from "@/components/screens/nvke/types";
import type { useNvkeContext } from "./nvke.hooks";
import { useNvkeRead } from "./use-scoped-read";
import { GET_NVKE_MAINTENANCE } from "./nvke.queries";
export const useNvkeMaintenance = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  siteId: string;
  operation: NvkeOperationView | null;
  enabled: boolean;
}): NvkeMaintenancePanelProps | null => {
  const { context, operation } = args,
    t = useTranslations("nvke");
  const eligible =
    args.enabled &&
    !!context.scope &&
    !context.failed &&
    !context.loading &&
    context.access.read &&
    !!context.scopeKey &&
    operation?.operationKey === "getCluster" &&
    operation.status === "SUCCEEDED" &&
    !operation.isInventory;
  const read = useNvkeRead<GetNvkeMaintenanceQuery>(
    GET_NVKE_MAINTENANCE,
    {
      siteId: args.siteId,
      operationId: operation?.id,
      expectedActorId: context.scope?.actorId,
      expectedVersion: operation?.version,
    },
    eligible ? JSON.stringify([args.scopeKey, operation.id, operation.version]) : "",
    0,
    context.scope?.organizationId
  );
  const data = read.data?.astroliftNvkeMaintenanceObservation;
  const matched =
    !!operation &&
    !!data &&
    data.siteId === args.siteId &&
    data.operationId === operation?.id &&
    data.operationVersion === operation.version;
  if (!eligible) return null;
  return {
    observation: matched && !read.failed ? data : null,
    loading: read.loading,
    error: read.failed || (!!data && !matched) ? t("readFailed") : null,
    onRefresh: read.refresh,
  };
};
