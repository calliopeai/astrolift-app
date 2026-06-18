"use client";

import { useQuery } from "@apollo/client/react";
import {
  BookOpenIcon,
  BoxIcon,
  FileCodeIcon,
  GitBranchIcon,
  InfoIcon,
  PackageIcon,
  WrenchIcon,
} from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import { GET_WORKLOAD, LIST_CONTAINERS } from "@/graphql/registry/registry.queries";
import type { AstroliftContainer, AstroliftWorkload } from "@/graphql/registry/registry.types";

interface WorkloadResp {
  astroliftWorkload: AstroliftWorkload | null;
}
interface ContainersResp {
  astroliftContainers: AstroliftContainer[];
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
 * Build tab content for an agent (spec 33 PR-9).
 *
 * Composed from existing queries:
 *   - repo / source       ← the resolved fleet row (`sourceRepo`/`sourceUrl`)
 *   - container image      ← `GET_WORKLOAD` + `LIST_CONTAINERS` (`imageRef`)
 *   - manifest preview     ← link into the app's workload manifest route
 *
 * The brief / attached skills / tools are NOT resolvable from existing
 * queries — there is no agent→brief or agent→skill join, and `LIST_SKILLS` /
 * `LIST_TOOL_DEFS` are org/skill-scoped, not agent-scoped. Rather than fake an
 * association, those sections render an explicit "backend join pending" notice
 * pointing at the deferred `agent(slug)` resolver (spec 33 PR-9 net-new).
 */
export function BuildContent({ agent }: { agent: AstroliftAgentListItem }) {
  const { data: wlData, loading: wlLoading } = useQuery<WorkloadResp>(GET_WORKLOAD, {
    variables: { appSlug: agent.appSlug, slug: agent.slug },
    fetchPolicy: "cache-and-network",
  });
  const { data: cData, loading: cLoading } = useQuery<ContainersResp>(LIST_CONTAINERS, {
    variables: { workloadSlug: agent.slug },
    fetchPolicy: "cache-and-network",
  });

  const workload = wlData?.astroliftWorkload ?? null;
  const containers = cData?.astroliftContainers ?? [];
  const primary = containers.find((c) => c.isPrimary) ?? containers[0] ?? null;

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

      {/* Brief / Skills / Tools — backend join gap (spec 33 PR-9). These have
          no agent-scoped read query; surfaced as a pending notice, not faked. */}
      <BackendJoinPending
        icon={<BookOpenIcon className="size-4" />}
        title="Brief"
        body="The assembled brief for this agent (system prompt + skill bindings) isn't reachable yet — there's no agent→brief join in the API. It lands with the agent(slug) resolver."
      />
      <BackendJoinPending
        icon={<WrenchIcon className="size-4" />}
        title="Skills & tools"
        body="The skills attached to this agent and their tool definitions aren't reachable per-agent yet (the skill and tool registries are org-scoped, not agent-scoped). Browse the org-wide registries below until the agent(slug) join ships."
        links={[
          { href: "/agents/skills", label: "Skill registry" },
          { href: "/agents/tools", label: "Tool registry" },
        ]}
      />
    </div>
  );
}

/**
 * A neutral "this section needs a backend join we don't have yet" card. Used
 * for the agent Build tab's brief/skills/tools, which can't be agent-scoped
 * from existing queries (spec 33 PR-9 flags `agent(slug)` as net-new backend
 * work). Deliberately not an error state — the agent is fine, the read path
 * just isn't wired.
 */
function BackendJoinPending({
  icon,
  title,
  body,
  links,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  links?: Array<{ href: string; label: string }>;
}) {
  return (
    <Card className="border-dashed">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          {icon}
          {title}
          <Badge variant="outline" className="text-muted-foreground ml-1 gap-1 text-[10px]">
            <InfoIcon className="size-3" />
            Backend join pending
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-sm leading-relaxed">{body}</p>
        {links && links.length > 0 && (
          <div className="flex flex-wrap gap-3">
            {links.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className="inline-flex items-center gap-1 text-sm text-[var(--brand-primary)] hover:underline"
              >
                {l.label}
              </Link>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
