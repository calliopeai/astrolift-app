"use client";

import { ArrowUpCircleIcon, BoxesIcon, DatabaseIcon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Panel, PanelGrid } from "@/components/panel/Panel";
import { PermissionNote } from "@/components/settings/Restricted";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PILL_TONE } from "@/lib/status-tones";
import { cn } from "@/lib/utils";

import { resourceInFlight, resourceTone } from "../brain-status-model";
import type { BrainStatusResult } from "../BrainStatusCell";
import { BrainSubstrateForm, type SubstrateServerError } from "../BrainSubstrateForm";
import { substrateProblems, type Persistence, type SubstrateDraft } from "../substrate-cells";
import type { BrainSubstrate } from "./brain-detail-model";
import { type EnvironmentChoice, EnvironmentPicker } from "./EnvironmentPicker";

export interface BrainSubstrateTabProps {
  /** The cell last applied, from the layer itself (no host reads). */
  local: BrainSubstrate | null;
  /** The status read: adoption and graduation come with it. */
  status: BrainStatusResult;
  environment: string;
  environments: EnvironmentChoice[];
  onRetry: () => void;
  /**
   * The permission graduation still needs here (it commits and deploys, so
   * it takes `app.update` and `app.deploy`), or null when the viewer has both.
   */
  graduateNeeds: "app.update" | "app.deploy" | null;
  graduation: {
    open: boolean;
    onOpen: () => void;
    onClose: () => void;
    draft: SubstrateDraft;
    onChange: (next: SubstrateDraft) => void;
    /** Persistence the layer holds data on: it stays attached. */
    locked: Persistence[];
    submitting: boolean;
    onSubmit: () => void;
    error: SubstrateServerError | null;
  };
}

/**
 * Substrate (#2424, #2427): Mimwell's cell for this layer, what Astrolift
 * attaches for it and each resource's state, and whether the layer has
 * adopted a non-files authority yet (its own cutover does that). When
 * Mimwell reports the cell outgrown (`substrate.graduation.needed`), the
 * tab offers graduation: the new cell, committed and applied, deployed by
 * its push. The cell shows from the layer while the status read, which
 * goes to the source host, is on its way. Pure.
 */
export function BrainSubstrateTab({
  local,
  status,
  environment,
  environments,
  onRetry,
  graduateNeeds,
  graduation,
}: BrainSubstrateTabProps) {
  const t = useTranslations("brains.substrateTab");
  const read = status.state === "ready" ? status.status : null;
  // The status read's substrate knows adoption and graduation; until it
  // answers (or when it fails) the layer's own record still names the cell.
  const substrate = read?.substrate ?? local;
  const statusError = status.state === "error" ? status.message : null;
  const graduationNeeded = read?.substrate.graduationNeeded === true;
  const blocked = substrateProblems(graduation.draft).length > 0;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <EnvironmentPicker environments={environments} />
      {graduationNeeded && (
        <div
          role="status"
          className="border-info-border bg-info/10 flex min-w-0 flex-wrap items-start gap-3 rounded-md border px-4 py-3"
        >
          <ArrowUpCircleIcon className="text-info mt-0.5 size-4 shrink-0" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-info-fg text-sm font-medium">{t("graduationNeeded")}</p>
            <p className="text-muted-foreground mt-0.5 text-xs">
              {t("graduationNeededDescription", { environment })}
            </p>
          </div>
          {graduateNeeds === null ? (
            <Button size="sm" onClick={graduation.onOpen}>
              {t("graduate")}
            </Button>
          ) : (
            <div className="w-full">
              <PermissionNote permission={graduateNeeds} verb={t("graduating")} />
            </div>
          )}
        </div>
      )}

      <PanelGrid>
        <Panel
          title={t("cell")}
          icon={<DatabaseIcon className="size-4" />}
          span={6}
          loading={!substrate && status.state === "loading"}
          error={!substrate ? statusError : null}
          onRetry={onRetry}
          empty={
            !substrate && status.state === "ready"
              ? {
                  icon: <DatabaseIcon className="size-5" />,
                  title: t("noEnvironment", { environment }),
                }
              : null
          }
        >
          {substrate && (
            <DefinitionList
              items={[
                {
                  term: t("cellName"),
                  description: (
                    <span className="font-mono [overflow-wrap:anywhere]">{substrate.cell}</span>
                  ),
                },
                {
                  term: t("adoption"),
                  description:
                    substrate.authority === "files" ? (
                      <span className="text-muted-foreground">{t("filesCanon")}</span>
                    ) : read === null ? (
                      <span className="text-muted-foreground">
                        {status.state === "loading" ? t("checking") : t("adoptionUnknown")}
                      </span>
                    ) : substrate.adopted === true ? (
                      <span className="inline-flex items-center gap-1.5">
                        <StatusDot status="ok" />
                        {t("adopted", { store: substrate.adoptedStore || substrate.authority })}
                      </span>
                    ) : substrate.adopted === false ? (
                      <span className="inline-flex items-center gap-1.5">
                        <StatusDot status="warn" />
                        {t("notAdopted")}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">{t("adoptionUnknown")}</span>
                    ),
                },
                {
                  term: t("replicas"),
                  description: <span className="font-mono">{substrate.replicas}</span>,
                },
                {
                  term: t("image"),
                  description: substrate.image ? (
                    <span className="font-mono [overflow-wrap:anywhere]">{substrate.image}</span>
                  ) : (
                    <span className="text-muted-foreground">{t("builtImage")}</span>
                  ),
                },
              ]}
            />
          )}
        </Panel>

        <Panel
          title={t("resources")}
          icon={<BoxesIcon className="size-4" />}
          span={6}
          loading={!substrate && status.state === "loading"}
          error={!substrate ? statusError : null}
          onRetry={onRetry}
          empty={
            substrate && substrate.resources.length === 0
              ? {
                  icon: <BoxesIcon className="size-5" />,
                  title: t("nothingAttached"),
                  description: t("nothingAttachedDescription"),
                }
              : !substrate && status.state === "ready"
                ? {
                    icon: <BoxesIcon className="size-5" />,
                    title: t("noEnvironment", { environment }),
                  }
                : null
          }
        >
          {substrate && (
            <div className="flex min-w-0 flex-col gap-3">
              <p className="flex min-w-0 flex-wrap items-center gap-1 text-xs">
                <span className="text-muted-foreground">{t("persistence")}</span>
                {substrate.persistence.map((p) => (
                  <Badge key={p} variant="outline" className="font-mono">
                    {p}
                  </Badge>
                ))}
              </p>
              <ul className="divide-border min-w-0 divide-y">
                {substrate.resources.map((r) => {
                  const tone = resourceTone(r.state);
                  return (
                    <li
                      key={`${r.kind}:${r.name}:${r.environment}`}
                      className="flex min-w-0 flex-wrap items-start justify-between gap-2 py-2 first:pt-0 last:pb-0"
                    >
                      <div className="min-w-0">
                        <p className="font-mono text-sm">
                          {r.kind} <span className="text-muted-foreground">{r.name}</span>
                        </p>
                        {r.detail && (
                          <p className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
                            {r.detail}
                          </p>
                        )}
                      </div>
                      <span className="flex shrink-0 items-center gap-2">
                        {r.environment && (
                          <span className="text-muted-foreground font-mono text-xs">
                            {r.environment}
                          </span>
                        )}
                        <Badge
                          variant="outline"
                          className={cn("gap-1.5 font-mono", PILL_TONE[tone])}
                        >
                          {resourceInFlight(r.state) && <StatusDot status="pending" />}
                          {r.state}
                        </Badge>
                      </span>
                    </li>
                  );
                })}
              </ul>
              {statusError && <p className="text-muted-foreground text-xs">{t("fromLayer")}</p>}
            </div>
          )}
        </Panel>
      </PanelGrid>

      <Dialog open={graduation.open} onOpenChange={(open) => !open && graduation.onClose()}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{t("graduateTitle")}</DialogTitle>
            <DialogDescription>{t("graduateDescription")}</DialogDescription>
          </DialogHeader>
          <BrainSubstrateForm
            mode="graduate"
            idPrefix="graduate"
            draft={graduation.draft}
            onChange={graduation.onChange}
            locked={graduation.locked}
            serverError={graduation.error}
          />
          <DialogFooter>
            <Button variant="outline" onClick={graduation.onClose} disabled={graduation.submitting}>
              {t("cancel")}
            </Button>
            <Button onClick={graduation.onSubmit} disabled={graduation.submitting || blocked}>
              {graduation.submitting && <Loader2Icon className="size-4 animate-spin" />}
              {t("graduateSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
