"use client";

import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { FolderIcon } from "lucide-react";

import { ListSummary } from "@/components/list/ListSummary";
import {
  EntityAccessPanel,
  type EntityAccessPanelProps,
} from "@/components/screens/administration/access/EntityAccessPanel";
import type { AstroliftProject } from "@/graphql/identity/identity.types";

export interface TeamAccessPanelProps {
  slug: string;
  access: EntityAccessPanelProps;
  reach: {
    projects: AstroliftProject[];
    loading: boolean;
    error: { message: string } | null;
    onRetry: () => void;
  };
}

/**
 * A team's Access tab (access UX design 3.2, 3.3): who has access on the
 * team and why, from `astroliftAccessOn`: grants on the team and the org
 * grants that reach it, group grants and mappings, each removable at its
 * source. What those grants reach, the team's projects and their apps, is a
 * summary beside it (Leo's list rule 3). Pure.
 */
export function TeamAccessPanel({ slug, access, reach }: TeamAccessPanelProps) {
  const t = useTranslations("teams.access");
  return (
    <div className="grid min-w-0 grid-cols-12 items-start gap-6">
      <div className="col-span-12 min-w-0 xl:col-span-8">
        <EntityAccessPanel {...access} />
      </div>
      <div className="col-span-12 min-w-0 xl:col-span-4">
        {reach.error && reach.projects.length > 0 ? (
          <div role="alert" className="text-danger-fg mb-3 text-sm">
            <p className="[overflow-wrap:anywhere]">{reach.error.message}</p>
            <Button variant="outline" size="sm" onClick={reach.onRetry}>
              {t("retry")}
            </Button>
          </div>
        ) : null}
        <ListSummary<AstroliftProject>
          title={t("reaches")}
          icon={<FolderIcon className="size-4" />}
          description={t("description")}
          count={reach.loading || reach.error ? null : reach.projects.length}
          rows={reach.projects}
          keyOf={(p) => p.id}
          renderRow={(p) => (
            <div className="min-w-0">
              <div className="truncate text-sm" title={p.name}>
                {p.name}
              </div>
              <div className="text-muted-foreground truncate font-mono text-xs" title={p.slug}>
                {slug}/{p.slug}
              </div>
            </div>
          )}
          viewAllHref={`/administration/projects?q=${encodeURIComponent(slug)}`}
          loading={reach.loading}
          error={reach.error}
          onRetry={reach.onRetry}
          empty={{
            icon: <FolderIcon className="size-5" />,
            title: t("emptyTitle"),
            description: t("emptyDescription"),
          }}
        />
      </div>
    </div>
  );
}
