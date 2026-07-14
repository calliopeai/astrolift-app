"use client";

import { useQuery } from "@apollo/client/react";
import {
  BookOpenIcon,
  BoxIcon,
  FileCodeIcon,
  GitBranchIcon,
  PackageIcon,
  WrenchIcon,
} from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_AGENT_DETAIL } from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentDetail,
  AstroliftAgentListItem,
  AstroliftAgentSkill,
  AstroliftToolDef,
} from "@/graphql/agents/agents.types";
import { GET_WORKLOAD, LIST_CONTAINERS } from "@/graphql/registry/registry.queries";
import type { AstroliftContainer, AstroliftWorkload } from "@/graphql/registry/registry.types";
import { formatRelativeAge } from "@/lib/format";

interface WorkloadResp {
  astroliftWorkload: AstroliftWorkload | null;
}
interface ContainersResp {
  astroliftContainers: AstroliftContainer[];
}
interface AgentDetailResp {
  agent: AstroliftAgentDetail | null;
}

// Tool-adapter display labels, kept in sync with the skill/tool registry
// surfaces (`/agents/tools`, `/agents/skills/[id]`). Free `String!` on the
// schema, so unknown adapters fall through to the raw value.
const ADAPTER_LABELS: Record<string, string> = {
  python_fn: "Python function",
  http_endpoint: "HTTP endpoint",
  mcp_server: "MCP server",
};

function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return "{}";
  }
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs tracking-wide uppercase">{label}</dt>
      <dd className={mono ? "font-mono text-sm break-all" : "text-sm"}>{value}</dd>
    </div>
  );
}

/**
 * Build tab content for an agent (spec 33 PR-9 / spec 38 Phase 4).
 *
 * Composed from two reads:
 *   - container image      ← `GET_WORKLOAD` + `LIST_CONTAINERS` (`imageRef`)
 *   - manifest preview     ← link into the app's workload manifest route
 *   - source / repo        ← the resolved fleet row (`sourceRepo`/`sourceUrl`)
 *   - brief / skills / tools ← `GET_AGENT_DETAIL` (the `agent(orgId, slug)` join)
 *
 * The brief, the agent's ordered skill bindings, and each skill's tool
 * definitions now come from `agent(slug)` — the read-side join that bundles
 * them onto a single agent. The brief is nullable (an agent may not have an
 * assembled brief yet) and the skill list may be empty; both render calm
 * empty states rather than an error. (Before the resolver landed these were
 * "backend join pending" placeholders — that gap is closed.)
 */
export function BuildContent({
  agent,
  orgId,
}: {
  agent: AstroliftAgentListItem;
  orgId: string;
}) {
  const { data: wlData, loading: wlLoading } = useQuery<WorkloadResp>(GET_WORKLOAD, {
    variables: { appSlug: agent.appSlug, slug: agent.slug },
    fetchPolicy: "cache-and-network",
  });
  const { data: cData, loading: cLoading } = useQuery<ContainersResp>(LIST_CONTAINERS, {
    variables: { workloadSlug: agent.slug },
    fetchPolicy: "cache-and-network",
  });
  const {
    data: adData,
    loading: adLoading,
    error: adError,
  } = useQuery<AgentDetailResp>(GET_AGENT_DETAIL, {
    variables: { orgId, slug: agent.slug },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });

  const workload = wlData?.astroliftWorkload ?? null;
  const containers = cData?.astroliftContainers ?? [];
  const primary = containers.find((c) => c.isPrimary) ?? containers[0] ?? null;

  const detail = adData?.agent ?? null;
  const detailPending = adLoading && !detail;
  const brief = detail?.brief ?? null;
  const skills = [...(detail?.skills ?? [])].sort((a, b) => a.position - b.position);

  return (
    <div className="space-y-5">
      {/* Source / repo — from the resolved agent row. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <GitBranchIcon className="size-4" />
            Source
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
          <Field
            label="Repository"
            mono
            value={
              agent.sourceRepo ? (
                agent.sourceUrl ? (
                  <a
                    href={agent.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="hover:text-foreground inline-flex items-center gap-1 hover:underline"
                  >
                    {agent.sourceRepo}
                  </a>
                ) : (
                  agent.sourceRepo
                )
              ) : (
                "—"
              )
            }
          />
          <Field label="App" mono value={`${agent.projectSlug}/${agent.appSlug}`} />
        </CardContent>
      </Card>

      {/* Container image — from GET_WORKLOAD + LIST_CONTAINERS. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <PackageIcon className="size-4" />
            Image
          </CardTitle>
        </CardHeader>
        <CardContent>
          {cLoading && containers.length === 0 ? (
            <Skeleton className="h-16 w-full" />
          ) : primary ? (
            <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
              <Field label="Image ref" mono value={primary.imageRef || "—"} />
              <Field
                label="Dockerfile"
                mono
                value={primary.dockerfilePath || "—"}
              />
              <Field label="Build context" mono value={primary.buildContext || "—"} />
              <Field
                label="Container"
                mono
                value={
                  <span className="inline-flex items-center gap-1.5">
                    {primary.name}
                    {primary.isPrimary && (
                      <Badge variant="secondary" className="text-xs">
                        primary
                      </Badge>
                    )}
                  </span>
                }
              />
            </dl>
          ) : (
            <p className="text-muted-foreground text-sm">
              No container is registered for this agent yet.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Manifest — deep-link into the app's workload manifest preview, which
          renders the platform-computed K8s resources + image-tag diff. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <FileCodeIcon className="size-4" />
            Manifest
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-muted-foreground text-sm">
            {wlLoading && !workload ? (
              <Skeleton className="h-4 w-48" />
            ) : workload ? (
              <>
                <span className="font-mono">{workload.kind}</span> workload declared in{" "}
                <span className="font-mono">astrolift.toml</span>.
              </>
            ) : (
              "Manifest preview is available on the app's workload page."
            )}
          </div>
          <Link
            href={`/apps/${encodeURIComponent(agent.appSlug)}/workloads/${encodeURIComponent(agent.slug)}`}
            className="inline-flex items-center gap-1 text-sm text-[var(--brand-primary)] hover:underline"
          >
            <BoxIcon className="size-4" />
            Open workload manifest
          </Link>
        </CardContent>
      </Card>

      {/* Brief — the assembled brief for this agent, from `agent(slug)`. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <BookOpenIcon className="size-4" />
            Brief
          </CardTitle>
        </CardHeader>
        <CardContent>
          {detailPending ? (
            <Skeleton className="h-24 w-full" />
          ) : adError ? (
            <p className="text-muted-foreground text-sm">
              The brief could not be loaded right now.
            </p>
          ) : brief ? (
            <div className="space-y-3">
              <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
                <Field label="Content hash" mono value={brief.contentHash || "—"} />
                <Field
                  label="Assembled"
                  value={
                    brief.createdAt ? (
                      <span title={brief.createdAt}>{formatRelativeAge(brief.createdAt)}</span>
                    ) : (
                      "—"
                    )
                  }
                />
              </dl>
              <div>
                <p className="text-muted-foreground mb-1 text-xs tracking-wide uppercase">
                  Configuration
                </p>
                <pre className="bg-muted text-muted-foreground max-h-80 overflow-auto rounded-md p-3 font-mono text-xs">
                  {prettyJson(brief.config)}
                </pre>
              </div>
            </div>
          ) : (
            <EmptyState
              icon={<BookOpenIcon className="size-5" />}
              title="No brief defined"
              description="This agent has no assembled brief yet. A brief is composed when the agent's skills are bound and its system prompt is generated."
            />
          )}
        </CardContent>
      </Card>

      {/* Skills & tools — the agent's ordered skill bindings and each skill's
          tool definitions, from `agent(slug)`. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <WrenchIcon className="size-4" />
            Skills &amp; tools
            {skills.length > 0 && (
              <Badge variant="outline" className="text-muted-foreground ml-1 text-xs">
                {skills.length}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {detailPending ? (
            <Skeleton className="h-24 w-full" />
          ) : adError ? (
            <p className="text-muted-foreground text-sm">
              Skills could not be loaded right now.
            </p>
          ) : skills.length > 0 ? (
            <div className="divide-y">
              {skills.map((binding) => (
                <SkillBlock key={binding.skill.id} binding={binding} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<WrenchIcon className="size-5" />}
              title="No skills attached"
              description="This agent has no skills bound to it yet. Skills and their tool definitions are managed in the org-wide registries."
              secondary={
                <div className="flex flex-wrap justify-center gap-3">
                  <Link
                    href="/agents/skills"
                    className="inline-flex items-center gap-1 text-sm text-[var(--brand-primary)] hover:underline"
                  >
                    Skill registry
                  </Link>
                  <Link
                    href="/agents/tools"
                    className="inline-flex items-center gap-1 text-sm text-[var(--brand-primary)] hover:underline"
                  >
                    Tool registry
                  </Link>
                </div>
              }
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * One bound skill on the agent: its identity (name · slug · version, with
 * Global/Inactive badges) plus its nested tool definitions. Mirrors the
 * skill-detail / tool-registry presentation so a skill reads the same wherever
 * it appears.
 */
function SkillBlock({ binding }: { binding: AstroliftAgentSkill }) {
  const { skill, toolDefs } = binding;
  return (
    <div className="space-y-3 py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <Link
          href={`/agents/skills/${skill.id}`}
          className="font-medium hover:underline"
        >
          {skill.name}
        </Link>
        <span className="text-muted-foreground font-mono text-xs">{skill.slug}</span>
        <span className="text-muted-foreground text-xs">v{skill.skillVersion}</span>
        {skill.isGlobal && (
          <Badge variant="outline" className="text-xs">
            Global
          </Badge>
        )}
        {!skill.isActive && (
          <Badge variant="secondary" className="text-xs">
            Inactive
          </Badge>
        )}
      </div>
      {skill.description && (
        <p className="text-muted-foreground text-sm leading-relaxed">
          {skill.description}
        </p>
      )}
      {toolDefs.length > 0 ? (
        <div className="flex flex-col gap-2">
          {toolDefs.map((tool) => (
            <ToolRow key={tool.id} tool={tool} />
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground text-xs">No tools registered for this skill.</p>
      )}
    </div>
  );
}

/**
 * A single tool definition row under a skill — name + description, adapter
 * badge, and handler ref. Matches the tool-registry row shape.
 */
function ToolRow({ tool }: { tool: AstroliftToolDef }) {
  return (
    <div className="flex items-center gap-4 text-sm">
      <WrenchIcon className="text-muted-foreground size-4 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="font-medium">{tool.name}</span>
        {tool.description && (
          <span className="text-muted-foreground truncate text-xs">{tool.description}</span>
        )}
      </div>
      <Badge variant="secondary" className="shrink-0 text-xs">
        {ADAPTER_LABELS[tool.adapter] ?? tool.adapter}
      </Badge>
      {tool.handlerRef && (
        <span className="text-muted-foreground hidden max-w-[180px] shrink-0 truncate font-mono text-xs sm:inline">
          {tool.handlerRef}
        </span>
      )}
    </div>
  );
}
