import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { agentToolsList } from "@/components/screens/agents/detail/agent-skills-list";
import en from "@/messages/en.json";

import type { CopiedListDefinition } from "./list-state";

/**
 * List labels are translated (#2381): a declaration names its copy, the copy
 * exists for every label it declares (key parity carries it to every
 * locale), and no list hook is handed a declaration nobody translates.
 */
const frontend = path.resolve(__dirname, "../..");
const catalog = (en as { listDefinitions: Record<string, Copy> }).listDefinitions;

interface Copy {
  search?: string;
  fields?: Record<string, string>;
  options?: Record<string, Record<string, string>>;
  views: Record<string, string>;
  notes?: Record<string, string>;
}

const modules = import.meta.glob<Record<string, unknown>>([
  "../screens/**/*.ts",
  "!../screens/**/*.test.ts",
]);

async function declarations(): Promise<CopiedListDefinition[]> {
  const found: CopiedListDefinition[] = [
    agentToolsList(["python_fn", "http_endpoint", "mcp_server"]),
  ];
  for (const [file, load] of Object.entries(modules)) {
    if (!readFileSync(path.resolve(__dirname, file), "utf8").includes('copy: "')) continue;
    for (const value of Object.values(await load())) {
      const def = value as Partial<CopiedListDefinition> | null;
      if (def && typeof def === "object" && typeof def.copy === "string" && def.fields) {
        found.push(def as CopiedListDefinition);
      }
    }
  }
  return found;
}

/** The copy a declaration needs, as the keys `useLocalizedList` reads. */
function needed(def: CopiedListDefinition): string[] {
  return [
    ...(def.searchable === false ? [] : ["search"]),
    ...def.fields.map((f) => `fields.${f.key}`),
    ...def.views.map((v) => `views.${v.key}`),
    ...def.views.filter((v) => v.note !== undefined).map((v) => `notes.${v.key}`),
  ].sort();
}

function present(copy: Copy): string[] {
  return [
    ...(copy.search === undefined ? [] : ["search"]),
    ...Object.keys(copy.fields ?? {}).map((k) => `fields.${k}`),
    ...Object.keys(copy.views).map((k) => `views.${k}`),
    ...Object.keys(copy.notes ?? {}).map((k) => `notes.${k}`),
  ].sort();
}

describe("list declarations", () => {
  it("each has its copy, and the catalog holds no copy for a list that is gone", async () => {
    const defs = await declarations();
    const copies = new Set(defs.map((d) => d.copy));
    expect(defs.length).toBeGreaterThan(30);
    expect(Object.keys(catalog).filter((key) => !copies.has(key))).toEqual([]);
    for (const def of defs) {
      const copy = catalog[def.copy];
      expect(copy, def.copy).toBeDefined();
      expect(present(copy), def.copy).toEqual(needed(def));
      // Option copy only for options the declaration has.
      for (const [field, options] of Object.entries(copy.options ?? {})) {
        const declared = def.fields.find((f) => f.key === field)?.options?.map((o) => o.value);
        expect(declared, `${def.copy}.options.${field}`).toBeDefined();
        expect(Object.keys(options).filter((v) => !declared!.includes(v))).toEqual([]);
      }
    }
  });
});

/**
 * Hooks that hand a list its bare declaration because the screen translates
 * the labels as it renders (`{ ...list, definition: … }`). Every other list
 * hook takes `useLocalizedList(DECLARATION)` or a definition built from
 * translated copy.
 */
const LOCALIZED_BY_SCREEN: Record<string, string[]> = {
  "components/screens/apps/domains/use-app-domains.ts": ["APP_DOMAINS_LIST"], // DomainsScreen
  "components/screens/apps/overview/use-teams-card.ts": ["APP_TEAM_ACCESS_LIST"], // TeamsCard
  "components/screens/apps/secrets/use-app-secrets.ts": [
    "APP_SECRETS_LIST", // SecretsScreen
    "APP_SECRET_BUNDLES_LIST",
  ],
  "components/screens/apps/secrets/use-deploy-tokens.ts": ["APP_DEPLOY_TOKENS_LIST"], // DeployTokensScreen
  "components/screens/apps/security/use-app-previews.ts": ["APP_PREVIEWS_LIST"], // AppPreviewsScreen
  "components/screens/approvals/use-secret-proposals-queue.ts": ["SECRET_PROPOSALS_LIST"], // SecretProposalsQueue
  "components/screens/members/use-members.ts": ["PEOPLE_LIST"], // MembersScreen
  "components/screens/metrics/use-metrics.ts": ["METRICS_APPS_LIST"], // MetricsScreen
  "components/screens/pipelines/use-pipeline-secrets.ts": ["PIPELINE_SECRETS_LIST"], // PipelineSecrets
  "components/screens/pipelines/use-pipelines.ts": ["PIPELINES_LIST", "PIPELINE_RUNS_LIST"], // PipelinesScreen
};

function sources(dir: string): string[] {
  return readdirSync(path.join(frontend, dir), { withFileTypes: true }).flatMap((entry) => {
    const rel = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "node_modules" ? [] : sources(rel);
    return /\.tsx?$/.test(entry.name) && !/\.(test|stories)\.tsx?$/.test(entry.name) ? [rel] : [];
  });
}

describe("list hooks", () => {
  it("are never handed a declaration whose labels nobody translates", () => {
    const bare: Record<string, string[]> = {};
    for (const file of [...sources("components"), ...sources("app")]) {
      const text = readFileSync(path.join(frontend, file), "utf8");
      for (const m of text.matchAll(/use(?:Local)?ListState\(\s*([A-Z][A-Z0-9_]*)\s*[,)]/g)) {
        (bare[file.split(path.sep).join("/")] ??= []).push(m[1]!);
      }
    }
    expect(bare).toEqual(LOCALIZED_BY_SCREEN);
  });
});
