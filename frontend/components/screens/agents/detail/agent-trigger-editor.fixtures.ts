import type {
  AstroliftAgentTrigger,
  AstroliftAgentTriggerResult,
} from "@/graphql/agents/agents.types";

import type { TriggerBindingEditorViewProps } from "./TriggerBindingEditor";

/** Hand-typed fixtures for the Control tab's trigger-binding editor. */

/** The JSON scalar is typed as an object but carries any JSON; cast in one place. */
const json = (value: unknown) => value as AstroliftAgentTrigger["inputMapping"];

const LONG =
  "platform-team-shared-production-workloads/monorepo-with-a-deliberately-long-repository-name-that-keeps-going";

export const TRIGGERS: AstroliftAgentTrigger[] = [
  {
    slug: "trg-release-notes",
    endpoint: "https://astrolift.example.com/hooks/w/7f3a9c1e",
    scmRepo: "acme/web",
    branchPattern: "main",
    inputMapping: json({ ref: "ref", sha: "after" }),
    enabled: true,
    lastTriggeredAt: "2026-09-27T14:05:00Z",
    createdAt: "2026-09-01T09:00:00Z",
  },
  {
    slug: "trg-any",
    endpoint: "https://astrolift.example.com/hooks/w/2b8d0f44",
    scmRepo: "",
    branchPattern: "",
    inputMapping: json(null),
    enabled: false,
    lastTriggeredAt: null,
    createdAt: "2026-09-20T12:30:00Z",
  },
  {
    slug: "trg-one-mapping",
    endpoint: "https://astrolift.example.com/hooks/w/c41e7a90",
    scmRepo: "acme/infra",
    branchPattern: "release/*",
    inputMapping: json({ ref: "ref" }),
    enabled: true,
    lastTriggeredAt: null,
    createdAt: "2026-09-25T08:15:00Z",
  },
];

export const LONG_TRIGGERS: AstroliftAgentTrigger[] = [
  {
    slug: "trg-long",
    endpoint: `https://astrolift.example.com/hooks/w/${"a1b2c3d4".repeat(12)}`,
    scmRepo: LONG,
    branchPattern: "feature/an-extremely-long-branch-pattern-for-wrapping-checks/**",
    inputMapping: json(
      Object.fromEntries(Array.from({ length: 14 }, (_, i) => [`input_${i}`, `payload.path.${i}`]))
    ),
    enabled: true,
    lastTriggeredAt: "2026-09-28T07:59:00Z",
    createdAt: "2026-06-01T00:00:00Z",
  },
];

export const CREATED: AstroliftAgentTriggerResult = {
  ok: true,
  message: "",
  slug: "trg-new",
  endpoint: "https://astrolift.example.com/hooks/w/9e0f1a2b",
  signingSecret: "whsec_3kT9vQ2xLm8pR4nZ7bY1cW6dF0gH5jK",
};

export const EDITOR: TriggerBindingEditorViewProps = {
  agentSlug: "release-notes",
  agentName: "Release notes",
  triggers: TRIGGERS,
  loading: false,
  creating: false,
  onCreate: async () => CREATED,
  onUnbind: async () => {},
};
