import type { EnvironmentSpec } from "./environment-specs";

export const ENVIRONMENT_SPEC: EnvironmentSpec = {
  id: "spec-1",
  slug: "engineering-codex",
  name: "Engineering Codex",
  runtime: "codex",
  imageTag: "",
  agentType: "codex",
  toolPreset: "dev+k8s",
  vncEnabled: true,
  managedModel: false,
  modelGateway: true,
  runAsNonRoot: true,
  allowInstall: false,
  teamId: "team-1",
  projectId: "project-1",
  configRepo: "calliopeai/agents",
  configBranch: "main",
  configManifestPath: "agents/engineering/astrolift.toml",
  secretRefs: [{ env_var: "GITHUB_TOKEN", uri: "vault://engineering/github" }],
  updatedAt: "2026-09-29T18:00:00Z",
};
export const LONG_ENVIRONMENT_SPEC: EnvironmentSpec = {
  ...ENVIRONMENT_SPEC,
  id: "spec-long",
  slug: "a".repeat(128),
  name: "Environment".repeat(24),
  imageTag: `registry.example.com/${"project".repeat(24)}/agent@sha256:${"a".repeat(64)}`,
  configRepo: `https://example.com/${"repository".repeat(48)}`,
  secretRefs: [{ env_var: "TOKEN", uri: `vault://secrets/${"owner".repeat(100)}` }],
};
