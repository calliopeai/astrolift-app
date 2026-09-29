import type { AddToolState } from "./use-add-tool";
import type { ImportResult, ImportSkillsState } from "./use-import-skills";
import type { NewSkillState } from "./use-new-skill";
import type { SkillListItem } from "./skills-list";
import type { SkillBuilderState } from "./use-skill-builder";
import type { Skill } from "./use-skill";
import type { ToolDef } from "./use-skill-tool-defs";

/** Hand-typed fixtures for the agent skills screens (group agent-skills). */

const noop = () => {};
const noopAsync = async () => {};
const none = async () => ({});

export const LONG =
  "customer-support-escalation-triage-and-knowledge-base-summarisation-for-the-emea-region";

/** The long strings every list story carries: a 64-char SHA, a 200-char ARN, an unbroken URL. */
export const SHA64 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
export const ARN200 =
  `arn:aws:iam::123456789012:role/${"astrolift-agent-skill-runtime-".repeat(6)}`.slice(0, 200);
export const LONG_URL = `https://github.com/example-org/${"agent-config-".repeat(8)}repo/blob/main/astrolift.toml`;

// ─── Skills list ──────────────────────────────────────────────────────────────

export const SKILL_ITEMS: SkillListItem[] = [
  {
    id: "sk-1",
    name: "Document Summariser",
    slug: "document-summariser",
    description: "Summarises long documents into a short brief for the agent.",
    skillVersion: 3,
    isGlobal: false,
    isActive: true,
    updatedAt: "2026-09-27T10:12:00Z",
  },
  {
    id: "sk-2",
    name: "Release Notes",
    slug: "release-notes",
    description: "",
    skillVersion: 1,
    isGlobal: false,
    isActive: false,
    updatedAt: "2026-09-20T08:00:00Z",
  },
  {
    id: "sk-3",
    name: "Repo Search",
    slug: "repo-search",
    description: "Search the connected repositories for code and docs.",
    skillVersion: 7,
    isGlobal: true,
    isActive: true,
    updatedAt: "2026-09-25T16:40:00Z",
  },
];

/** Sixty skills: numbered pages. */
export const MANY_SKILLS: SkillListItem[] = Array.from({ length: 60 }, (_, i) => ({
  id: `sk-many-${i}`,
  name: `Skill ${String(i + 1).padStart(2, "0")}`,
  slug: `skill-${i + 1}`,
  description: i % 3 === 0 ? "" : `What skill ${i + 1} teaches the agent.`,
  skillVersion: (i % 9) + 1,
  isGlobal: i % 5 === 0,
  isActive: i % 4 !== 0,
  updatedAt: new Date(Date.UTC(2026, 8, 28, 12) - i * 3_600_000).toISOString(),
}));

export const LONG_SKILL: SkillListItem = {
  id: "sk-long",
  name: `Document Summariser ${LONG}`,
  slug: `${SHA64}`,
  description: `${ARN200} ${LONG_URL}`,
  skillVersion: 128,
  isGlobal: true,
  isActive: true,
  updatedAt: "2026-09-28T09:00:00Z",
};

// ─── Skill builder ────────────────────────────────────────────────────────────

export const SKILL: Skill = {
  id: "sk-1",
  name: "Document Summariser",
  slug: "document-summariser",
  description: "Summarises long documents into a short brief for the agent.",
  content:
    "You summarise documents. Keep the brief under 200 words and cite the section headings you drew on.",
  skillVersion: 3,
  isGlobal: false,
  isActive: true,
};

export const TOOLS: ToolDef[] = [
  {
    id: "td-1",
    name: "search_docs",
    slug: "search_docs",
    description: "Full-text search over the org's knowledge base.",
    adapter: "python_fn",
    handlerRef: "astrolift.tools.search.search_docs",
    inputSchema: { type: "object", properties: { query: { type: "string" } } },
    outputSchema: {},
    createdAt: "2026-09-20T14:00:00Z",
  },
  {
    id: "td-2",
    name: "fetch_page",
    slug: "fetch_page",
    description: "",
    adapter: "http_endpoint",
    handlerRef: "https://tools.example.com/fetch",
    inputSchema: {},
    outputSchema: {},
    createdAt: "2026-09-21T09:30:00Z",
  },
  {
    id: "td-3",
    name: "ticketing",
    slug: "ticketing",
    description: "Ticket lookups over MCP.",
    adapter: "mcp_server",
    handlerRef: "",
    inputSchema: {},
    outputSchema: {},
    createdAt: "2026-09-22T11:15:00Z",
  },
];

export const LONG_TOOLS: ToolDef[] = TOOLS.map((t) => ({
  ...t,
  name: `${t.name}_${LONG}`,
  description: `${ARN200} `,
  handlerRef: t.adapter === "http_endpoint" ? LONG_URL : `${t.handlerRef || "handler"}.${SHA64}`,
}));

export const MANY_TOOLS: ToolDef[] = Array.from({ length: 40 }, (_, i) => ({
  ...TOOLS[i % 3],
  id: `td-many-${i}`,
  name: `tool_${String(i + 1).padStart(2, "0")}`,
  slug: `tool_${i + 1}`,
}));

export const SKILL_BUILDER: SkillBuilderState = {
  id: "sk-1",
  skill: SKILL,
  skillLoading: false,
  errorMessage: null,
  onRetry: noop,
  tools: TOOLS,
  toolsLoading: false,
  toolsError: null,
  onToolsRetry: noop,
  saving: false,
  deleting: false,
  aiAssisting: false,
  saveSkill: none,
  deleteSkill: noopAsync,
  aiAssist: async () => null,
};

export const SKILL_BUILDER_LONG: SkillBuilderState = {
  ...SKILL_BUILDER,
  skill: {
    ...SKILL,
    name: `Document Summariser ${LONG}`,
    slug: `document-summariser-${SHA64}`,
    description: `${ARN200}`,
    content: `${LONG_URL}\n${`${LONG}\n`.repeat(20)}`,
    isGlobal: true,
  },
  tools: LONG_TOOLS,
};

// ─── Register tool ───────────────────────────────────────────────────────────

export const ADD_TOOL: AddToolState = {
  skillId: "sk-1",
  skill: SKILL,
  skillLoading: false,
  skillError: null,
  creating: false,
  createTool: none,
};

// ─── Import ───────────────────────────────────────────────────────────────────

export const IMPORT_RESULT: ImportResult = {
  importedSkills: ["document-summariser", "release-notes", "repo-search"],
  importedTools: ["search_docs", "fetch_page"],
  sourceRef: "acme/agent-config@main",
};

export const IMPORT_SKILLS: ImportSkillsState = {
  loading: false,
  result: null,
  importSkills: none,
  clearResult: noop,
};

// ─── New skill ────────────────────────────────────────────────────────────────

export const NEW_SKILL: NewSkillState = {
  orgReady: true,
  loading: false,
  createSkill: none,
};
