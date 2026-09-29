"use client";

import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { PermissionNote } from "@/components/settings/Restricted";
import { ShellHeader } from "@/components/shell/ShellHeader";

import { workflowsCrumbs } from "./workflows-list";

export interface WorkflowInstancesScreenProps {
  /** `audit_log.read`, the grant the Temporal instances sat behind on the old Running tab. */
  canView: boolean;
  /** The permission set is still loading. */
  loading?: boolean;
  /** WorkflowInstancesPanelView behind its hook; mounted only for a viewer who may see it. */
  panel: ReactNode;
}

/**
 * Agents › Workflows › Platform instances: the Temporal instances behind
 * deploys, provisioning and drift detection, with cancel and terminate for
 * a stuck one. It was the second list on the Workflows page's Running tab;
 * rule 3 gives it its own page, reached from the Workflows `⋯` menu. Pure.
 */
export function WorkflowInstancesScreen({
  canView,
  loading = false,
  panel,
}: WorkflowInstancesScreenProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={workflowsCrumbs({ label: "Platform instances" })}
        title="Platform instances"
        context={
          <span className="text-muted-foreground text-sm">
            Temporal operations across apps and platform services
          </span>
        }
      />
      {loading ? (
        <Skeleton className="h-40 w-full rounded-md" />
      ) : canView ? (
        panel
      ) : (
        <PermissionNote permission="audit_log.read" verb="Viewing platform instances" />
      )}
    </div>
  );
}
