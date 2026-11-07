"use client";
import { NvkeOperationScreen } from "@/components/screens/nvke/NvkeOperationScreen";
import { useNvkeOperation } from "@/graphql/nvke/use-nvke-operation";

export function NvkeOperationClient({
  siteId,
  operationId,
}: {
  siteId: string;
  operationId: string;
}) {
  const { scopeKey, ...props } = useNvkeOperation(siteId, operationId);
  return <NvkeOperationScreen key={scopeKey} {...props} />;
}
