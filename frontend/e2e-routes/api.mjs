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
const observedAt = "2026-09-30T15:30:00Z";
const object = (extra = {}) => ({ ...extra });
const observations = { errors: [], mutations: 0, promptInvocations: [] };
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
      items: [sharedModel],
      totalCount: 1,
      nextCursor: null,
      page: args.page,
      pageSize: args.pageSize,
    });
  if (field === "clusterModelDeployment") return args.id === sharedModelId ? sharedModel : null;
  if (field === "clusterModelSubscriptionsPage" || field === "clusterModelSubscriptionTargetsPage")
    return object({
      items: [],
      totalCount: 0,
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
      runtimeVersion: "0.15.1+cpu",
      architecture: "amd64",
      hardwareAdmission: "unknown",
    });
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
      model: args.repoId === hubModel.repoId ? hubModel : null,
    });
  if (field === "astroliftSharedModelPromptReadiness")
    return object({
      state: role === "owner" ? "READY" : "UNAVAILABLE",
      eligible: role === "owner",
      maxPromptChars: 4000,
      maxOutputTokens: 128,
      promptsPerMinute: 6,
      maxWaitSeconds: 40,
    });
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
    const resourceTotals = {
      source: "persisted_model_configuration",
      observedAt,
      replicas: 1,
      cpuCoresPerReplica: 2,
      memoryBytesPerReplica: 8589934592,
      gpuDevicesPerReplica: 0,
      gpuResource: null,
      totalCpuCores: 2,
      totalMemoryBytes: 8589934592,
      totalGpuDevices: 0,
    };
    return object({
      clusterId: args.clusterId,
      start: args.start,
      end: args.end,
      retrievedAt: observedAt,
      modelCount: 1,
      returnedCount: 1,
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
      items: [
        object({
          serviceId: sharedModelId,
          name: sharedModel.name,
          status: "active",
          desired: resourceTotals,
          applied: resourceTotals,
          observations: [measurement("ready_replicas", "count", 1)],
        }),
      ],
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
  if (req.url === "/observations/reset" && req.method === "POST") {
    observations.errors.length = 0;
    observations.mutations = 0;
    observations.promptInvocations.length = 0;
    res.statusCode = 204;
    res.end();
    return;
  }
  if (req.url === "/observations") {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(observations));
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
