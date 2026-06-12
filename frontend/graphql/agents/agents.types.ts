import type {
  AstroliftAgentTask as GeneratedAgentTask,
  AstroliftBrief as GeneratedBrief,
  AstroliftDispatcherInstance as GeneratedDispatcherInstance,
  AstroliftSkill as GeneratedSkill,
  AstroliftToolDef as GeneratedToolDef,
} from "@/graphql/__generated__/schema";

export type AstroliftSkill = Pick<
  GeneratedSkill,
  "id" | "name" | "slug" | "description" | "content" | "skillVersion" | "isGlobal" | "isActive" | "createdAt" | "updatedAt"
>;

export type AstroliftToolDef = Pick<
  GeneratedToolDef,
  "id" | "name" | "slug" | "description" | "adapter" | "inputSchema" | "outputSchema" | "handlerRef" | "createdAt"
>;

export type AstroliftBrief = Pick<
  GeneratedBrief,
  "id" | "contentHash" | "storageKey" | "config" | "createdAt"
>;

export type AstroliftAgentTask = Pick<
  GeneratedAgentTask,
  "id" | "status" | "callbackUrl" | "result" | "createdAt" | "startedAt" | "finishedAt"
>;

export type AstroliftDispatcherInstance = Pick<
  GeneratedDispatcherInstance,
  "id" | "serviceUrl" | "capabilities" | "lastHeartbeat" | "registeredAt"
>;
