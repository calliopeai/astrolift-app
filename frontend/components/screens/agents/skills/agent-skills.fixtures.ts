import type { AddToolFormProps } from "./AddToolForm";
import type { ImportResult, ImportSkillsState } from "./use-import-skills";
import type { NewSkillState } from "./use-new-skill";
import type { Skill, SkillBuilderState, ToolDef } from "./use-skill-builder";
import type { SkillListItem, SkillsListState } from "./use-skills-list";

/** Hand-typed fixtures for the agent skills screens (group agent-skills). */

const noop = () => {};
const noopAsync = async () => {};
const yes = async () => true;

export const LONG =
  "customer-support-escalation-triage-and-knowledge-base-summarisation-for-the-emea-region";

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
    agentType: "claude_code",
  },
  {
    id: "sk-2",
    name: "Release Notes",
    slug: "release-notes",
    description: "",
    skillVersion: 1,
    isGlobal: false,
    isActive: false,
  },
  {
    id: "sk-3",
    name: "Repo Search",
    slug: "repo-search",
    description: "Search the connected repositories for code and docs.",
    skillVersion: 7,
    isGlobal: true,
    isActive: true,
  },
];

function listState(skills: SkillListItem[]): SkillsListState {
  return {
    loading: false,
    errorMessage: null,
    skills,
    ownSkills: skills.filter((s) => !s.isGlobal),
    globalSkills: skills.filter((s) => s.isGlobal),
  };
}

export const SKILLS_LIST_FULL: SkillsListState = listState(SKILL_ITEMS);
export const SKILLS_LIST_EMPTY: SkillsListState = listState([]);
export const SKILLS_LIST_LOADING: SkillsListState = { ...listState([]), loading: true };
export const SKILLS_LIST_ERROR: SkillsListState = {
  ...listState([]),
  errorMessage: "Response not successful: Received status code 500",
};
export const SKILLS_LIST_LONG: SkillsListState = listState(
  SKILL_ITEMS.map((s) => ({
    ...s,
    name: `${s.name} ${LONG}`,
    slug: `${s.slug}-${LONG}`,
    description: `${LONG} `.repeat(4),
    agentType: s.agentType ? `${s.agentType}_${LONG}` : undefined,
  }))
);

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

export const SKILL_BUILDER: SkillBuilderState = {
  skill: SKILL,
  tools: TOOLS,
  skillLoading: false,
  toolsLoading: false,
  errorMessage: null,
  saving: false,
  deleting: false,
  deletingTool: false,
  creatingTool: false,
  aiAssisting: false,
  saveSkill: yes,
  deleteSkill: noopAsync,
  deleteTool: noopAsync,
  createTool: yes,
  aiAssist: async () => null,
};

export const SKILL_BUILDER_LONG: SkillBuilderState = {
  ...SKILL_BUILDER,
  skill: {
    ...SKILL,
    name: `Document Summariser ${LONG}`,
    slug: `document-summariser-${LONG}`,
    description: `${LONG} `.repeat(3),
    content: `${LONG}\n`.repeat(20),
    isGlobal: true,
  },
  tools: TOOLS.map((t) => ({
    ...t,
    name: `${t.name}_${LONG}`,
    description: `${LONG} `.repeat(3),
    handlerRef: `${t.handlerRef || "handler"}.${LONG}`,
  })),
};

// ─── Add tool form ────────────────────────────────────────────────────────────

export const ADD_TOOL_FORM: AddToolFormProps = {
  onSubmit: yes,
  loading: false,
  onDone: noop,
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
  importSkills: noopAsync,
  clearResult: noop,
  viewSkills: noop,
};

// ─── New skill ────────────────────────────────────────────────────────────────

export const NEW_SKILL: NewSkillState = {
  orgReady: true,
  loading: false,
  createSkill: noopAsync,
};
