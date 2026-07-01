"use client";

import { useQuery } from "@apollo/client/react";
import {
  ChartLineIcon,
  ChevronRightIcon,
  RocketIcon,
  SettingsIcon,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

interface Props {
  appSlug: string;
}

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

interface QuickLinkCardProps {
  href: string;
  icon: LucideIcon;
  title: string;
  description: string;
  chip?: React.ReactNode;
}

function QuickLinkCard({ href, icon: Icon, title, description, chip }: QuickLinkCardProps) {
  return (
    <Link
      href={href}
      className={cn(
        "group hover:border-primary/40 hover:bg-accent/30 focus-visible:ring-ring/50",
        "rounded-xl outline-none transition-colors focus-visible:ring-3",
      )}
    >
      <Card className="hover:border-primary/40 h-full transition-colors">
        <CardContent className="flex items-start gap-3 p-4">
          <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
            <Icon className="size-5" />
          </div>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex items-center gap-2">
              <p className="text-foreground text-sm font-semibold">{title}</p>
              {chip}
            </div>
            <p className="text-muted-foreground text-xs leading-snug">{description}</p>
          </div>
          <ChevronRightIcon
            aria-hidden
            className="text-muted-foreground mt-1 size-4 shrink-0 opacity-0 transition-all group-hover:translate-x-0.5 group-hover:opacity-100"
          />
        </CardContent>
      </Card>
    </Link>
  );
}

/**
 * Bottom-of-page quick-link grid for the app overview (#408). Three
 * cards point to the highest-traffic sub-tabs (observability,
 * deployments, settings) and cut a navigation click. Deployments
 * card shows a live count chip from the existing
 * `LIST_DEPLOYMENTS` query.
 */
export function QuickLinksGrid({ appSlug }: Props) {
  // No environmentName / limit: we want the full count for this app.
  // The query is cached, so the deployments tab benefits from the
  // warmed cache when the operator clicks through.
  const { data, loading } = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });

  const count = data?.astroliftDeployments?.length ?? 0;
  const chip = loading && count === 0 ? null : (
    <Badge variant="secondary" className="font-mono text-2xs">
      {count} {count === 1 ? "deployment" : "deployments"}
    </Badge>
  );

  return (
    <div className="grid gap-3 sm:grid-cols-1 md:grid-cols-3">
      <QuickLinkCard
        href={`/apps/${appSlug}/observability`}
        icon={ChartLineIcon}
        title="Observability"
        description="Metrics, logs, and traces for this app's workloads."
      />
      <QuickLinkCard
        href={`/apps/${appSlug}/deployments`}
        icon={RocketIcon}
        title="Deployments"
        description="Recent deploys, in-flight rollouts, and rollback history."
        chip={chip ?? undefined}
      />
      <QuickLinkCard
        href={`/apps/${appSlug}/settings`}
        icon={SettingsIcon}
        title="Settings"
        description="Project assignment, deploy strategy, manifest, and teams."
      />
    </div>
  );
}
