"use client";

import { BookOpenIcon, WrenchIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Badge } from "@/components/ui/badge";
import type { AstroliftAgentSkill } from "@/graphql/agents/agents.types";

import { ADAPTER_LABELS, type AgentToolRow } from "./agent-skills-list";

interface ListProps<T> {
  list: ListStateController;
  rows: T[];
  totalCount: number;
  loading: boolean;
  error: { message: string } | null;
  onRetry: () => void;
}

export type AgentSkillsListProps = ListProps<AstroliftAgentSkill>;
export type AgentToolsListProps = ListProps<AgentToolRow>;

type Translate = ReturnType<typeof useTranslations<"agentSkillsTab">>;

const skillColumns = (t: Translate): Column<AstroliftAgentSkill>[] => [
  {
    id: "name",
    header: t("skill"),
    sortKey: "name",
    cell: (b) => (
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate font-medium">{b.skill.name}</span>
        {b.skill.description && (
          <span className="text-muted-foreground truncate text-xs">{b.skill.description}</span>
        )}
      </span>
    ),
  },
  {
    id: "slug",
    header: t("slug"),
    cellClassName: "text-muted-foreground font-mono text-xs",
    cell: (b) => <span className="block max-w-64 truncate">{b.skill.slug}</span>,
  },
  {
    id: "version",
    header: t("version"),
    width: "w-24",
    cellClassName: "text-muted-foreground font-mono text-xs",
    cell: (b) => `v${b.skill.skillVersion}`,
  },
  {
    id: "tools",
    header: t("tools"),
    sortKey: "tools",
    width: "w-20",
    align: "right",
    cellClassName: "font-mono text-xs tabular-nums",
    cell: (b) => b.toolDefs.length,
  },
  {
    id: "status",
    header: t("status"),
    width: "w-36",
    cell: (b) => (
      <span className="flex flex-wrap gap-1">
        {b.skill.isGlobal && (
          <Badge variant="outline" className="text-xs">
            {t("global")}
          </Badge>
        )}
        <Badge variant={b.skill.isActive ? "secondary" : "outline"} className="text-xs">
          {b.skill.isActive ? t("active") : t("inactive")}
        </Badge>
      </span>
    ),
  },
  {
    id: "position",
    header: t("order"),
    sortKey: "position",
    width: "w-20",
    align: "right",
    cellClassName: "text-muted-foreground font-mono text-xs tabular-nums",
    cell: (b) => b.position,
  },
];

const toolColumns = (
  t: Translate,
  adapters: ReturnType<typeof useTranslations<"agentToolRegistry">>
): Column<AgentToolRow>[] => [
  {
    id: "name",
    header: t("tool"),
    sortKey: "name",
    cell: (r) => (
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate font-medium">{r.tool.name}</span>
        {r.tool.description && (
          <span className="text-muted-foreground truncate text-xs">{r.tool.description}</span>
        )}
      </span>
    ),
  },
  {
    id: "skill",
    header: t("skill"),
    sortKey: "skill",
    cell: (r) => <span className="block max-w-48 truncate">{r.skill.name}</span>,
  },
  {
    id: "adapter",
    header: t("adapter"),
    sortKey: "adapter",
    width: "w-36",
    cell: (r) => (
      <Badge variant="secondary" className="text-xs">
        {ADAPTER_LABELS[r.tool.adapter] ? adapters(r.tool.adapter) : r.tool.adapter}
      </Badge>
    ),
  },
  {
    id: "handler",
    header: t("handler"),
    cellClassName: "text-muted-foreground font-mono text-xs",
    cell: (r) =>
      r.tool.handlerRef ? (
        <span className="block max-w-64 truncate" title={r.tool.handlerRef}>
          {r.tool.handlerRef}
        </span>
      ) : (
        t("notSet")
      ),
  },
];

/**
 * Skills & tools › Skills (spec 44 §5.2): the skills bound to this agent, in
 * binding order, on the embedded list. The whole row opens the skill in the
 * registry. Pure.
 */
export function AgentSkillsList({
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
}: AgentSkillsListProps) {
  const t = useTranslations("agentSkillsTab");
  return (
    <ListPage<AstroliftAgentSkill>
      embedded
      list={list}
      label={t("skills")}
      columns={skillColumns(t)}
      rows={rows}
      getRowId={(b) => b.skill.id}
      rowHref={(b) => `/agents/skills/${b.skill.id}`}
      loading={loading}
      error={error}
      onRetry={onRetry}
      totalCount={totalCount}
      empty={{
        icon: <BookOpenIcon className="size-5" />,
        title: t("noSkills"),
        description: t("noSkillsDescription"),
        actionHref: "/agents/skills",
        actionLabel: t("skillRegistry"),
      }}
    />
  );
}

/**
 * Skills & tools › Tools: every tool the agent's skills carry, with the
 * skill it comes from, on the embedded list. The whole row opens the tool in
 * the registry. Pure.
 */
export function AgentToolsList({
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
}: AgentToolsListProps) {
  const t = useTranslations("agentSkillsTab");
  const adapters = useTranslations("agentToolRegistry");
  return (
    <ListPage<AgentToolRow>
      embedded
      list={list}
      label={t("tools")}
      columns={toolColumns(t, adapters)}
      rows={rows}
      getRowId={(r) => `${r.skill.id}:${r.tool.id}`}
      rowHref={(r) => `/agents/tools/${r.tool.id}`}
      loading={loading}
      error={error}
      onRetry={onRetry}
      totalCount={totalCount}
      empty={{
        icon: <WrenchIcon className="size-5" />,
        title: t("noTools"),
        description: t("noToolsDescription"),
        actionHref: "/agents/tools",
        actionLabel: t("toolRegistry"),
      }}
    />
  );
}
