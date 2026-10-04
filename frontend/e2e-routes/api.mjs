// Controlled HTTP fixtures for the real Next route walk. These are frontend
// roles; backend authorization remains covered by its RoleBinding tests.
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import {
  buildSchema,
  GraphQLError,
  graphql,
  getNamedType,
  isListType,
  isNonNullType,
  isEnumType,
  isObjectType,
} from "graphql";

const schema = buildSchema(readFileSync(new URL("../schema.graphql", import.meta.url), "utf8"));
const permissions = [
  ...readFileSync(
    new URL("../lib/permissions/permissions.generated.ts", import.meta.url),
    "utf8"
  ).matchAll(/^  "([^"]+)",/gm),
].map(([_, slug]) => slug);
const id = "11111111-1111-4111-8111-111111111111";
const sharedModelId = "22222222-2222-4222-8222-222222222222";
const sharedClusterId = "33333333-3333-4333-8333-333333333333";
const sharedProviderId = "44444444-4444-4444-8444-444444444444";
const createdModelId = "77777777-7777-4777-8777-777777777777";
const observedAt = "2026-09-30T15:30:00Z";
const object = (extra = {}) => ({ ...extra });
const observations = { errors: [], mutations: 0, promptInvocations: [] };
const modelWriteRequests = [];
let acceptedModel = null;
const sharedResources = {
  cpuRequest: "2",
  memoryRequest: "8Gi",
  gpuCount: 0,
  replicas: 1,
  cpuKvCacheGiB: 2,
};
const sharedModel = {
  id: sharedModelId,
  version: 5,
  name: "Controlled shared CPU model",
  organizationId: id,
  clusterId: sharedClusterId,
  providerId: sharedProviderId,
  clusterSlug: "shared-fixture",
  clusterName: "Controlled shared cluster",
  modelRepo: "Qwen/Qwen3-0.6B",
  revisionSha: "a".repeat(40),
  computeMode: "cpu",
  subscriptionsEnabled: true,
  runtimeSupported: true,
  runtimeReason: null,
  status: "active",
  reason: null,
  ready: true,
  readinessObservedAt: observedAt,
  readinessGeneration: 3,
  desiredSubscriptionRevision: 2,
  appliedSubscriptionRevision: 2,
  operationId: "controlled-shared-reconcile",
  operationStartedAt: observedAt,
  operationCompletedAt: observedAt,
  desiredResources: sharedResources,
  appliedResources: sharedResources,
};
let currentSharedModel = sharedModel;
const activeSubscription = {
  id: "88888888-8888-4888-8888-888888888888",
  version: 2,
  modelDeploymentId: sharedModelId,
  appId: "66666666-6666-4666-8666-666666666666",
  appSlug: "controlled-app",
  appName: "Controlled consumer",
  environmentId: "55555555-5555-4555-8555-555555555555",
  environmentName: "production",
  alias: "chat",
  bindingPrefix: "MODEL_CHAT_",
  status: "active",
  canRevoke: true,
  desiredEnabled: true,
  desiredRevision: 2,
  appliedRevision: 2,
  reason: null,
  reconcileStartedAt: observedAt,
  reconciledAt: observedAt,
};
const siblingSubscription = {
  ...activeSubscription,
  id: "99999999-9999-4999-8999-999999999999",
  environmentId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  environmentName: "staging",
  alias: "search",
  bindingPrefix: "MODEL_SEARCH_",
};
let subscriptionRows = [activeSubscription, siblingSubscription];
const hubModel = {
  repoId: sharedModel.modelRepo,
  revisionSha: sharedModel.revisionSha,
  author: "Qwen",
  pipelineTag: "text-generation",
  library: "transformers",
  license: "apache-2.0",
  gated: "NONE",
  architectures: ["Qwen3ForCausalLM"],
  downloads: 0,
  likes: 0,
  compatibility: "UNKNOWN",
};
const measurement = (key, unit, amount, state = "AVAILABLE") => ({
  key,
  unit,
  value: amount,
  state,
  source: state === "UNSUPPORTED" ? "unsupported_device_pod_mapping" : "controlled_prometheus",
  observedAt: state === "AVAILABLE" ? observedAt : null,
  aggregationWindowSeconds: 0,
  samples: [],
});
function value(type, field, args, role) {
  if (isNonNullType(type)) return value(type.ofType, field, args, role);
  if (field === "clusterModelDeploymentsPage")
    return object({
      items: acceptedModel ? [currentSharedModel, acceptedModel] : [currentSharedModel],
      totalCount: acceptedModel ? 2 : 1,
      nextCursor: null,
      page: args.page,
      pageSize: args.pageSize,
    });
  if (field === "clusterModelDeployment")
    return args.id === sharedModelId
      ? currentSharedModel
      : args.id === acceptedModel?.id
        ? acceptedModel
        : null;
  if (field === "clusterModelSubscriptionsPage")
    return object({
      items: args.modelDeploymentId === sharedModelId ? subscriptionRows : [],
      totalCount: args.modelDeploymentId === sharedModelId ? subscriptionRows.length : 0,
      nextCursor: null,
      page: args.page,
      pageSize: args.pageSize,
    });
  if (field === "capabilities") return ["models.connection_approvals"];
  if (field === "modelConnectionRestriction" || field === "organizationModelConnectionPolicy")
    return object({
      id: null,
      version: 0,
      mode: "AUTO",
      requiredApprovals: 1,
      allowSelfApproval: true,
    });
  if (field === "modelConnectionAction")
    return object({
      action: role === "owner" && currentSharedModel.ready ? "AUTO" : "DENY",
      reason: currentSharedModel.ready ? null : "Controlled reconciliation remains pending.",
      policyVersion: "controlled-auto-policy",
      requiredApprovals: 1,
      allowSelfApproval: true,
    });
  if (field === "modelConnectionTargetsPage")
    return object({
      page: args.page,
      pageSize: args.pageSize,
      nextCursor: null,
      totalCount: args.input?.modelDeploymentId === sharedModelId ? 1 : 0,
      items:
        args.input?.modelDeploymentId === sharedModelId
          ? [
              object({
                environmentId: activeSubscription.environmentId,
                environmentVersion: 4,
                appVersion: 7,
                appSlug: activeSubscription.appSlug,
                environmentName: activeSubscription.environmentName,
                clusterId: sharedClusterId,
                eligible: role === "owner" && currentSharedModel.ready,
                action: role === "owner" && currentSharedModel.ready ? "AUTO" : "DENY",
                reason: currentSharedModel.ready
                  ? null
                  : "Controlled reconciliation remains pending.",
                policyVersion: "controlled-auto-policy",
                requiredApprovals: 1,
                allowSelfApproval: true,
              }),
            ]
          : [],
    });
  if (field === "clusterModelSubscriptionTargetsPage")
    return object({
      items:
        args.modelDeploymentId === sharedModelId
          ? [
              object({
                environmentId: activeSubscription.environmentId,
                environmentVersion: 4,
                appId: activeSubscription.appId,
                appSlug: activeSubscription.appSlug,
                appName: activeSubscription.appName,
                environmentName: activeSubscription.environmentName,
                clusterId: sharedClusterId,
                eligible: currentSharedModel.ready,
                reason: currentSharedModel.ready
                  ? null
                  : "Controlled reconciliation remains pending.",
              }),
            ]
          : [],
      totalCount: args.modelDeploymentId === sharedModelId ? 1 : 0,
      nextCursor: null,
      page: args.page,
      pageSize: args.pageSize,
    });
  if (field === "clusterModelPlacementClustersPage")
    return object({
      items: [
        object({
          id: sharedClusterId,
          providerId: sharedProviderId,
          name: sharedModel.clusterName,
          slug: sharedModel.clusterSlug,
          region: "us-west-2",
        }),
      ],
      totalCount: 1,
      nextCursor: null,
      page: args.page,
      pageSize: args.pageSize,
    });
  if (field === "clusterModelRuntimeAdmission")
    return object({
      eligible: role === "owner",
      reason: null,
      runtimeVersion: args.input?.computeMode === "gpu" ? "0.15.1" : "0.15.1+cpu",
      architecture: "amd64",
      hardwareAdmission: "unknown",
    });
  if (field === "modelHostingAction")
    return object({
      allowed: role === "owner" && args.organizationId === id,
      reason: role === "owner" ? null : "Controlled hosting authority refusal.",
    });
  if (field === "huggingFaceConnectionsPage")
    return object({ items: [], totalCount: 0, page: args.page, pageSize: args.pageSize });
  if (field === "clusterModelSourceAccess") {
    const accessible =
      role === "owner" &&
      args.organizationId === id &&
      [hubModel.repoId, "Qwen/Qwen2.5-0.5B-Instruct"].includes(args.modelRepo) &&
      args.revisionSha === hubModel.revisionSha &&
      args.connectionId == null &&
      args.expectedConnectionVersion == null;
    return object({
      accessible,
      reason: accessible ? null : "Controlled pinned-source access refusal.",
      observedAt,
      model: accessible ? { ...hubModel, repoId: args.modelRepo } : null,
    });
  }
  if (field === "astroliftHuggingFaceModels")
    return object({
      state: "AVAILABLE",
      source: "controlled_hub_transport",
      observedAt,
      nextCursor: null,
      retryAfterSeconds: null,
      items: [hubModel],
    });
  if (field === "astroliftHuggingFaceModel")
    return object({
      state: "AVAILABLE",
      source: "controlled_hub_transport",
      observedAt,
      retryAfterSeconds: null,
      model: [hubModel.repoId, "Qwen/Qwen2.5-0.5B-Instruct"].includes(args.repoId)
        ? { ...hubModel, repoId: args.repoId }
        : null,
    });
  if (field === "astroliftSharedModelPromptReadiness") {
    const ready =
      role === "owner" &&
      args.id === sharedModelId &&
      args.expectedVersion === currentSharedModel.version &&
      currentSharedModel.ready;
    return object({
      state: ready ? "READY" : "INACTIVE",
      eligible: ready,
      maxPromptChars: 4000,
      maxOutputTokens: 128,
      promptsPerMinute: 6,
      maxWaitSeconds: 40,
    });
  }
  if (field === "astroliftModelDeploymentMetrics")
    return object({
      serviceId: args.serviceId,
      clusterId: args.expectedClusterId,
      start: args.start,
      end: args.end,
      retrievedAt: observedAt,
      stepSeconds: 30,
      scope: "deployment_aggregate_not_app_attributed",
      sampleLimit: 120,
      metrics: [
        measurement("requests_waiting", "count", 0),
        measurement("vram_usage", "bytes", null, "UNSUPPORTED"),
      ],
    });
  if (field === "astroliftClusterModelDensity") {
    const resourceTotals = (resources) => ({
      source: "persisted_model_configuration",
      observedAt,
      replicas: 1,
      cpuCoresPerReplica: Number(resources.cpuRequest),
      memoryBytesPerReplica: 8589934592,
      gpuDevicesPerReplica: resources.gpuCount,
      gpuResource: resources.gpuCount > 0 ? "nvidia.com/gpu" : null,
      totalCpuCores: Number(resources.cpuRequest),
      totalMemoryBytes: 8589934592,
      totalGpuDevices: resources.gpuCount,
    });
    const models = acceptedModel ? [currentSharedModel, acceptedModel] : [currentSharedModel];
    return object({
      clusterId: args.clusterId,
      start: args.start,
      end: args.end,
      retrievedAt: observedAt,
      modelCount: models.length,
      returnedCount: models.length,
      inventoryLimit: 20,
      truncated: false,
      scope: "organization_cluster_owned_models",
      source: "persisted_model_inventory",
      capacity: object({
        state: "UNSUPPORTED",
        source: "unsupported_tenant_node_pool_mapping",
        observedAt: null,
        gpuDevices: [],
        cpuCores: null,
        memoryBytes: null,
        vramBytes: null,
        freshnessSeconds: 1800,
      }),
      items: models.map((model) =>
        object({
          serviceId: model.id,
          name: model.name,
          status: model.status,
          desired: resourceTotals(model.desiredResources),
          applied: model.appliedResources ? resourceTotals(model.appliedResources) : null,
          observations: [
            measurement(
              "ready_replicas",
              "count",
              model.ready ? 1 : null,
              model.ready ? "AVAILABLE" : "NO_DATA"
            ),
          ],
        })
      ),
    });
  }
  if (field === "astroliftModelEndpointsPage")
    return object({
      items: [
        object({
          id,
          name: "Controlled local vLLM endpoint",
          variant: "vllm",
          status: "active",
          registeredAppSlug: "fixture",
          environmentName: "production",
          config: { model: "Controlled fixture" },
        }),
      ],
      totalCount: 1,
      page: args.page ?? 1,
      pageSize: args.pageSize ?? 10,
    });
  if (field === "astroliftModelPromptReadiness") {
    if (role !== "owner")
      throw new GraphQLError("Controlled prompt permission denial", {
        extensions: { code: "PERMISSION_DENIED" },
      });
    return object({
      state: "READY",
      eligible: true,
      maxPromptChars: 4000,
      maxOutputTokens: 128,
      promptsPerMinute: 6,
      maxWaitSeconds: 40,
    });
  }
  if (field === "astroliftMyUiPreferences")
    return object({
      homeLayout: null,
      homeLayoutAsked: true,
      fleetView: "list",
      workflowView: "list",
      appView: "classic",
      flowParticles: false,
      motion: "reduced",
      restrictedSettings: "show",
      restrictedSettingsChoice: null,
      restrictedSettingsOrgDefault: "show",
      appearance: {},
    });
  if (field === "reprovision")
    return object({ needsReprovision: false, state: "ready", reason: "" });
  if (field === "heartbeatStatus") return "connected";
  if (field === "currency") return "USD";
  if (field === "lifecycle") return "managed";
  if (field === "clientKind") return "web";
  if (field === "triggerMode") return "manual";
  if (field === "level") return "info";
  if (field === "lastSeen" || field === "timestamp") return "2026-09-01T12:00:00Z";
  if (isListType(type)) {
    if (field === "featureFlags")
      return [
        "zentinelle.enabled",
        "admin.permissions_enabled",
        "admin.cost_enabled",
        "admin.quotas_enabled",
      ].map((key) => object({ key, enabled: true }));
    if (field === "modules")
      return ["apps", "agents", "workflows", "admin", "models"].map((key) => object({ key }));
    if (field === "astroliftMyPermissions")
      return role === "owner"
        ? permissions
        : role === "reader"
          ? permissions.filter((p) => /\.(read|read_logs|read_metrics|watch|access)$/.test(p))
          : [];
    if (field === "astroliftSecretChangeProposals")
      return [
        object({
          status: "pending",
          op: "set",
          approvals: [],
          approvalsCount: 0,
          requiredApproverCount: 1,
        }),
      ];
    if (field === "astroliftDeployments")
      return [object({ status: "pending_approval", approvalsReceived: 0, approvalsRequired: 1 })];
    if (field === "items" && getNamedType(type).name === "AstroliftPrincipal")
      return [
        object({
          kind: "GROUP",
          groupExternalId: "route-group",
          name: "route-group",
          key: "group:route-group",
        }),
      ];
    if (/errors|lockedFields|nextCursor|Notifications|Incidents/.test(field)) return [];
    if (!isObjectType(getNamedType(type))) return [];
    return [object()];
  }
  const named = getNamedType(type);
  if (isEnumType(named)) return named.getValues()[0].name;
  if (isObjectType(named)) return object();
  if (named.name === "Boolean") {
    if (/canView/.test(field)) return role !== "member";
    if (/canCreate|canManage|canRun/.test(field)) return role === "owner";
    return !/deleted|reauth|required|hasNext|hasPrevious|elevated/i.test(field);
  }
  if (named.name === "Int" || named.name === "Float") return 1;
  if (named.name === "JSON" || named.name === "JSONString") return {};
  if (/deletedAt|nextCursor/.test(field)) return null;
  if (/Date|Time/.test(named.name) || /At$/.test(field)) return "2026-09-01T12:00:00Z";
  if (named.name === "ID" || named.name === "GUID" || /Id$|^id$|guid/i.test(field))
    return args.id ?? id;
  if (/slug/i.test(field)) return args.slug ?? "fixture";
  if (/email/i.test(field)) return "route-test@example.test";
  if (/url|href/i.test(field)) return "https://example.test";
  if (/status|phase|state/.test(field)) return "running";
  if (/kind/.test(field)) return "app";
  if (/yaml|manifest/.test(field)) return "name: fixture\n";
  if (/username/.test(field)) return role;
  if (field === "scopeKind") return "ORG";
  if (field === "principalKind") return "user";
  if (field === "authKind") return "web";
  if (field === "lifecycle") return "active";
  if (/version/.test(field)) return "1.0.0";
  if (field === "topology" || field === "topologyKind") return "service";
  if (field === "timezone") return "UTC";
  return "Fixture";
}

createServer(async (req, res) => {
  if (req.url === "/health") {
    res.end("ok");
    return;
  }
  if (
    ["/observations/reset", "/observations/reset?model=unsubscribed"].includes(req.url) &&
    req.method === "POST"
  ) {
    observations.errors.length = 0;
    observations.mutations = 0;
    observations.promptInvocations.length = 0;
    modelWriteRequests.length = 0;
    acceptedModel = null;
    currentSharedModel = sharedModel;
    subscriptionRows = req.url.endsWith("model=unsubscribed")
      ? []
      : [activeSubscription, siblingSubscription];
    res.statusCode = 204;
    res.end();
    return;
  }
  if (req.url === "/observations") {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(observations));
    return;
  }
  if (req.url === "/observations/model-writes") {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(modelWriteRequests));
    return;
  }
  let body = "";
  for await (const chunk of req) body += chunk;
  try {
    const { query, variables, operationName } = JSON.parse(body);
    const role = /sessionid=(owner|reader|member)/.exec(req.headers.cookie ?? "")?.[1] ?? "member";
    const result = await graphql({
      schema,
      source: query,
      variableValues: variables,
      operationName,
      rootValue: {},
      fieldResolver(source, args, context, info) {
        if (info.parentType.name === "Mutation") {
          const input = args.input;
          const update = info.fieldName === "updateClusterModel";
          const deprovision = info.fieldName === "deprovisionClusterModel";
          if (
            (update || deprovision) &&
            role === "owner" &&
            modelWriteRequests.length === 0 &&
            input.organizationId === id &&
            input.id === sharedModelId &&
            input.expectedClusterId === sharedClusterId &&
            input.expectedProviderId === sharedProviderId &&
            input.ifMatchVersion === 5 &&
            (update
              ? input.cpuRequest === "3" &&
                input.memoryRequest === "8Gi" &&
                input.gpuCount === 0 &&
                input.cpuKvCacheGiB === 2 &&
                input.allowSubscriptions === true
              : input.deleteData === false)
          ) {
            modelWriteRequests.push({ operationName, input: { ...input } });
            if (deprovision && subscriptionRows.length > 0)
              return object({
                ok: false,
                errors: [
                  object({
                    code: "CONFLICT",
                    message:
                      "Controlled refusal: reconcile all subscription revocations before removal.",
                    field: null,
                    resource: null,
                    retryAfter: null,
                    currentVersion: null,
                    requestedVersion: null,
                  }),
                ],
                data: null,
              });
            currentSharedModel = {
              ...sharedModel,
              version: 7,
              status: update ? "updating" : "deprovisioning",
              ready: false,
              readinessObservedAt: null,
              readinessGeneration: null,
              desiredSubscriptionRevision: 3,
              operationId: update ? "controlled-accepted-update" : "controlled-accepted-delete",
              operationCompletedAt: null,
              desiredResources: update
                ? {
                    ...sharedModel.desiredResources,
                    cpuRequest: input.cpuRequest,
                    memoryRequest: input.memoryRequest,
                    gpuCount: input.gpuCount,
                    cpuKvCacheGiB: input.cpuKvCacheGiB,
                  }
                : sharedModel.desiredResources,
            };
            return object({ ok: true, errors: [], data: currentSharedModel });
          }
          const subscribe = info.fieldName === "subscribeClusterModel";
          const revoke = info.fieldName === "revokeModelSubscription";
          if (
            (subscribe || revoke) &&
            role === "owner" &&
            modelWriteRequests.length === 0 &&
            input.organizationId === id &&
            input.expectedClusterId === sharedClusterId &&
            input.expectedProviderId === sharedProviderId &&
            (subscribe
              ? input.modelDeploymentId === sharedModelId &&
                input.appEnvironmentId === activeSubscription.environmentId &&
                input.alias === "assistant" &&
                input.ifMatchVersion === 5 &&
                input.ifMatchEnvironmentVersion === 4
              : input.id === activeSubscription.id &&
                input.ifMatchVersion === 2 &&
                input.ifMatchDeploymentVersion === 5)
          ) {
            modelWriteRequests.push({ operationName, input: { ...input } });
            currentSharedModel = {
              ...sharedModel,
              version: 7,
              status: "updating",
              ready: false,
              readinessObservedAt: null,
              readinessGeneration: null,
              desiredSubscriptionRevision: 3,
              operationId: "controlled-accepted-subscription",
              operationCompletedAt: null,
            };
            const row = {
              ...activeSubscription,
              id: subscribe ? "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" : activeSubscription.id,
              version: subscribe ? 1 : 3,
              alias: subscribe ? input.alias : activeSubscription.alias,
              bindingPrefix: subscribe ? "MODEL_ASSISTANT_" : activeSubscription.bindingPrefix,
              status: subscribe ? "pending" : "revoking",
              desiredEnabled: subscribe,
              desiredRevision: 3,
              appliedRevision: subscribe ? 0 : 2,
              reconciledAt: null,
              canRevoke: false,
            };
            subscriptionRows = subscribe
              ? [...subscriptionRows, row]
              : subscriptionRows.map((existing) => (existing.id === row.id ? row : existing));
            return object({
              ok: true,
              errors: [],
              data: object({
                restartRequired: true,
                deployment: currentSharedModel,
                subscription: row,
              }),
            });
          }
          if (
            info.fieldName === "provisionClusterModel" &&
            role === "owner" &&
            args.input.organizationId === id &&
            args.input.clusterId === sharedClusterId &&
            args.input.expectedProviderId === sharedProviderId &&
            [hubModel.repoId, "Qwen/Qwen2.5-0.5B-Instruct"].includes(args.input.modelRepo) &&
            args.input.revisionSha === hubModel.revisionSha &&
            ((args.input.computeMode === "cpu" &&
              args.input.gpuCount === 0 &&
              ["Controlled newly created CPU model", "Qwen2.5-0.5B-Instruct"].includes(
                args.input.name
              )) ||
              (args.input.computeMode === "gpu" &&
                args.input.gpuCount === 1 &&
                args.input.name === "Controlled newly created GPU model")) &&
            modelWriteRequests.length === 0
          ) {
            const input = args.input;
            modelWriteRequests.push({ operationName, input: { ...input } });
            acceptedModel = {
              ...sharedModel,
              id: createdModelId,
              version: 3,
              name: input.name,
              modelRepo: input.modelRepo,
              revisionSha: input.revisionSha,
              computeMode: input.computeMode,
              subscriptionsEnabled: input.allowSubscriptions,
              status: "updating",
              ready: false,
              readinessObservedAt: null,
              readinessGeneration: null,
              desiredSubscriptionRevision: 0,
              appliedSubscriptionRevision: 0,
              operationId: "controlled-accepted-create",
              operationCompletedAt: null,
              appliedResources: null,
              desiredResources: {
                cpuRequest: input.cpuRequest,
                memoryRequest: input.memoryRequest,
                gpuCount: input.gpuCount,
                cpuKvCacheGiB: input.cpuKvCacheGiB ?? null,
                replicas: 1,
              },
            };
            return object({ ok: true, errors: [], data: acceptedModel });
          }
          if (
            info.fieldName === "testSharedModelEndpoint" &&
            role === "owner" &&
            args.input.managedServiceId === sharedModelId &&
            args.input.expectedClusterId === sharedClusterId &&
            args.input.expectedProviderId === sharedProviderId &&
            args.input.expectedVersion === 5 &&
            args.input.prompt === "Answer shared 2 plus 2 for the local browser regression."
          ) {
            observations.promptInvocations.push({ ...args.input });
            return object({
              ok: true,
              errors: [],
              data: object({
                status: "succeeded",
                reply: "4",
                latencyMs: 120,
                promptTokens: 9,
                completionTokens: 1,
                totalTokens: 10,
                error: "",
              }),
            });
          }
          if (
            info.fieldName === "testModelEndpoint" &&
            role === "owner" &&
            args.input.managedServiceId === id &&
            args.input.prompt === "Answer 2 plus 2 for the local browser regression."
          ) {
            observations.promptInvocations.push({
              id: args.input.managedServiceId,
              prompt: args.input.prompt,
            });
            return object({
              ok: true,
              errors: [],
              data: object({
                status: "succeeded",
                reply: "4",
                latencyMs: 120,
                promptTokens: 9,
                completionTokens: 1,
                totalTokens: 10,
              }),
            });
          }

          observations.mutations++;
          throw new Error("Route walk must not perform mutations");
        }
        if (info.fieldName === "astroliftPipeline" && !/^[0-9a-f-]{36}$/i.test(args.id))
          throw new Error("Invalid pipeline ID");
        return source && Object.hasOwn(source, info.fieldName)
          ? source[info.fieldName]
          : value(info.returnType, info.fieldName, args, role);
      },
    });
    if (result.errors) {
      observations.errors.push({
        operationName,
        role,
        messages: result.errors.map((e) => e.message),
      });
      console.error(
        operationName,
        result.errors.map((e) => e.message)
      );
    }
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(result));
  } catch (error) {
    res.statusCode = 500;
    res.end(String(error));
  }
}).listen(Number(process.env.ROUTE_API_PORT ?? 6172), "127.0.0.1");
