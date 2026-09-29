"use client";

import { FolderIcon } from "lucide-react";

import { ListSummary } from "@/components/list/ListSummary";
import {
  PrincipalAccessPanel,
  type PrincipalAccessPanelProps,
} from "@/components/screens/administration/access/PrincipalAccessPanel";
import type { AstroliftProject } from "@/graphql/identity/identity.types";

export interface TeamAccessPanelProps {
  slug: string;
  access: PrincipalAccessPanelProps;
  reach: {
    projects: AstroliftProject[];
    loading: boolean;
    error: { message: string } | null;
    onRetry: () => void;
  };
}

/**
 * A team's Access tab (access UX design 3.2): "what does being on payments
 * give you". The grants held at the team (every holder, removable at their
 * source) are the list; what those grants reach, the team's projects and
 * their apps, is a summary beside it (Leo's list rule 3). Pure.
 */
export function TeamAccessPanel({ slug, access, reach }: TeamAccessPanelProps) {
  return (
    <div className="grid min-w-0 grid-cols-12 items-start gap-6">
      <div className="col-span-12 min-w-0 xl:col-span-8">
        <PrincipalAccessPanel {...access} showHolder />
      </div>
      <ListSummary<AstroliftProject>
        span={4}
        title="Reaches"
        icon={<FolderIcon className="size-4" />}
        description="A grant at this team applies to these projects and every app in them."
        count={reach.projects.length}
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
          title: "No projects yet",
          description: "A grant here still covers projects created under the team later.",
        }}
      />
    </div>
  );
}
