import type {
  AgentConfigModel,
  AgentSkillModel,
  AgentToolModel,
} from "@/lib/manifest/agent-config-model";
import type { ManifestErr } from "@/lib/manifest/agent-config-schema";

import type { ManifestPreviewScreenProps } from "./ManifestPreviewScreen";
import type { RenderedManifest } from "./use-manifest-preview";

/**
 * Hand-typed fixtures for the agent config builder, its pane, and the
 * manifest preview screen (group app-config-agent).
 */

const noop = () => {};

export const LONG =
  "platform-team-shared-production-support-agent-with-a-deliberately-long-name-that-keeps-going";

// ─── Agent config builder ────────────────────────────────────────────────────

export const SKILL: AgentSkillModel = {
  slug: "triage",
  name: "Ticket triage",
  description: "Classifies incoming support tickets and routes them.",
  content: "You triage support tickets. Label each with a queue and a priority.",
  contentKey: "content",
  dependencies: ["search"],
  agent_type: "support",
  scaffolding_tags: ["support", "routing"],
  is_active: true,
  extra: {},
};

export const TOOL: AgentToolModel = {
  slug: "search",
  name: "Knowledge search",
  description: "Searches the help center.",
  adapter: "python_fn",
  handler_ref: "support.tools.search:run",
  input_schema: '{ "type": "object" }',
  output_schema: '{ "type": "object" }',
  implementation_config: "{ }",
  commands: ["search"],
  required_packages: ["httpx"],
  capability_group: "knowledge",
  agent_type_bindings: ["support"],
  is_builtin: false,
  extra: {},
};

export const AGENT_MODEL: AgentConfigModel = {
  astrolift_version: "1",
  environment: {
    tool_preset: "standard",
    allow_install: true,
    vars: [{ key: "LOG_LEVEL", value: "info" }],
  },
  skills: [SKILL, { ...SKILL, slug: "summarize", name: "Summarize", is_active: false }],
  tools: [TOOL, { ...TOOL, slug: "shell", name: "Shell", adapter: "mcp_server", is_builtin: true }],
  extra: { metadata: {} },
};

export const EMPTY_AGENT_MODEL: AgentConfigModel = {
  astrolift_version: "",
  environment: { tool_preset: "", allow_install: false, vars: [] },
  skills: [],
  tools: [],
  extra: {},
};

export const AGENT_ERRORS: ManifestErr[] = [
  { path: "astrolift_version", message: "astrolift_version is required for an agent config." },
  { path: "skills[1].slug", message: 'Duplicate skill slug "triage".' },
  { path: "tools[0].input_schema", message: "Input schema: Unexpected token" },
];

export const INVALID_AGENT_MODEL: AgentConfigModel = {
  ...AGENT_MODEL,
  astrolift_version: "",
  skills: [SKILL, { ...SKILL }],
  tools: [{ ...TOOL, input_schema: "{ type: object" }],
};

export const LONG_AGENT_MODEL: AgentConfigModel = {
  ...AGENT_MODEL,
  astrolift_version: LONG,
  environment: {
    tool_preset: LONG,
    allow_install: false,
    vars: [{ key: `${LONG}_KEY`.toUpperCase().replace(/-/g, "_"), value: LONG }],
  },
  skills: [{ ...SKILL, slug: LONG, name: LONG, description: LONG, content: LONG }],
  tools: [{ ...TOOL, slug: LONG, name: LONG, handler_ref: `${LONG}:run`, capability_group: LONG }],
  extra: { [LONG]: {} },
};

export const BUILDER_PROPS = { model: AGENT_MODEL, errors: [] as ManifestErr[], onChange: noop };

// ─── Agent config pane (TOML drafts) ─────────────────────────────────────────

export const AGENT_TOML = `astrolift_version = "1"

[environment]
tool_preset = "standard"
allow_install = true
LOG_LEVEL = "info"

[skills.triage]
name = "Ticket triage"
description = "Classifies incoming support tickets and routes them."
content = "You triage support tickets. Label each with a queue and a priority."
dependencies = ["search"]
agent_type = "support"

[tools.search]
name = "Knowledge search"
adapter = "python_fn"
handler_ref = "support.tools.search:run"
commands = ["search"]
`;

/** Fails validation: no astrolift_version, and an off-contract tool_preset lands as a reserved env var. */
export const INVALID_AGENT_TOML = `[environment]
tool_preset = 3

[skills.triage]
name = "Ticket triage"
`;

/** A multi-line string the scoped codec will not round-trip. */
export const UNSAFE_AGENT_TOML = `astrolift_version = "1"

[skills.triage]
content = """
You triage support tickets.
Label each with a queue and a priority.
"""
`;

export const LONG_AGENT_TOML = `astrolift_version = "${LONG}"

[environment]
tool_preset = "${LONG}"

[skills.${LONG}]
name = "${LONG}"
description = "${LONG} ${LONG}"

[tools.${LONG}]
name = "${LONG}"
handler_ref = "${LONG}:run"
`;

export const PANE_PROPS = { draft: AGENT_TOML, onDraftChange: noop, onSwitchToCode: noop };

// ─── Manifest preview ────────────────────────────────────────────────────────

export const RENDERED: RenderedManifest = {
  appSlug: "checkout",
  environmentName: "production",
  imageTag: "a1b2c3d",
  namespace: "checkout-production",
  resources: [
    { apiVersion: "apps/v1", kind: "Deployment", metadata: { name: "checkout-web" } },
    { apiVersion: "v1", kind: "Service", metadata: { name: "checkout-web" } },
    { apiVersion: "networking.k8s.io/v1", kind: "Ingress", metadata: { name: "checkout-web" } },
  ],
  error: null,
  errorPath: null,
  errorLine: null,
  errorColumn: null,
};

export const PREVIEW_PROPS: ManifestPreviewScreenProps = {
  slug: "checkout",
  envName: "__preview__",
  setEnvName: noop,
  imageTag: "",
  setImageTag: noop,
  environments: [
    { id: "env-1", name: "staging" },
    { id: "env-2", name: "production" },
  ],
  loading: false,
  result: RENDERED,
};

export const RENDER_ERROR: RenderedManifest = {
  ...RENDERED,
  resources: [],
  error: 'invalid type: string "two", expected u32 for key `workloads.web.replicas`',
  errorPath: "workloads.web.replicas",
  errorLine: 14,
  errorColumn: 12,
};

export const LONG_RENDERED: RenderedManifest = {
  ...RENDERED,
  appSlug: LONG,
  environmentName: LONG,
  imageTag: LONG,
  namespace: LONG,
  resources: [{ apiVersion: "apps/v1", kind: "Deployment", metadata: { name: LONG } }],
};
