// Controlled HTTP fixtures for the real Next route walk. These are frontend
// roles; backend authorization remains covered by its RoleBinding tests.
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import {
  buildSchema,
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
const object = (extra = {}) => ({ ...extra });
const observations = { errors: [], mutations: 0 };
function value(type, field, args, role) {
  if (isNonNullType(type)) return value(type.ofType, field, args, role);
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
      return ["apps", "agents", "workflows", "admin"].map((key) => object({ key }));
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
