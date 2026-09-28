"use client";

import { ArchiveIcon, Loader2Icon, PlayCircleIcon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useArchiveApp } from "./use-archive-app";

export type ArchiveAppViewProps = ReturnType<typeof useArchiveApp> & {
  appName: string;
  isArchived: boolean;
  archivedAt: string | null;
};

/**
 * Archive / restore: a reversible alternative to deregister that scales
 * every workload to zero and suppresses deploys.
 */
export function ArchiveAppView({
  archiving,
  restoring,
  onArchive,
  onRestore,
  appName,
  isArchived,
  archivedAt,
}: ArchiveAppViewProps) {
  const fmt = useFormatters();
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  async function handleArchive() {
    if (await onArchive()) setConfirmOpen(false);
  }

  return (
    <Section
      title="App archive"
      description={
        isArchived
          ? archivedAt
            ? `Archived ${fmt.formatRelativeTime(archivedAt)}. Workloads are at zero replicas; deploys are suppressed.`
            : "App is archived. Workloads are at zero replicas."
          : "Archiving scales all workloads to zero and suppresses deploys. Restore returns replicas to their pre-archive counts."
      }
      action={
        <>
          {isArchived ? (
            <Badge
              variant="outline"
              className="border-warning-border bg-warning/10 text-warning-fg"
            >
              Archived
            </Badge>
          ) : null}
          <Can permission="app.update">
            {isArchived ? (
              <Button
                size="sm"
                variant="default"
                disabled={restoring}
                onClick={() => void onRestore()}
              >
                {restoring ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <PlayCircleIcon className="size-3.5" />
                )}
                Restore
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                disabled={archiving}
                onClick={() => setConfirmOpen(true)}
              >
                {archiving ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <ArchiveIcon className="size-3.5" />
                )}
                Archive
              </Button>
            )}
          </Can>
        </>
      }
    >
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Archive {appName}?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="text-muted-foreground space-y-2 text-sm">
                <p>
                  Archive is a reversible alternative to deregister. It scales every workload to{" "}
                  <span className="text-foreground font-mono">replicas=0</span>, suppresses webhook
                  + scheduled deploys, and releases the load balancer capacity.
                </p>
                <p>
                  Your manifest, secrets, deploy tokens, managed services, and domain bindings are
                  preserved. Restoring returns each workload to its pre-archive replica count.
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={archiving}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={archiving}
              onClick={(e) => {
                e.preventDefault();
                void handleArchive();
              }}
            >
              {archiving ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <ArchiveIcon className="size-4" />
              )}
              Archive {appName}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Section>
  );
}
